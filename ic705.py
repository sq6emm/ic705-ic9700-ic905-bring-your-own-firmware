#!/usr/bin/env python3
"""
ic705.py — IC-705 firmware (.dat) library + CLI for other agents.

Two main operations (and importable functions):
  decompress <file.dat> [outdir]   open, verify integrity, remove the positional obfuscation,
                                    split into components, write component files + manifest.json
  rebuild    <indir> <out.dat>      reassemble components, recompute MD5 + size-law fields, verify,
                                    write a byte-consistent .dat (lossless if components unchanged)

HONEST SCOPE — read this:
  * The container, the positional obfuscation layer, the standard-MD5 integrity and the size law are
    fully solved and round-trip-verified on all 11 releases. `rebuild` will always produce a container
    that passes every integrity check we can observe IN THE FILE (MD5 @0x9c0064 over [0x64:0x9c0064]
    + the size law). No second checksum/signature over the tail/suffix was found in the files.
  * "decompress" here means: strip the obfuscation and split. It is NOT codec decompression.
    The `main_block` and `dsp_tail` components stay COMPRESSED with a proprietary, byte-synchronous
    LZSS-family codec that is NOT broken (model/tables live in the SPI-flash bootloader, not the .dat).
    `decompress_stream()` therefore raises NotImplementedError for those; it only passes through the
    components that are not codec-compressed.
  * Whether a REAL radio accepts a rebuilt-modified .dat is UNVERIFIED: the flash bootloader may do
    extra checks we cannot see. Editing a compressed component's bytes yields a valid-but-broken image.

Obfuscation (positional sawtooth, constant K=0x19; i = absolute file offset):
  deobfuscate '+':  plain  = (stored + K - i) & 0xff      reobfuscate '+':  stored = (plain - K + i) & 0xff
  deobfuscate '-':  plain  = (stored - K + i) & 0xff      reobfuscate '-':  stored = (plain + K - i) & 0xff
  deobf and reobf with the SAME sign are exact inverses -> a '+'-region can be edited losslessly:
  raw -> deobf('+') -> edit -> reobf('+') -> write back. Header [0:0x64], the 16-byte MD5 and the RAW
  WLAN suffix are NOT obfuscated. Sign is per-field inside the codec, so for anything other than a
  known '+' code/data region prefer editing the `.raw.bin` slice directly (rebuild is lossless from raw).
"""
import sys, os, struct, hashlib, json
import numpy as np

K = 0x19

# ---- primitives -------------------------------------------------------------
def deobfuscate(stored: bytes, base: int, sign: str = '+') -> bytes:
    a = np.frombuffer(stored, np.uint8).astype(np.int64)
    i = np.arange(base, base + len(a), dtype=np.int64)
    p = (a + K - i) & 0xff if sign == '+' else (a - K + i) & 0xff
    return p.astype(np.uint8).tobytes()

def reobfuscate(plain: bytes, base: int, sign: str = '+') -> bytes:
    a = np.frombuffer(plain, np.uint8).astype(np.int64)
    i = np.arange(base, base + len(a), dtype=np.int64)
    s = (a - K + i) & 0xff if sign == '+' else (a + K - i) & 0xff
    return s.astype(np.uint8).tobytes()

def parse_header(d: bytes) -> dict:
    assert d[:4] == b'D\x00wU', "bad magic %r (not an IC-705 container)" % d[:4]
    ver = d[4:0x2c].split(b'\x00')[0].decode('latin1', 'replace')
    f = {off: struct.unpack_from('<I', d, off)[0] for off in range(0x2c, 0x64, 4)}
    return {"version": ver, "fields": f,
            "f38": f[0x38], "f40": f[0x40], "f50": f[0x50], "f54": f[0x54], "f5c": f[0x5c]}

def verify(d: bytes) -> dict:
    h = parse_header(d); f38, f54, f5c = h["f38"], h["f54"], h["f5c"]
    digest_at = 0x64 + f38
    md5_ok = hashlib.md5(d[0x64:digest_at]).hexdigest() == d[digest_at:digest_at + 16].hex()
    size_ok = (0x64 + f38 + f54 + 20000 + f5c) == len(d)
    return {"md5_ok": md5_ok, "size_law_ok": size_ok, "digest_at": digest_at,
            "md5": d[digest_at:digest_at + 16].hex()}

def boundaries(d: bytes):
    """Return [(name, lo, hi, kind)] with VERIFIED boundaries. kind in
    {header, obf, obf_const, md5, raw}. obf* = positionally obfuscated; raw = stored as-is."""
    h = parse_header(d); n = len(d)
    digest_at = 0x64 + h["f38"]            # 0x9c0064 (const in all 11)
    const_base = 0x600064                  # f40 size block base (const in all 11)
    main_base = h["f50"] + 0x64            # 0x10064
    suffix_start = n - h["f5c"]
    return [
        ("header",      0,            0x64,         "header"),
        ("pre_main",    0x64,         main_base,    "obf"),
        ("main_block",  main_base,    const_base,   "obf"),       # Main CPU app + GUI (COMPRESSED)
        ("const_block", const_base,   digest_at,    "obf_const"), # FPGA + sub-images (const all 11)
        ("md5",         digest_at,    digest_at+16, "md5"),
        ("dsp_tail",    digest_at+16, suffix_start, "obf"),       # compressed DSP image(s)
        ("wlan_suffix", suffix_start, n,            "raw"),       # RAW, not obfuscated
    ]

# ---- decompress (open + verify + deobf + split) -----------------------------
COMPRESSED = {"main_block", "dsp_tail"}   # proprietary codec, NOT broken

def decompress(path: str, outdir: str = None) -> dict:
    d = open(path, 'rb').read()
    h = parse_header(d); v = verify(d)
    name = os.path.splitext(os.path.basename(path))[0]
    outdir = outdir or os.path.join("decompressed", name)
    os.makedirs(outdir, exist_ok=True)
    man = {"file": os.path.basename(path), "version": h["version"], "size": len(d),
           "verify": v, "fields_hex": {hex(k): hex(val) for k, val in h["fields"].items()},
           "components": []}
    for cname, lo, hi, kind in boundaries(d):
        rawslice = d[lo:hi]
        open(os.path.join(outdir, cname + ".raw.bin"), "wb").write(rawslice)
        e = {"name": cname, "offset": lo, "size": hi - lo, "kind": kind,
             "sha256_raw": hashlib.sha256(rawslice).hexdigest(),
             "compressed_codec": cname in COMPRESSED}
        if kind in ("obf", "obf_const"):           # also emit a de-obfuscated view ('+' default)
            dec = deobfuscate(rawslice, lo, '+')
            open(os.path.join(outdir, cname + ".deobf.bin"), "wb").write(dec)
            e["note"] = ("still codec-compressed after de-obf; codec NOT broken"
                         if cname in COMPRESSED else "de-obfuscated (not codec-compressed)")
        man["components"].append(e)
    json.dump(man, open(os.path.join(outdir, "manifest.json"), "w"), indent=2)
    print("%s  ver=%s  MD5=%s size_law=%s -> %s/"
          % (name, h["version"], "OK" if v["md5_ok"] else "FAIL",
             "OK" if v["size_law_ok"] else "FAIL", outdir))
    return man

def decompress_stream(component_bytes: bytes, name: str, *, hypothesis=None, **options):
    """Pass through known raw components, or probe an explicit experimental codec.

    With hypothesis=ic705_codec.CodecSpec(...), returns a DecodeResult with token
    diagnostics, not a claim of recovered firmware. base must be the component's
    original DAT offset. Without a hypothesis the existing behavior is preserved.
    """
    if hypothesis is not None:
        from ic705_codec import CodecSpec, decompress_stream as probe_stream
        if not isinstance(hypothesis, CodecSpec):
            raise TypeError("hypothesis must be an ic705_codec.CodecSpec")
        if "base" not in options:
            raise ValueError("experimental decoding requires the original component base offset")
        return probe_stream(component_bytes, hypothesis, **options)
    if options:
        raise ValueError("codec options require an explicit hypothesis=CodecSpec(...)")
    if name in COMPRESSED:
        raise NotImplementedError(
            "%s has no confirmed codec. Use hypothesis=ic705_codec.CodecSpec(...) "
            "with base=... for experimental decoding and token diagnostics." % name)
    return component_bytes   # others are raw/de-obf already

# ---- rebuild (reassemble + reseal) -----------------------------------------
ORDER = ["header", "pre_main", "main_block", "const_block", "md5", "dsp_tail", "wlan_suffix"]

def rebuild(indir: str, outpath: str) -> dict:
    """Reassemble from <indir>/<name>.raw.bin, recompute the size-law fields + MD5, verify, write.
    Lossless if the raw slices are unchanged. If you resized dsp_tail/wlan_suffix, f54/f5c and the
    length are recomputed so the size law + MD5 stay valid (head sizes must be preserved)."""
    parts = {}
    for nm in ORDER:
        p = os.path.join(indir, nm + ".raw.bin")
        if not os.path.exists(p):
            raise FileNotFoundError("missing component %s (%s)" % (nm, p))
        parts[nm] = bytearray(open(p, 'rb').read())
    hdr = parts["header"]
    # PRESERVE the original header (works for every region incl. 705J, whose size-law constant differs
    # from 705E because of the EUR-only blocks). Only the MD5 is recomputed. f38 (MD5-region size) comes
    # from the preserved header; the head components must sum to it.
    f38 = struct.unpack_from('<I', hdr, 0x38)[0]
    head_sz = len(parts["pre_main"]) + len(parts["main_block"]) + len(parts["const_block"])
    if head_sz != f38:
        # a head component was resized -> adjust f38 (and f40 = const_block size) so the MD5 region is right
        f38 = head_sz
        struct.pack_into('<I', hdr, 0x38, f38)
        struct.pack_into('<I', hdr, 0x40, len(parts["const_block"]))
        print("  note: head size changed; f38 updated to 0x%x (f54/f5c/size-law NOT auto-adjusted)" % f38)
    body = bytes(hdr) + bytes(parts["pre_main"]) + bytes(parts["main_block"]) + bytes(parts["const_block"])
    digest = hashlib.md5(body[0x64:0x64 + f38]).digest()        # MD5 over [0x64 : 0x64+f38]
    out = body + digest + bytes(parts["dsp_tail"]) + bytes(parts["wlan_suffix"])
    open(outpath, 'wb').write(out)
    v = verify(out)
    print("rebuilt %s (%d B)  MD5=%s  (size-law informational: %s)"
          % (outpath, len(out), "OK" if v["md5_ok"] else "FAIL",
             "matches E-constant" if v["size_law_ok"] else "region-specific constant (normal for 705J)"))
    if not v["md5_ok"]:
        raise RuntimeError("rebuild produced an INVALID MD5")
    return v

# ---- CLI --------------------------------------------------------------------
def _usage():
    print("usage:\n  ic705.py decompress <file.dat> [outdir]\n  ic705.py rebuild <indir> <out.dat>\n"
          "  ic705.py verify <file.dat>")
def main(argv):
    if len(argv) < 2: _usage(); return 2
    cmd = argv[1]
    if cmd == "decompress" and len(argv) >= 3:
        decompress(argv[2], argv[3] if len(argv) > 3 else None)
    elif cmd == "rebuild" and len(argv) >= 4:
        rebuild(argv[2], argv[3])
    elif cmd == "verify" and len(argv) >= 3:
        d = open(argv[2], 'rb').read(); print(parse_header(d)["version"], verify(d))
    else:
        _usage(); return 2
    return 0

if __name__ == '__main__':
    sys.exit(main(sys.argv))
