# IC-9700 QSPI flash — on-chip layout and direct access

How the decoded IC-9700 firmware is stored in the radio's serial flash, how the
bootloader chooses between the two firmware slots, and how to read or write the flash
directly with a cheap programmer. Companion to
[`IC705_FLASH_LAYOUT.md`](IC705_FLASH_LAYOUT.md); the `.dat` container itself is
covered in [`IC705_DAT_FORMAT.md`](IC705_DAT_FORMAT.md) and the README (IC-9700:
KEY `0x2b`, domain start `0x38`, MD5 over `0x800000` bytes).

> **No firmware is included.** This file documents format and method only.

## Flash device

| Property | Value |
|---|---|
| Part | Spansion / Cypress **S25FL127S** (`S25FL127SABMFI10x`, top marking `FL127SIF10`) |
| Package | SOIC-8, **208 mil** (wide body) |
| Reference | **IC181** on the MAIN unit, next to the CPU IC101 (R7S721001, RZ/A1H) |
| Type | SPI NOR, QSPI boot |
| Size | 128 Mbit = **16 MiB** |
| Voltage | **3.3 V** (2.7–3.6 V, I/O not 5 V tolerant) |
| JEDEC RDID | `01 20 18` |
| Erase | 64 KiB sectors (flashrom definition `S25FL127S-64kB`) |

The flash pins (`SFL_SS`, `SFL_CK`, `SFL_D0..D3`) run only to the CPU; there is no
service connector or test point for them. The two GT24C1024 EEPROMs next to it (IC191,
IC192) hold settings/calibration and are not part of the firmware. IC331 is the same
flash type but belongs to the DSP/CPLD side, not to the main CPU.

The RZ/A1H executes in place from this flash, memory-mapped at XIP base
**`0x18000000`** (flash offset `N` is read at `0x18000000 + N`).

## On-flash layout — A/B slots

The IC-9700 keeps **two complete copies** of the firmware and a slot marker:

| Region | Flash offset | Size | Content |
|---|---:|---:|---|
| Bootstrap + loader | `0x000000` | `0x10000` | `== decoded_body[0x000000 : 0x010000]` |
| **Slot A** | `0x010000` | `0x7f0000` | `== decoded_body[0x010000 : 0x800000]` |
| **Slot B** | `0x800000` | `0x7f0000` | `== decoded_body[0x010000 : 0x800000]` |
| **Slot marker** | `0xff0000` | 16 B | `SX3913 V0.10-000` → boot slot B, anything else → slot A |

Each slot is the decoded `.dat` body (after the 64 KiB bootstrap) written **verbatim**:
no extra wrapper, no checksum, no re-encoding.

```python
from icom_rza1_decode import derive_key, find_start, autokey_decode
key, _ = derive_key(raw); start, iv, _ = find_start(raw, key)
body, _ = autokey_decode(raw[start:start + 0x800000], start, key, iv)
slot_image = body[0x10000:0x800000]          # what lands in slot A or B
```

**Slot selection:** the loader (`bootstrap[0x4000:]`, runs at `0x20004000`) only
compares the 16 marker bytes. It does not check the selected slot's contents, read
any key or GPIO, or look at the reset cause. **There is no automatic fallback:** if the
selected slot does not boot, the radio stays dark.

**SD-card update:** the updater in the main app writes the new firmware into the
**inactive** slot, then sets or erases the marker so that slot becomes active. The
previous firmware stays intact in the other slot.

**Switching slots by hand** (with direct flash access) is therefore a one-sector edit:

```python
d = bytearray(open("dump.bin", "rb").read())
d[0xff0000:0xff0010] = b"\xff" * 16                 # boot slot A
# d[0xff0000:0xff0010] = b"SX3913 V0.10-000"        # boot slot B
open("new_flash.bin", "wb").write(d)
```

## Inside a slot

All offsets are relative to the slot start (`0x18010000` for A, `0x18800000` for B).

| Slot offset | Content | Located by |
|---:|---|---|
| `+0x0` | `csz` (LE32): main-image LZSS length, padded to a multiple of 4 | loader |
| `+0x4` | `osz` (LE32): decompressed main-image length | loader |
| `+0x8` | **Main CPU image**, Okumura LZSS → RAM `0x20005000`, entry `0x20005060`; FF padding to `csz` | loader |
| `+0x8 + csz` | **Graphics pack header** `csz2`, `osz2` (LE32 each) | main app, **relative to `csz`** |
| `+0x10 + csz` | **Graphics pack**, LZSS (4096-byte ring, start `0xfee`) → SDRAM `0x0c000000` | main app, **relative to `csz`** |
| … | `0xFF` up to the next fixed blob | — |
| `+0x3e0000`, `+0x3e4000` | data blobs | fixed address |
| `+0x3f0000` … `+0x793000` | component image | fixed address (`0x18400000` / `0x18bf0000`) |
| `+0x7e0000`, `+0x7ec000` | data blobs | fixed address (`0x187f0000` / `0x18fe0000`, `0x187fc000`) |

Stock E1.50 values: `csz = 0x2145F4`, `osz = 0x405334`; graphics pack `csz2 = 0x80C2C`,
`osz2 = 0x2675BC`; the pack ends at slot `+0x295230`.

**Important for re-packing:** the main app finds the graphics pack at
`slot + csz + 8`, i.e. **directly behind the main stream**, not at a fixed address. If a
re-compressed main image changes `csz`, the graphics pack (header and data) has to
move with it, or the app reads `0xFFFFFFFF` as the pack length and hangs before the
display comes up. Everything after the FF gap is addressed absolutely and must stay
where it is. [`ic_rza1_repack.py`](ic_rza1_repack.py) handles this: it detects blobs
chained after the main stream, keeps the stock 4-byte alignment and moves them along.
Because the pack moves, a modified main image can grow until the pack would reach
`+0x3e0000`, about 1.3 MiB more than stock.

A quick check of a slot image before writing it, mirroring what the app does:

```python
import struct
c = struct.unpack_from("<I", slot, 0)[0]
csz2, osz2 = struct.unpack_from("<II", slot, c + 8)
assert osz2 != 0xFFFFFFFF and 0 < csz2 < 0x7f0000    # pack header must be found at csz + 8
```

## Reading / writing the flash directly

A **CH341A** (`ch341a_spi`) with **flashrom** reads and writes the S25FL127S. Use
**3.3 V** logic levels; the classic 5 V CH341A boards exceed the part's I/O rating.

As on the IC-705, **in-circuit reads do not work**: with the chip soldered, a SOIC
clip gets RDID `00 00 00`, because the unpowered CPU and the 3.3 V rail load the bus.
**Desolder IC181** and read it in a socket. Several flashrom definitions share the
`01 20 18` ID, so name the chip explicitly:

```sh
flashrom -p ch341a_spi                                   # probe: lists the matches
flashrom -p ch341a_spi -c "S25FL127S-64kB" -r dump1.bin
flashrom -p ch341a_spi -c "S25FL127S-64kB" -r dump2.bin
cmp dump1.bin dump2.bin                                  # must be identical
flashrom -p ch341a_spi -c "S25FL127S-64kB" -w new_flash.bin   # rewrites only changed sectors, verifies
```

On a stock radio the status register reads `0x00` (no block protection), so flashrom
writes data without touching the status or configuration registers. Leave the
configuration register (quad-mode bit) alone; the radio reads the flash in quad mode.
Keep the original dump as a 1:1 restore point, and put the chip back in its original
orientation (dot = pin 1).

For Linux host setup (the `spi_ch341` kernel driver, USB permissions) see
[`IC705_FLASH_LAYOUT.md`](IC705_FLASH_LAYOUT.md#linux-host-notes).
