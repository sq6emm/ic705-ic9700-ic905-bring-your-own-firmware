#!/usr/bin/env python3
"""Generic decoder for the Icom RZ/A1 firmware family (IC-705, IC-9700, IC-905, IC-7610, ...).

The main-CPU image of these radios is protected by the same two reversible stages:

  1. Autokey byte transform, keyed only by a 1-byte per-model constant KEY:
         plain[i] = stored[i] XOR ((prev_plain + position - KEY) & 255)
     Feedback is on the previous *plaintext* byte. Erased-flash padding is 0xFF; a
     known 0xFF run (anchor) fixes the stream exactly and round-trips bit-for-bit.
  2. Okumura LZSS (4096-byte ring, cursor 0xFEE, LSB-first flags, len=(hi&15)+3,
     index = lo | ((hi & 0xF0) << 4)), terminated by an explicit output length.

Everything that differs between models is recovered automatically:
  * KEY           -- from the 0xFF padding ramp: in an FF run, stored[pos] == (KEY - pos) & 255,
                     so consecutive stored bytes decrease by 1 and KEY = (stored + pos) & 255.
  * START         -- file offset of the main domain (header size), found by locating the
                     ARM reset-vector table (18 F0 9F E5 ...) in the decoded bootstrap.
  * STREAM offset -- small main header (4 or 8 bytes); found by probing which offset just
                     past the 64 KiB bootstrap LZSS-decodes to the ARM vector table.
  * OUTPUT length -- the header length word that yields the complete ARM image.

Verified (KEY / start / main version string):
  IC-705  0x19 / 0x64 / AVSX4075
  IC-9700 0x2b / 0x38 / AVSX3913
  IC-905  0x7f / 0x88 / AVSX4209

No firmware is included; supply your own .dat from Icom's official update.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

ARM_VECTORS = bytes.fromhex("18f09fe518f09fe5")   # two ARM 'ldr pc,[pc,#imm]' words
BOOTSTRAP = 0x10000                                # 64 KiB bootstrap before the main stream


def autokey_reverse(data, base, key, last=0xFF):
    """Recover plaintext backwards from a known final plaintext byte (0xFF anchor)."""
    out = bytearray(len(data)); p = last
    for i in range(len(data) - 1, -1, -1):
        out[i] = p; p = ((data[i] ^ p) - base - i + key) & 255
    return bytes(out), p


def autokey_decode(data, base, key, previous):
    out = bytearray()
    for i, c in enumerate(data):
        previous = c ^ ((previous + base + i - key) & 255); out.append(previous)
    return bytes(out), previous


def lzss_decompress(data, expected):
    if not 0 < expected <= 64 * 1024 * 1024:
        raise ValueError("unreasonable output length")
    ring = bytearray(4096); cursor = 0xFEE; pos = 0; flags = 0; out = bytearray()
    while len(out) < expected:
        if pos >= len(data):
            break
        flags >>= 1
        if not flags & 0x100:
            flags = data[pos] | 0xFF00; pos += 1
        if flags & 1:
            out.append(data[pos]); ring[cursor] = data[pos]
            cursor = (cursor + 1) & 4095; pos += 1
        else:
            lo, hi = data[pos], data[pos + 1]; pos += 2
            index = lo | ((hi & 0xF0) << 4); length = (hi & 0x0F) + 3
            for j in range(length):
                if len(out) >= expected:
                    break
                v = ring[(index + j) & 4095]
                out.append(v); ring[cursor] = v; cursor = (cursor + 1) & 4095
    return bytes(out), pos


def _lzss_head(data, pos, need=8):
    ring = bytearray(4096); cursor = 0xFEE; flags = 0; out = bytearray()
    try:
        while len(out) < need:
            flags >>= 1
            if not flags & 0x100:
                flags = data[pos] | 0xFF00; pos += 1
            if flags & 1:
                out.append(data[pos]); ring[cursor] = data[pos]
                cursor = (cursor + 1) & 4095; pos += 1
            else:
                lo, hi = data[pos], data[pos + 1]; pos += 2
                index = lo | ((hi & 0xF0) << 4); length = (hi & 0x0F) + 3
                for j in range(length):
                    v = ring[(index + j) & 4095]
                    out.append(v); ring[cursor] = v; cursor = (cursor + 1) & 4095
    except IndexError:
        pass
    return bytes(out[:need])


def derive_key(raw, search=0x8000):
    """Find a monotonically -1 run (0xFF padding under the autokey) and read KEY off it."""
    for s in range(0x20, min(search, len(raw) - 16)):
        run = raw[s:s + 16]
        if all((run[i] - 1) & 255 == run[i + 1] for i in range(15)):
            return (run[0] + s) & 255, s
    raise ValueError("no 0xFF padding ramp found; cannot derive KEY")


def find_start(raw, key):
    """Sweep the domain start; anchor on the nearest 0xFF run (bootstrap padding)."""
    for s in range(0x10, 0x1000):
        for window in (0x1000, BOOTSTRAP):
            if s + window > len(raw):
                continue
            boot, iv = autokey_reverse(raw[s:s + window], s, key)
            if boot[:8] == ARM_VECTORS:
                full, _ = autokey_decode(raw[s:s + BOOTSTRAP], s, key, iv)
                return s, iv, full
    raise ValueError("ARM bootstrap not found")


def _find_main(body):
    """Return (stream_offset, output_length) for the LZSS main image, or None."""
    for off in range(BOOTSTRAP, BOOTSTRAP + 0x10):
        if _lzss_head(body, off) != ARM_VECTORS:
            continue
        words = [struct.unpack_from("<I", body, w)[0] for w in range(BOOTSTRAP, off, 4)] or \
                [struct.unpack_from("<I", body, BOOTSTRAP)[0]]
        best = None
        for out_len in sorted({w for w in words if 0x40000 < w < 0x800000}, reverse=True):
            image, consumed = lzss_decompress(body[off:], out_len)
            if image[:8] == ARM_VECTORS and consumed <= len(body) - off:
                best = (off, out_len, image, consumed); break
        if best:
            return best
    return None


def arm_elf(image, base, entry):
    """Minimal ELF32/ARM executable wrapper, for analysis only."""
    offset = 0x1000 + (base & 0xFFF)
    strings = b"\0.text\0.shstrtab\0"; string_offset = offset + len(image)
    shoff = (string_offset + len(strings) + 3) & ~3
    ident = b"\x7fELF\x01\x01\x01" + bytes(9)
    header = struct.pack("<16sHHIIIIIHHHHHH", ident, 2, 40, 1, entry, 52, shoff,
                         0x05000000, 52, 32, 1, 40, 3, 2)
    program = struct.pack("<8I", 1, offset, base, base, len(image), len(image), 7, 4096)
    data = bytearray(header + program); data.extend(bytes(offset - len(data)))
    data.extend(image); data.extend(strings); data.extend(bytes(shoff - len(data)))
    data.extend(bytes(40))
    data.extend(struct.pack("<10I", 1, 1, 7, base, offset, len(image), 0, 0, 4, 0))
    data.extend(struct.pack("<10I", 7, 3, 0, 0, string_offset, len(strings), 0, 0, 1, 0))
    return bytes(data)


def decode(path, outdir=None, main_base=0x20005000):
    raw = Path(path).read_bytes()
    key, ramp = derive_key(raw)
    start, iv, bootstrap = find_start(raw, key)
    span = min(len(raw) - start, 0xF00000)
    body, _ = autokey_decode(raw[start:start + span], start, key, iv)
    found = _find_main(body)
    if not found:
        raise ValueError("main LZSS stream not located")
    stream_off, out_len, main, consumed = found
    version = None
    m = re.search(rb"AVSX\d+ V[0-9.]+-[A-Z0-9]+", main)
    if m:
        version = m.group().decode()
    result = {
        "file": Path(path).name, "sha256": hashlib.sha256(raw).hexdigest(),
        "key": key, "domain_start": start, "initial_feedback": iv, "padding_ramp_at": ramp,
        "main_stream_offset": stream_off, "main_decompressed": out_len,
        "main_consumed": consumed, "main_entry": struct.unpack_from("<I", main, 0x20)[0],
        "main_load_address": main_base, "version_string": version,
        "bootstrap_sha256": hashlib.sha256(bootstrap).hexdigest(),
        "main_sha256": hashlib.sha256(main).hexdigest(),
    }
    if outdir:
        d = Path(outdir); d.mkdir(parents=True, exist_ok=True)
        (d / "bootstrap.bin").write_bytes(bootstrap)
        (d / "main.bin").write_bytes(main)
        (d / "main.elf").write_bytes(arm_elf(main, main_base, result["main_entry"]))
        (d / "manifest.json").write_text(json.dumps(result, indent=2) + "\n")
    return result


def main():
    ap = argparse.ArgumentParser(description="Decode an Icom RZ/A1 firmware .dat (main CPU image).")
    ap.add_argument("source")
    ap.add_argument("outdir", nargs="?", default=None)
    args = ap.parse_args()
    print(json.dumps(decode(args.source, args.outdir), indent=2))


if __name__ == "__main__":
    main()
