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

How that decoded body is stored in the radio's on-chip QSPI flash (IS25LP128, XIP at
`0x18000000`), and how to read or write it directly with a CH341A programmer:
[`IC705_FLASH_LAYOUT.md`](IC705_FLASH_LAYOUT.md).

The IC-9700 equivalent (S25FL127S, two A/B firmware slots plus a slot marker, graphics
pack chained behind the main image, direct CH341A access):
[`IC9700_FLASH_LAYOUT.md`](IC9700_FLASH_LAYOUT.md).

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
| `ic_rza1_repack.py` | **Re-packer.** LZSS encoder + autokey encoder; re-seals a (modified) main-CPU image into a device-valid IC-705 / IC-9700 `.dat` (valid MD5, same length; blobs chained behind the main stream move with it, fixed-offset content stays put). |
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
| IC-9700 | `0x800000` | ✅ valid container, re-decodes identical; the graphics pack chained behind the main stream is moved along with it ([layout](IC9700_FLASH_LAYOUT.md#inside-a-slot)) |
| IC-905  | — | ❌ **not possible** — firmware is RSA-2048 **signed** (see below); decode works, re-seal needs Icom's private key |

(On the IC-705 / IC-9700 the radio's SD-card updater verifies a plain MD5 per component, so a correctly
re-sealed container passes; the IC-905 verifies an RSA signature instead — see below.)

## Patches — build a tested firmware from your own official file

No modified firmware is distributed. A patch in [`patches/`](patches/) holds only the
changes to the main-CPU image (and, for builds with a custom bootloader, to the 64 KiB
bootstrap at the start of the container — optional `"bootstrap"` section): our own code
and data as bytes, plus references of the form *copy N bytes from address X of the
official image / official bootstrap*. You download the official
`.dat` from Icom yourself and build the result locally:

**Easiest: use the web patcher** — https://sq6emm.github.io/ic705-ic9700-ic905-bring-your-own-firmware/ —
it runs entirely in your browser (the file never leaves your computer) and offers exactly one
patch per official file: the latest release. Or on the command line:

```sh
python3 icom_patch.py patches/9700E150_trv16c.json 9700E150.dat 9700E150_trv16c.dat
```

`icom_patch.py` checks the MD5 of your input file, decodes it, applies the patch,
re-packs it (`ic_rza1_repack.repack(raw, image, new_bootstrap=...)`; a replacement
bootstrap must keep the stock bootloader tag at 0x4f00, otherwise it is refused), and writes the output **only if its MD5 equals the MD5 of the build that
was tested**. You get a byte-identical copy of that build or nothing.

| Patch | Official input (MD5) | Output (MD5) |
|---|---|---|
| `9700E150_trv16c.json` (IC-9700 TRV 1.6c, includes the custom A/B bootloader) | `9700E150.dat` `6f04fd2248ba39254ed01b30b52ce36a` | `1a9d78b5a6295b8217d7cbe26c02d728` |

IC-705 E/J releases will be added after hardware verification.

Flashing modified firmware is at your own risk and may void the warranty. Transmitting
is your responsibility under your licence and national band plan.

## IC-905 is RSA-signed — not re-packable

Unlike the IC-705 / IC-9700 (and IC-7610), whose container integrity is a plain **MD5** per component
(an unkeyed checksum, so a rebuilt container can be re-sealed), the **IC-905 firmware is digitally signed.**
Its on-radio SD-card updater (`FUN_2005efe8`) verifies, for each component, an **RSA-2048 signature over a
SHA-256 digest** (mbedTLS `rsa_pkcs1_verify`, algorithm id 6, 32-byte hash, 256-byte signature), using a
public key embedded in the firmware image:

```
RSA-2048, e = 0x010001 (65537), hash = SHA-256
modulus (256 bytes, hex) =
  C07C320D9889A5DAA3766904E5C8DF929EBF4AB3219BE2D30E90B246F01171AE
  BF7DC0B657C1ED43718F9EEF1D09F562D210FF4BAAA485875260C5907DE6A488
  B3EB60D2E227E00821D8E6783D55B27E089C5D0D49A4052EA376B7C812A7C2D88
  897525BDA4048656D47A0FB3FF05DC7B96B3BA2A9D4B4BFD8DEF32E137657D325
  920EAD843F1E8AE93EFAF8DB3236A0082E867E5E0291D31FF5799401BB466D929
  70761ED6290C0A54AA70E1E55DE460A66A940497D21E89ED40679C63A6CDAF64D
  95335F7A498306A81393AD299FCF135558C987B6241A23225C065DFDAE8B72601
  D3990CEFE0253F89FA28318906ACE9C94377FF8A35678FD7A31C8F9BCF5
```

The 256-byte field after each component is this signature, **not** an MD5 — which is why a generic MD5
re-seal cannot produce a valid IC-905 container. Re-signing a modified image requires Icom's **private**
key (not derivable from the public key), so re-packing a *modified* IC-905 image is **cryptographically
infeasible**. Decoding (reading the main-CPU image) still works.

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
- **Re-pack (device-valid container):** IC-705 and IC-9700 ✅; **IC-905 is not re-packable** — its firmware
  is RSA-2048 + SHA-256 **signed**, so a modified image cannot be re-sealed without Icom's private key.
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
