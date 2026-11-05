# Undocumented CI-V commands (Icom RZ/A1 family)

Commands implemented in the firmware of the **IC-705 / IC-9700 / IC-905** (and **IC-7610**) that are **not
listed in Icom's public CI-V reference guides**. Recovered by reverse-engineering the firmware; the IC-705
findings were confirmed on a real radio over USB CI-V.

> ⚠️ **WARNING.** Some of these are **factory service** commands that **reset the radio, enter calibration/
> alignment mode, or write raw calibration data**. Sending them can **wipe your settings** or **misalign the
> radio** (hard to undo without test equipment). *Read* commands are safe; *writes/actions* are not. This
> documents **what exists** — it is not a recommendation to use it. Use only on radios you own, with backups.

## Frame recap
`FE FE <to> <from> <cmd> [<sub>] [<data>] FD`. Default radio addresses: 705=`0xA4`, 9700=`0xA2`, 905=`0xAC`,
7610=`0x98`; controller = `0xE0`. Reply `…FB`=OK, `…FA`=NG (rejected).

---

## 1. Factory service block — command `0x1A`, sub `0xD0`–`0xEF` (all radios)
A hidden factory interface tucked under the standard `0x1A` (settings) command. Subs `0xD0`–`0xEF` are not in
the public reference. Four groups:

### 🟢 Safe reads (no password)
| Cmd | What it reads |
|---|---|
| `1A D5` | Region/destination code (which market the radio is set for → which bands it may transmit) |
| `1A D6` | An internal measured value (raw ADC reading used for calibration) |
| `1A D8` | An internal status byte |
| `1A E4` | Minor service value |

### 🔧 Password-protected adjustment / self-test
Each requires a **model-specific ASCII password** sent as the command data:

| Radio | Enter alignment mode | DV self-test | Secondary adjust |
|---|---|---|---|
| IC-705 | `SX-4075 Adjust` | `SX-4075 DV Check` | `EX-4255 Adjust` |
| IC-9700 | `SX-3913 Adjust` | `SX-3913 DV Check` | — |
| IC-7610 | `SX-3864 Adjust` | — | `SX-3864 MAC Write` ← writes the Ethernet MAC |
| IC-905 | `SX-4209 Adjust` | `SX-4209 DV Check` | `EX-4327 Adjust` |

- **`1A E0` + "`<model> Adjust`"** — enters the factory **alignment/adjustment mode** (the on-screen service
  menu technicians use to calibrate the radio). ✅ *Confirmed on a real IC-705: this unlocked the service menu
  on the display.*
- **`1A D2` + "`<model> DV Check`"** — runs a D-STAR/DV self-test.
- **`1A D7` + "`<secondary> Adjust`"** — adjusts a secondary subsystem (only when the radio is in a safe state).

### 🔴 UNPROTECTED — reset / writes (NO password!)
| Cmd | What it does |
|---|---|
| **`1A D0`** | **FACTORY RESET — clears all settings to defaults. No password.** (This wiped our test radio during blind probing.) |
| **`1A E1`** | Another reset/clear variant |
| `1A D1 / E2 / E5 / E7` | Write service flags / a calibration value / a raw data block / a config bit (some trigger subsystem re-init) |
| `1A D3 / E3` | Service diagnostic / conditional band operation |

---

## 2. Other undocumented commands (per radio)

### IC-705
- **`0x24`** — implemented (2-byte sub-command); a real radio replies to it; purpose not in the public reference.
- **`0x28`** — a set-only command outside the standard list.

### IC-9700
- **`0x28 00`** — *band/preset recall*: reads an index 0–8 and recalls a band/preset. (On radios with a voice
  keyer, `0x28` means voice-TX instead — here it does something else.)
- **`0x1C 02` / `0x1C 03`** — present, while the standard `0x1C 01` (read TX frequency) is absent.
- **`0x0D`** — a memory/bank operation.

### IC-7610 (dual receiver)
- **`0x2A`** — three sub-commands: `00` reads/writes 13-byte VFO/frequency records (a ring of 4, memo-pad-like);
  `01` extended antenna/tuner control; `02` has a further sub-level.

### IC-905 (microwave, 144 MHz – 10 GHz)
- **Extended frequency encoding.** Standard CI-V frequency is 5 BCD bytes (can't represent above ~4.3 GHz).
  The IC-905 uses a **variable length: 6 BCD bytes (12 digits) for bands ≥ 1.389 GHz, 5 bytes below**, with
  64-bit internal arithmetic and per-band offsets. This is how it carries 2.4 / 5.6 / 10 GHz frequencies over
  CI-V (commands `0x00/0x02/0x03/0x05/0x25`).

---

## How these were found
Decompile the firmware main image → locate the CI-V command dispatcher (a table of command → handler pointers)
→ read each handler. The IC-705 results were verified live over USB CI-V, including unlocking the on-screen
service menu with the recovered `SX-4075 Adjust` password.

## Responsible use
This describes what the firmware implements. The service/reset/adjust commands are dangerous (data loss,
misalignment). Don't rely on undocumented commands for anything important — Icom may change them without notice.
No firmware is included or required to read this document.
