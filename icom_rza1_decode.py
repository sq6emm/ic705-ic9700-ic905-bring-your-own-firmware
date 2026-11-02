#!/usr/bin/env python3
"""Generic decoder for the Icom RZ/A1 firmware family (IC-705, IC-9700, ...).

The main-CPU image of these radios is protected by the same two reversible stages:

  1. Autokey byte transform, keyed only by a 1-byte per-model constant KEY:
         plain[i] = stored[i] XOR ((prev_plain + absolute_offset - KEY) & 255)
     Feedback is on the previous *plaintext* byte. Erased-flash padding is 0xFF; a
     known 0xFF run (anchor) fixes the stream exactly.
  2. Okumura LZSS (4096-byte ring, cursor 0xFEE, LSB-first flags, len=(hi&15)+3,
     index = lo | ((hi & 0xF0) << 4)), terminated by an explicit output length.

Only two per-model parameters differ, and both are recovered automatically:
  * KEY  -- from the 0xFF padding ramp: in an FF run, stored[pos] == (KEY - pos) & 255,
            so consecutive stored bytes decrease by 1 and KEY = (stored + pos) & 255.
  * START -- the file offset where the main MD5 domain begins (header size), found by
             searching for the ARM reset-vector table (18 F0 9F E5 ...) in the decoded
             bootstrap.

Verified: IC-705 (KEY 0x19, start 0x64) and IC-9700 (KEY 0x2b, start 0x38).
No firmware is included; supply your own .dat from Icom's official update.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct

ARM_VECTORS = bytes.fromhex("18f09fe518f09fe5")   # two ARM 'ldr pc,[pc,#0xf0]' words
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
            raise ValueError("truncated LZSS input")
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


def derive_key(raw, search=0x4000):
    """Find a monotonically -1 run (0xFF padding under the autokey) and read KEY off it."""
    for s in range(0x40, min(search, len(raw) - 16)):
        run = raw[s:s + 16]
        if all((run[i] - 1) & 255 == run[i + 1] for i in range(15)):
            return (run[0] + s) & 255, s
    raise ValueError("no 0xFF padding ramp found; cannot derive KEY")


def find_start(raw, key, candidates=(0x38, 0x40, 0x64, 0x14, 0x1C, 0x20, 0x2C, 0x34)):
    for s in candidates:
        if s + BOOTSTRAP > len(raw):
            continue
        boot, iv = autokey_reverse(raw[s:s + BOOTSTRAP], s, key)
        if boot[:8] == ARM_VECTORS:
            return s, iv, boot
    raise ValueError("ARM bootstrap not found at any candidate start offset")


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
    # One continuous autokey stream over the main MD5 domain; decode enough to cover main.
    span = min(len(raw) - start, 0xC00000)
    body, _ = autokey_decode(raw[start:start + span], start, key, iv)
    comp, out_len = struct.unpack_from("<II", body, BOOTSTRAP)
    stream = body[BOOTSTRAP + 8:BOOTSTRAP + 8 + comp]
    main, consumed = lzss_decompress(stream, out_len)
    if main[:8] != ARM_VECTORS:
        raise ValueError("decoded main image does not start with the ARM vector table")
    version = None
    m = re.search(rb"AVSX\d+ V[0-9.]+-[A-Z0-9]+", main)
    if m:
        version = m.group().decode()
    result = {
        "file": Path(path).name,
        "sha256": hashlib.sha256(raw).hexdigest(),
        "key": key, "domain_start": start, "initial_feedback": iv,
        "padding_ramp_at": ramp,
        "main_compressed": comp, "main_decompressed": out_len,
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
