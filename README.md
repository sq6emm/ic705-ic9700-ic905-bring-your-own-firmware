# Icom IC-705 / IC-9700 / IC-905 firmware tools

Tools and documentation for the Icom **RZ/A1 firmware family** update container (`.dat`)
— verified on the **IC-705**, **IC-9700** and **IC-905** — including the byte codec used
for the main CPU, DSP, aux and WLAN images.

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

## Firmware family

The same codec is used across Icom's RZ/A1 radios; only **two per-model parameters**
differ, and both are recovered automatically:

- **`KEY`** — read off the `0xFF` padding ramp (in an `0xFF` run, `stored[pos] == (KEY - pos) & 255`).
- **`START`** — the main-domain offset (header size), found by locating the ARM reset
  vector table in the decoded bootstrap.

| Radio | KEY | START | Main version | Verified |
|---|---|---|---|---|
| IC-705  | `0x19` | `0x64` | `AVSX4075 V4.52-E00` | ✅ ARM image + vectors + strings |
| IC-9700 | `0x2b` | `0x38` | `AVSX3913 V5.43-E00` | ✅ ARM image + vectors + strings |
| IC-905  | `0x7f` | `0x88` | `AVSX4209 V3.92-E00` | ✅ ARM image + vectors + strings |

`icom_rza1_decode.py` needs no configuration — it derives the KEY, the start, the main
stream offset and the output length, and decodes the main-CPU image for any radio in
this family.

## Tools

| File | Purpose |
|---|---|
| `icom_rza1_decode.py` | **Family decoder.** Auto-derives the per-model KEY and domain start, then recovers the main-CPU image + bootstrap for any supported radio (IC-705, IC-9700, …). No per-model configuration. |
| `ic_rza1_repack.py` | **Re-packer.** LZSS encoder + autokey encoder; re-seals a (modified) main-CPU image into a device-valid IC-705 `.dat` (valid MD5, same length). |
| `ic705_decode.py` | Full IC-705 decode: verify integrity, reverse the autokey, LZSS-decompress every image (main, aux, DSP, WLAN), emit components + minimal ELFs + a manifest. |
| `ic705.py` | Container-level decompose / rebuild / verify, with MD5 + size-law re-seal (lossless round-trip). |
| `ic705_codec.py` | Independent Okumura-LZSS decoder / diagnostics (used for cross-checking). |
| `extract_streams.py` | Container parser (component map). |
| `ghidra_scripts/` | Ghidra helper to export recovered functions. |

## Usage

```sh
# Family decoder (auto KEY + start) — works on IC-705, IC-9700, ...:
python3 icom_rza1_decode.py 9700E150.dat out/9700E150

# Full IC-705 decode (all images), on a firmware file you supply yourself:
python3 ic705_decode.py 705E140.dat out/705E140 --verify-independent

# Container inspection / lossless rebuild:
python3 ic705.py verify   705E140.dat
python3 ic705.py decompress 705E140.dat out/
python3 ic705.py rebuild    out/705E140 705E140.rebuilt.dat
```

`ic705_decode.py` writes `main.elf`, `aux*.elf`, component `.bin` images and
`manifest.json` into the output directory, with load addresses set for analysis.

## Re-packing

`ic_rza1_repack.py` closes the loop: a decompressed main-CPU image is re-compressed and
re-sealed into a container the format accepts.

```python
from ic_rza1_repack import repack

raw = open("705E140.dat", "rb").read()
image = open("out/705E140/main.bin", "rb").read()   # patch this image as you like
new_dat = repack(raw, image)                        # valid MD5, other components preserved
open("705E140.patched.dat", "wb").write(new_dat)
```

The LZSS encoder (lazy matching) round-trips bit-for-bit through the decoder and runs ~2%
smaller than Icom's own, so a re-packed image fits even the tightly-packed models; the
autokey transform is an exact inverse. An **identity re-pack** (same image) produces a
valid-MD5 container that re-decodes to the identical image — only the main slot
(re-compressed) and the 16-byte MD5 digest change; everything else stays byte-identical.

Re-pack status:

| Radio | MD5 region | Identity re-pack |
|---|---|---|
| IC-705  | `0x9c0000` | ✅ valid container, re-decodes identical |
| IC-9700 | `0x800000` | ✅ valid container, re-decodes identical |
| IC-905  | — | ⏳ different (multi-component) integrity; decode works, re-seal is a follow-up |

(Whether a **real radio** accepts a modified image is still unverified — the bootloader
may check things not visible in the container.)

## Tests

```sh
python3 -m pytest tests/ -q
```

Tests use only synthetic data and small embedded byte constants — no firmware needed.

## Scope and limits

- Verified against all IC-705 E releases, the IC-705 J release, IC-9700 and IC-905.
- Decode and container re-seal are solved and round-trip-verified.
- The family decoder recovers the main-CPU image on any supported radio; the full
  per-image decoder (`ic705_decode.py`) is IC-705-specific for aux/DSP/WLAN.
- Re-packing a *modified* image additionally needs an LZSS encoder; whether a real
  radio accepts a rebuilt image is **unverified** (the bootloader may perform checks
  not visible in the container).
- Requires Python 3; `ic705.py` uses `numpy`.

## Project status

- **Decode:** verified on all IC-705 E/J releases, IC-9700 and IC-905 (main-CPU image recovered).
- **Re-pack (device-valid container):** IC-705 and IC-9700 ✅; IC-905 pending its (different,
  multi-component) integrity re-seal.
- **Next:** emulation-based validation on the shared RZ/A1 SoC (see Acknowledgements), so a rebuilt
  image can be checked in an emulator **before** any real-device flashing is ever attempted.

## Acknowledgements

Emulation of these radios builds on the reverse-engineering of the closely related Icom
**IC-7300**, which uses the same Renesas RZ/A1 SoC family:

- [jeskko/ic7300-homebrew](https://github.com/jeskko/ic7300-homebrew) (MIT) — a custom
  **QEMU machine for the RZ/A1**, a homebrew SDK, and container/LZSS tooling. We use its
  QEMU/SoC work as the reference base for emulating IC-705 / IC-9700 / IC-905 firmware.
- [zarthur/ic-7300_firmware](https://github.com/zarthur/ic-7300_firmware) (MIT) — IC-7300
  firmware reverse-engineering research.

These are prior art for the **shared silicon** only; this project targets the
IC-705 / IC-9700 / IC-905 and does not redistribute their work.

## Legal

For interoperability and research. **Do not** commit or redistribute Icom firmware,
manuals or schematics. Firmware images decoded with these tools are Icom's
copyrighted work; keep them local.
