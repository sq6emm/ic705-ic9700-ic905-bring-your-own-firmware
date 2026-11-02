#!/usr/bin/env python3
"""Experimental token decoder for IC-705 reverse engineering.

This implements explicit LZSS hypotheses, NOT a discovered IC-705 codec.
A structurally decodable stream or an expected length is not proof of correctness.
Only comparison with an independently known output digest sets verified=True.
"""
from __future__ import annotations
import argparse
from dataclasses import asdict, dataclass, field
import hashlib
import json
from pathlib import Path

MODELS = ('identity', 'sub-input', 'add-input', 'xor-input', 'sub-output',
          'add-output', 'xor-output', 'sub-output-word', 'add-output-word',
          'sub-output-page', 'add-output-page', 'complement')
LAYOUTS = ('okumura', 'be12-low4', 'le12-high4', 'be12-high4',
           'le13-high3', 'le11-high5', 'off16-len8-le', 'len8-off16-le')

@dataclass(frozen=True)
class Transform:
    model: str = 'identity'
    key: int = 0x19
    output_bias: int = 0

    def __post_init__(self):
        if self.model not in MODELS:
            raise ValueError(f'Unknown transform: {self.model}')

    def apply(self, byte: int, absolute_input: int, output_position: int) -> int:
        m = self.model
        if m == 'identity':
            return byte
        if m == 'complement':
            return byte ^ 255
        position = absolute_input
        if 'output' in m:
            shift = 2 if m.endswith('-word') else 8 if m.endswith('-page') else 0
            position = (output_position >> shift) + self.output_bias
        if m.startswith('sub-'):
            return (byte + self.key - position) & 255
        if m.startswith('add-'):
            return (byte - self.key + position) & 255
        return byte ^ ((position - self.key) & 255)

@dataclass(frozen=True)
class CodecSpec:
    flags: Transform = field(default_factory=Transform)
    literals: Transform = field(default_factory=Transform)
    matches: Transform = field(default_factory=Transform)
    layout: str = 'okumura'
    reference: str = 'absolute'
    window_size: int = 4096
    initial_cursor: int | None = None
    initial_fill: int = 0
    minimum_length: int = 3
    flag_bits: int = 8
    flag_order: str = 'lsb'
    flag_endian: str = 'little'
    literal_bit: int = 1
    strict_initial_dictionary: bool = False

    def __post_init__(self):
        if self.layout not in LAYOUTS or self.reference not in ('absolute', 'relative', 'relative-plus-one'):
            raise ValueError('Unknown token layout or reference mode')
        w = self.window_size
        if w < 32 or w > 65536 or w & (w - 1):
            raise ValueError('window_size must be a power of two from 32 to 65536')
        if self.initial_cursor is not None and not 0 <= self.initial_cursor < w:
            raise ValueError('initial_cursor outside dictionary')
        if not 0 <= self.initial_fill <= 255 or not 1 <= self.minimum_length <= 256:
            raise ValueError('Invalid dictionary fill or minimum length')
        if self.flag_bits not in (8, 16, 32) or self.flag_order not in ('lsb', 'msb'):
            raise ValueError('Invalid flag width/order')
        if self.flag_endian not in ('little', 'big') or self.literal_bit not in (0, 1):
            raise ValueError('Invalid flag endian/polarity')

@dataclass
class DecodeResult:
    data: bytes
    status: str
    error: str | None
    input_start: int
    consumed_bytes: int
    remaining_bytes: int
    literals: int
    matches: int
    initial_dictionary_reads: int
    tokens: int
    trace: list[dict]
    trace_truncated: bool
    expected_size: int | None
    expected_sha256: str | None
    verified: bool = False

    def report(self) -> dict:
        return {k: v for k, v in vars(self).items() if k != 'data'} | {
            'output_size': len(self.data), 'output_sha256': hashlib.sha256(self.data).hexdigest(),
            'output_prefix_hex': self.data[:64].hex(),
            'interpretation': 'matches supplied output digest' if self.verified else 'unverified codec hypothesis',
        }


def _match_fields(values: list[int], layout: str) -> tuple[int, int]:
    a, b = values[:2]
    if layout == 'okumura':
        return a | ((b & 240) << 4), b & 15
    if layout == 'be12-low4':
        return (a << 4) | (b >> 4), b & 15
    if layout == 'le12-high4':
        return a | ((b & 15) << 8), b >> 4
    if layout == 'be12-high4':
        return ((a & 15) << 8) | b, a >> 4
    if layout == 'le13-high3':
        return (a | (b << 8)) & 8191, b >> 5
    if layout == 'le11-high5':
        return (a | (b << 8)) & 2047, b >> 3
    if layout == 'off16-len8-le':
        return a | (b << 8), values[2]
    return b | (values[2] << 8), a


def decompress_stream(data: bytes, spec: CodecSpec | None = None, *, base: int = 0,
                      start: int = 0, max_output: int = 65536,
                      expected_size: int | None = None, expected_sha256: str | None = None,
                      initial_dictionary: bytes | None = None, trace_limit: int = 64) -> DecodeResult:
    """Decode one explicit hypothesis, retaining diagnostics even on malformed input.

    base is the original DAT offset of data[0]; start skips a hypothesized subheader.
    No length or checksum is guessed from IC-705 payload bytes. An output limit is
    a resource limit, not an end marker. Initial ring reads are counted separately.
    """
    spec = spec or CodecSpec()
    if not isinstance(data, bytes):
        raise TypeError('data must be bytes')
    if base < 0 or not 0 <= start <= len(data) or not 1 <= max_output <= 32 * 1024 * 1024:
        raise ValueError('Invalid base, start or output limit')
    if expected_size is not None and expected_size < 0:
        raise ValueError('expected_size must be nonnegative')
    if not 0 <= trace_limit <= 100000:
        raise ValueError('Invalid trace_limit')
    if expected_sha256 is not None:
        expected_sha256 = expected_sha256.lower()
        if len(expected_sha256) != 64 or any(c not in '0123456789abcdef' for c in expected_sha256):
            raise ValueError('expected_sha256 must be 64 hexadecimal characters')
    w = spec.window_size
    if initial_dictionary is not None and len(initial_dictionary) != w:
        raise ValueError('initial_dictionary must contain exactly window_size bytes')
    ring = bytearray(initial_dictionary if initial_dictionary is not None else bytes([spec.initial_fill]) * w)
    written = bytearray(w)
    cursor = spec.initial_cursor if spec.initial_cursor is not None else w - 18
    out = bytearray()
    pos = start
    flags = bits_left = literal_count = match_count = initial_reads = token_count = 0
    group_start = start
    trace = []
    status, error = 'input_exhausted', None
    limit = min(max_output, expected_size) if expected_size is not None else max_output
    while len(out) < limit:
        if pos == len(data):
            break
        if not bits_left:
            width = spec.flag_bits // 8
            if len(data) - pos < width:
                status, error = 'truncated_flags', 'Incomplete flag word'
                break
            group_start = pos
            normalized = bytes(spec.flags.apply(data[pos+j], base+pos+j, len(out)) for j in range(width))
            flags = int.from_bytes(normalized, spec.flag_endian)
            pos += width
            bits_left = spec.flag_bits
            if pos == len(data):
                status, error = 'truncated_token', 'Flag word has no token'
                break
        flag_index = spec.flag_bits - bits_left
        bit_index = flag_index if spec.flag_order == 'lsb' else bits_left - 1
        literal = (flags >> bit_index) & 1 == spec.literal_bit
        bits_left -= 1
        token_start, output_start = pos, len(out)
        event = dict(kind='literal' if literal else 'match', input_offset=base+pos,
                     output_offset=output_start, flag_offset=base+group_start,
                     flags=flags, flag_bit_index=bit_index, dictionary_cursor=cursor)
        if literal:
            value = spec.literals.apply(data[pos], base+pos, len(out))
            event.update(stored_hex=data[pos:pos+1].hex(), value=value)
            pos += 1
            out.append(value)
            ring[cursor] = value
            written[cursor] = 1
            cursor = (cursor + 1) % w
            literal_count += 1
        else:
            width = 3 if spec.layout in ('off16-len8-le', 'len8-off16-le') else 2
            if len(data) - pos < width:
                status, error = 'truncated_token', f'Incomplete {width}-byte match'
                event['error'] = error
            else:
                raw = data[pos:pos+width]
                values = [spec.matches.apply(v, base+pos+j, output_start) for j, v in enumerate(raw)]
                offset, length = _match_fields(values, spec.layout)
                length += spec.minimum_length
                pos += width
                event.update(stored_hex=raw.hex(), normalized_hex=bytes(values).hex(),
                             offset=offset, length=length)
                distance = offset + int(spec.reference == 'relative-plus-one')
                if spec.reference == 'absolute' and offset >= w:
                    error = 'Absolute match index outside dictionary'
                elif spec.reference != 'absolute' and not 1 <= distance <= min(len(out), w):
                    error = 'Relative match points outside available output history'
                if error:
                    status = 'invalid_reference'
                    event['error'] = error
                else:
                    copied = 0
                    for j in range(min(length, limit - len(out))):
                        index = (offset+j) % w if spec.reference == 'absolute' else (cursor-distance) % w
                        if not written[index]:
                            if spec.strict_initial_dictionary and initial_dictionary is None:
                                status, error = 'initial_dictionary_reference', 'Match uses unknown initial dictionary'
                                event['error'] = error
                                break
                            initial_reads += 1
                        value = ring[index]
                        out.append(value)
                        ring[cursor] = value
                        written[cursor] = 1
                        cursor = (cursor+1) % w
                        copied += 1
                    event.update(copied=copied, stopped_mid_match=copied < length)
                    match_count += 1
        event.update(input_bytes=pos-token_start, output_bytes=len(out)-output_start)
        token_count += 1
        if len(trace) < trace_limit:
            trace.append(event)
        if error:
            break
    if not error:
        if expected_size is not None and len(out) == expected_size:
            status = 'expected_size_reached'
        elif len(out) == max_output:
            status = 'output_limit'
        elif expected_size is not None:
            status, error = 'truncated_output', 'Input ended before expected output size'
    result = DecodeResult(bytes(out), status, error, start, pos-start, len(data)-pos,
                          literal_count, match_count, initial_reads, token_count, trace,
                          token_count > len(trace), expected_size, expected_sha256)
    if expected_sha256 is not None and not error and status in ('expected_size_reached', 'input_exhausted'):
        result.verified = hashlib.sha256(result.data).hexdigest() == expected_sha256
        if not result.verified:
            result.error = 'Output does not match supplied SHA-256'
            result.status = 'digest_mismatch'
    return result


def _number(text: str) -> int:
    return int(text, 0)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('input', type=Path)
    p.add_argument('--component', default='main_block')
    p.add_argument('--raw', action='store_true', help='Input is a raw component, not a DAT')
    p.add_argument('--base', type=_number, help='Required for --raw: original DAT offset')
    p.add_argument('--start', type=_number, default=0)
    p.add_argument('--max-output', type=_number, default=65536)
    p.add_argument('--expected-size', type=_number)
    p.add_argument('--expected-sha256')
    p.add_argument('--flags', choices=MODELS, default='identity')
    p.add_argument('--literals', choices=MODELS, default='identity')
    p.add_argument('--matches', choices=MODELS, default='identity')
    p.add_argument('--key', type=_number, default=25)
    p.add_argument('--output-bias', type=_number, default=0)
    p.add_argument('--layout', choices=LAYOUTS, default='okumura')
    p.add_argument('--reference', choices=('absolute', 'relative', 'relative-plus-one'), default='absolute')
    p.add_argument('--window-size', type=_number, default=4096)
    p.add_argument('--initial-cursor', type=_number)
    p.add_argument('--initial-fill', type=_number, default=0)
    p.add_argument('--flag-bits', type=int, choices=(8, 16, 32), default=8)
    p.add_argument('--flag-order', choices=('lsb', 'msb'), default='lsb')
    p.add_argument('--flag-endian', choices=('little', 'big'), default='little')
    p.add_argument('--literal-bit', type=int, choices=(0, 1), default=1)
    p.add_argument('--minimum-length', type=int, default=3)
    p.add_argument('--strict-initial-dictionary', action='store_true')
    p.add_argument('--trace-limit', type=int, default=64)
    p.add_argument('--output', type=Path, help='Write UNVERIFIED candidate bytes; refuses overwrite')
    p.add_argument('--report', type=Path, help='JSON diagnostics; refuses overwrite')
    a = p.parse_args()
    try:
        for dest in (a.output, a.report):
            if dest and dest.exists():
                raise ValueError(f'Refusing to overwrite {dest}')
        if a.output and a.report and a.output.absolute() == a.report.absolute():
            raise ValueError('Output and report must be different files')
        data = a.input.read_bytes()
        if a.raw:
            if a.base is None:
                raise ValueError('--raw requires --base')
            base = a.base
        else:
            import ic705
            if len(data) < 0x64 or data[:4] != b'D\x00wU':
                raise ValueError('Input is not an IC-705 DAT; use --raw with --base for a component')
            checks = ic705.verify(data)
            if not checks['md5_ok'] or not checks['size_law_ok']:
                raise ValueError('DAT integrity verification failed')
            regions = {name: (lo, hi) for name, lo, hi, _ in ic705.boundaries(data)}
            if a.component not in regions:
                raise ValueError('Unknown component')
            base, end = regions[a.component]
            if not 0 <= base <= end <= len(data):
                raise ValueError('Component boundaries are outside the input file')
            data = data[base:end]
        transform = lambda model: Transform(model, a.key, a.output_bias)
        spec = CodecSpec(transform(a.flags), transform(a.literals), transform(a.matches),
                         a.layout, a.reference, a.window_size, a.initial_cursor, a.initial_fill,
                         a.minimum_length, a.flag_bits, a.flag_order, a.flag_endian,
                         a.literal_bit, a.strict_initial_dictionary)
        result = decompress_stream(data, spec, base=base, start=a.start, max_output=a.max_output,
                                   expected_size=a.expected_size, expected_sha256=a.expected_sha256,
                                   trace_limit=a.trace_limit)
        report = result.report() | {'source': str(a.input), 'component': a.component,
                                    'base': base, 'codec_spec': asdict(spec)}
        encoded = json.dumps(report, indent=2) + '\n'
        if a.report:
            with a.report.open('x') as target:
                target.write(encoded)
        if a.output:
            with a.output.open('xb') as target:
                target.write(result.data)
        print(json.dumps({k: report[k] for k in ('status','output_size','consumed_bytes',
                                                'initial_dictionary_reads','verified','error')}))
        if not a.report:
            print(encoded)
        return 0 if not result.error else 1
    except (OSError, ValueError, TypeError) as exc:
        p.error(str(exc))
        return 2

if __name__ == '__main__':
    raise SystemExit(main())
