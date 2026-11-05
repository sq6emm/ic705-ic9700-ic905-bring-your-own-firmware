# Undocumented CI-V commands (IC-705 / IC-9700 / IC-905)

Commands implemented in the firmware of the **IC-705 / IC-9700 / IC-905** that are **not listed in Icom's public
CI-V reference guides**. Recovered by reverse-engineering the firmware; the IC-705 findings were confirmed on a
real radio over USB CI-V. (The IC-7610 has an analogous service block — see its own repository.)

> ⚠️ **WARNING.** Some of these are **factory service** commands that **reset the radio, enter calibration/
> alignment mode, or write raw calibration data**. Sending them can **wipe your settings** or **misalign the
> radio** (hard to undo without test equipment). *Read* commands are safe; *writes/actions* are not. This
> documents **what exists** — it is not a recommendation to use it. Use only on radios you own, with backups.

## Frame recap
`FE FE <to> <from> <cmd> [<sub>] [<data>] FD`. Default radio addresses: 705=`0xA4`, 9700=`0xA2`, 905=`0xAC`;
controller = `0xE0`. Reply `…FB`=OK, `…FA`=NG (rejected).

---

## 1. Factory service block — command `0x1A`, sub `0xD0`–`0xEF`
A hidden factory interface tucked under the standard `0x1A` (settings) command. Subs `0xD0`–`0xEF` are not in
the public reference. **Every sub below was read from each radio's own dispatch table and handler code** — this
is a per-radio verification, not a family assumption. The IC-705 was additionally confirmed on real hardware.

> Each radio implements a slightly different subset. The *meaning* of a given sub is the same across models, but
> which subs exist and the exact passwords differ. The password (a model-specific ASCII string) is sent as the
> command's data bytes; a wrong or missing password returns NG (`FA`) and does nothing.

### What each sub-command does (plain language)

**🟢 Reads — safe, no password**
- **`1A D5`** — *Read the region/destination code.* Tells which market the radio is built for, which decides
  which bands it may transmit. (On a real IC-705 this returned the region code directly.)
- **`1A D6`** — *Read an internal calibration measurement.* A raw sensor/ADC reading (e.g. a level), scaled 0–255.
- **`1A D8`** *(705 only)* — *Read one internal status byte.*
- **`1A D9` / `1A DA`** *(905 only)* — *Read a microwave-band calibration record* (a 13-byte record returned as
  BCD). These exist because the IC-905 covers the GHz bands and keeps extra per-band calibration.
- **`1A E4`** — *Read a minor service value.*

**🔧 Adjustment / self-test — password required**
- **`1A E0` + "`<model> Adjust`"** — *Enter the factory alignment/adjustment mode* — the on-screen service menu
  a technician uses to calibrate the radio. ✅ **Confirmed on a real IC-705: sending this unlocked the service
  menu on the display.** Internally it flips the radio into "service state 1" and clears working state.
- **`1A D2` + "`<model> DV Check`"** — *Run the D-STAR/DV self-test.* (Not present on the HF-only IC-7610.)
- **`1A D7` + "`<model> Adjust`"** — *Adjust a secondary subsystem*, only while the radio is in a safe state.
  **On the IC-7610 this slot is different — see that radio's own document — it is the Ethernet MAC-address write.**

**🔴 Reset / writes / actions — NO password**
- **`1A D0`** — **FACTORY RESET. Clears all settings to defaults. No password.** Internally: enters "service
  state 3" and zeroes the radio's working data structures. *(This is the command that wiped our test radio
  during blind probing — present and unprotected on all four models.)*
- **`1A E1`** — *A second reset/clear variant* ("service state 2"); only guard is that one data byte must be 0.
- **`1A D1`** *(705/7610)* — *Set a service/test-mode flag* (0/1, stored next to the region code); needs an
  internal permission bit already set.
- **`1A E2`** — *Select a service sub-mode* — a value of 6/7/8 switches the radio into one of three alignment/
  test states (specific calibration steps).
- **`1A E3`** — *Select the target of the next service operation* (writes a band/channel index into the service
  state, to steer a following adjust).
- **`1A E5`** — *Write a service data value* addressed by a byte in the command (a targeted calibration write).
- **`1A E7`** *(705/905)* — *Toggle one audio/DSP configuration option* (0/1); if it changes, the radio
  re-initializes several audio/DSP blocks.
- **`1A D3`** *(705/7610)* — *Run a service diagnostic and send the result back as a CI-V frame* (only in a safe
  state).

### Per-radio map (verified from each firmware)
| | IC-705 | IC-9700 | IC-905 |
|---|---|---|---|
| Subs present | D0 D1 D2 D3 D5 D6 D7 D8 · E0–E7 | D0 D2 D5 D6 D7 · E0–E7 | D0 D2 D5 D6 D7 D9 DA · E0–E7 E9 |
| Enter alignment (`E0`) | `SX-4075 Adjust` | `SX-3913 Adjust` | `SX-4209 Adjust` |
| DV self-test (`D2`) | `SX-4075 DV Check` | `SX-3913 DV Check` | `SX-4209 DV Check` |
| Secondary adjust (`D7`) | `EX-4255 Adjust` | *(model string)* | `EX-4327 Adjust` |
| Unprotected reset | `D0`, `E1` | `D0`, `E1` | `D0`, `E1` |

The IC-9700 block is the IC-705 block minus `D1/D3/D8`. The IC-905 adds the microwave calibration reads
`D9/DA` (and a reserved `E9`).

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

### IC-905 (microwave, 144 MHz – 10 GHz)
- **Extended frequency encoding.** Standard CI-V frequency is 5 BCD bytes (can't represent above ~4.3 GHz).
  The IC-905 uses a **variable length: 6 BCD bytes (12 digits) for bands ≥ 1.389 GHz, 5 bytes below**, with
  64-bit internal arithmetic and per-band offsets. This is how it carries 2.4 / 5.6 / 10 GHz frequencies over
  CI-V (commands `0x00/0x02/0x03/0x05/0x25`).

---

## How these were found
Decompile the firmware main image → locate the CI-V command dispatcher (a table of command → handler pointers)
→ read each handler. The service block was verified **per radio from each model's own tables and handlers**
(the reset, alignment-entry and self-test handlers were read individually for the IC-705, IC-9700 and IC-905, so
the per-model differences above are observed, not assumed). The IC-705 results were additionally verified live
over USB CI-V, including unlocking the on-screen service menu with the recovered `SX-4075 Adjust` password.

## Responsible use
This describes what the firmware implements. The service/reset/adjust commands are dangerous (data loss,
misalignment). Don't rely on undocumented commands for anything important — Icom may change them without notice.
No firmware is included or required to read this document.
