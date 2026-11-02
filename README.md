# IC-705 firmware tools

Tools and documentation for the Icom **IC-705** firmware update container (`.dat`),
including the byte codec used for the main CPU, DSP, aux and WLAN images.

> **No firmware is included.** Bring your own `.dat` from Icom's official firmware
> update. This repository contains only tooling and format documentation.

## Result

The IC-705 `.dat` is a multi-image container (main CPU, aux, DSP, WLAN) whose payload
is protected by two stacked, fully reversible transforms:

1. **Autokey byte transform** (per MD5 domain):

   ```
   plain[i] = stored[i] XOR ((prev_plain + absolute_offset - 0x19) & 255)
   ```

   The feedback is on the *previous plaintext* byte. Erased-flash padding is `0xFF`;
   a known `0xFF` anchor (end of the 64 KiB bootstrap, or the first LZSS flag byte of
   each tail domain) fixes the initial state, so the stream is recovered exactly and
   round-trips bit-for-bit.

2. **Okumura LZSS** — 4096-byte ring, cursor `0xFEE`, LSB-first flags
   (`1` = literal, `0` = 2-byte back-reference, `len = (hi & 15) + 3`,
   `index = lo | ((hi & 0xF0) << 4)`), terminated by an explicit output length.

Container integrity is an **unkeyed MD5** over `[0x64 : 0x64 + len@0x38]`; there is no
signature, so a rebuilt container can be re-sealed. Recovered images are standard
ARMv7-A (main, loader), Thumb / Cortex-M (aux) and a separate DSP image.

Full details, offsets and import addresses: [`IC705_DAT_FORMAT.md`](IC705_DAT_FORMAT.md).

## Tools

| File | Purpose |
|---|---|
| `ic705_decode.py` | Decode a `.dat`: verify integrity, reverse the autokey, LZSS-decompress each image, emit components + minimal ELFs + a manifest. |
| `ic705.py` | Container-level decompose / rebuild / verify, with MD5 + size-law re-seal (lossless round-trip). |
| `ic705_codec.py` | Independent Okumura-LZSS decoder / diagnostics (used for cross-checking). |
| `extract_streams.py` | Container parser (component map). |
| `ghidra_scripts/` | Ghidra helper to export recovered functions. |

## Usage

```sh
# Decode a firmware file you supply yourself:
python3 ic705_decode.py 705E140.dat out/705E140 --verify-independent

# Container inspection / lossless rebuild:
python3 ic705.py verify   705E140.dat
python3 ic705.py decompress 705E140.dat out/
python3 ic705.py rebuild    out/705E140 705E140.rebuilt.dat
```

`ic705_decode.py` writes `main.elf`, `aux*.elf`, component `.bin` images and
`manifest.json` into the output directory, with load addresses set for analysis.

## Tests

```sh
python3 -m pytest tests/ -q
```

Tests use only synthetic data and small embedded byte constants — no firmware needed.

## Scope and limits

- Verified against all IC-705 E releases and the J release.
- Decode and container re-seal are solved and round-trip-verified.
- Re-packing a *modified* image additionally needs an LZSS encoder; whether a real
  radio accepts a rebuilt image is **unverified** (the bootloader may perform checks
  not visible in the container).
- Requires Python 3; `ic705.py` uses `numpy`.

## Legal

For interoperability and research. **Do not** commit or redistribute Icom firmware,
manuals or schematics. Firmware images decoded with these tools are Icom's
copyrighted work; keep them local.
