# Frequency ranges & band plans — IC-705 / IC-9700

Per-region frequency coverage and TX band plans, reverse-engineered from the firmware. The
**destination/region** (set at the factory by an on-board diode matrix, then cached in config/EEPROM) selects
one of several **band plans**; each plan defines the segments on which the radio will transmit. RX coverage is
wideband and (mostly) region-independent.

> Band plans are factory data (also in Icom's service manuals). This documents **what the firmware supports**.
> Which bands you may actually transmit on is set by your radio's destination **and by your licence and local
> regulations** — always your responsibility.

HF middle bands are identical across all plans and omitted from the tables for clarity:
`30m 10.100–10.150 · 20m 14.000–14.350 · 17m 18.068–18.168 · 15m 21.000–21.450 · 12m 24.890–24.990 · 10m 28.000–29.700` (MHz).

---

## IC-705

- **RX:** 0.030 – 470.000 MHz, continuous general coverage.
- **TX:** HF / 50 / 144 / 430 MHz amateur bands, **plus per-region extras incl. 4 m (70 MHz)**.
- The firmware carries **5 band plans** (region → plan):

| Plan | 160 m | 80 m | 40 m | 6 m | **4 m** | 2 m | 70 cm | Typical destination |
|---|---|---|---|---|---|---|---|---|
| **0** | 1.800–2.000 | 3.500–4.000 | 7.000–7.300 | 50.0–54.0 | — | 144–146 | 430–440 | EUR (#13) |
| **1** | 1.800–2.000 | 3.500–4.000 | 7.000–7.300 | 50.0–54.0 | — | **144–148** | **430–450** | USA (#12) |
| **2** | 1.810–2.000 | 3.500–3.800 | 7.000–7.200 | 50.0–52.0 | **70.000–70.500** | 144–146 | 430–440 | UK / EU-with-4m |
| **3** | 1.810–1.850 | 3.500–3.800 | 7.000–7.200 | 50.0–52.0 | **70.150–70.250** | 144–146 | 430–440 | UK |
| **4** | 1.800–1.825 | 3.500–3.540 | 7.000–7.100 | 50.0–54.0 | — | 144–147 | 430–440 | restrictive (R1) |

(All plans also carry 60 m `5.255–5.405` except plan 4. Plans 0/1/4 list a `68.0–74.8` RX-coverage segment in
place of a 4 m TX band.)

**Possibilities:** the IC-705 firmware contains a **complete factory calibration set for 4 m (70 MHz)** — power,
ALC, SWR, APC and final-stage references for `70M`, the same depth as every other band — and **region plans 2
and 3 enable 4 m TX**. In other words the hardware is characterised for 70 MHz and the band is **region-locked,
not hardware-absent**; a radio set to a 4 m destination transmits 4 m with factory calibration. The destination
is a firmware/config value, so enabling a band does not require changing the hardware.

---

## IC-9700

- **RX / TX:** **2 m / 70 cm / 23 cm only** (three separate band modules). RX also covers nearby ranges (e.g.
  VHF air-band from ~108 MHz).
- The firmware carries **9 band plans** — **strictly tri-band; there are no hidden extra bands** (no 4 m, 6 m,
  1.25 m, 33 cm, 13 cm — confirmed absent):

| Plan | 2 m | 70 cm | 23 cm | Character |
|---|---|---|---|---|
| 0 | 144–146 | 430–440 | 1260–1300 | EUR (23 cm restricted) |
| **1, 7** | **144–148** | **430–450** | 1240–1300 | USA / ITU-R2 |
| **2, 4** | 144–146 | 430–440 | 1240–1300 | EUR general |
| 3 | 144–146 | 430–434 + 435–438 | 1240–1245 + 1270–1298 | segmented (e.g. FR) |
| 5 | 144–146 | 430–432 | 1260–1265 | very restrictive R1 |
| 6 | 144–146 | 430–440 | 1260–1300 | EUR variant |
| 8 | 144–147 | 430–440 | 1260–1270 | unusual |

**Possibilities:** region variants differ **only in band edges** (2 m 144–146 vs 144–148, 70 cm 430–440 vs
430–450, and the 23 cm limits). There is no additional band to unlock — the IC-9700 is a tri-band design.

---

## How it works (mechanism)
Region code (from the diode matrix, cached in config) → a lookup extracts a few bits → a **plan-map** byte table
→ plan index → a per-plan table of `{low, high}` frequency edges that gates TX. RX coverage is separate and
largely plan-independent. Full per-radio comparison (incl. IC-7610) and the IC-7610 wideband-TX variants are in
that model's own repository.

## Responsible use
Transmitting outside your licensed allocation is illegal in most jurisdictions. This is reverse-engineering
documentation of firmware behaviour; it is not advice to transmit anywhere. No firmware is included.
