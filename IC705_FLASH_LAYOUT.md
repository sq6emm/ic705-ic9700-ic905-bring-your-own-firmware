# IC-705 QSPI flash — on-chip layout and direct access

How the decoded IC-705 firmware is stored in the radio's serial flash, and how to
read or write that flash directly with a cheap programmer. This complements
[`IC705_DAT_FORMAT.md`](IC705_DAT_FORMAT.md), which covers the `.dat` update
container; here we document the **on-flash** form that the updater actually writes.

> **No firmware is included.** This file documents format and method only.

## Flash device

| Property | Value |
|---|---|
| Part | ISSI **IS25LP128** (SOIC-8) |
| Type | SPI NOR, QSPI boot |
| Size | 128 Mbit = **16 MiB** |
| Voltage | **3.3 V** (2.3–3.6 V) |
| JEDEC RDID | `9D 60 18` |
| Sector erase | 4 KiB |

The Renesas **RZ/A1H** boots execute-in-place from this flash, memory-mapped at
XIP base **`0x18000000`** (so flash offset `N` is read as address `0x18000000 + N`).

## On-flash layout

The updater writes the **decoded `body`** of the `.dat` to flash **verbatim** — it is
*not* the `.dat` container, and *not* re-encoded. Concretely, for the first
`0x600000` bytes the flash equals the decoded body byte-for-byte:

```
flash[0x000000 : 0x600000]  ==  decoded_body[0x000000 : 0x600000]
```

where `decoded_body` is recovered from any `.dat` as:

```python
# from ic705_decode.py
bootstrap, iv = feedback_reverse(raw[0x64:0x10064], 0x64)
body, _       = feedback_decode(raw[0x64:0x9c0064], 0x64, iv)   # 0x9c0000 bytes
```

Inside that region:

| Element | Flash offset | Notes |
|---|---:|---|
| Bootstrap (ARM reset vectors `18 f0 9f e5 …`) | `0x000000` | 64 KiB; runs XIP from `0x18000000` |
| Loader (copied to RAM `0x20004000`) | `0x004000` | `bootstrap[0x4000:0x49bc]` |
| Main image header: `compressed_size` (LE32) | `0x010000` | stored LZSS length |
| Main image header: `output_size` (LE32) | `0x010004` | decompressed length |
| **Main CPU image — Okumura LZSS** | `0x010008` | `compressed_size` bytes |

The bootstrap copies/decompresses the main image to RAM **`0x20005000`** (entry
`0x20005060`) and runs it there. Decode the main image with the same LZSS as the
`.dat`:

```python
main = flash[0x10008 : 0x10008 + compressed_size]
image, consumed = lzss_decode(main, output_size)   # -> ARMv7-A at 0x20005000
```

For reference, E140 has `compressed_size = 0x1F9DA8`, `output_size = 0x376000`.

**Beyond `0x600000`** the flash holds the DSP, WLAN and auxiliary images plus
on-screen resources, laid out at **different offsets than the `.dat` body's trailing
region** (the `.dat`'s trailing `0x3c0000` "constant-region span", field `0x40` in
[`IC705_DAT_FORMAT.md`](IC705_DAT_FORMAT.md)). Those images are independent of the
main CPU image; a main-CPU-only change touches only `flash[0x10000 : 0x010008 +
compressed_size]`, well under `0x600000`, and leaves the bootstrap (`< 0x10000`) and
everything from `0x600000` on untouched.

## Reading / writing the flash directly

A **CH341A** USB programmer (`ch341a_spi`) with **flashrom** reads and writes the
IS25LP128. Use a **3.3 V** CH341A — the classic 5 V boards exceed the part's I/O
rating.

In-circuit reads on this board are unreliable: with the chip still soldered, the SoC
and the 3.3 V rail load the SPI bus (RDID reads back `0x00`). **Desolder the chip** and
read it in a socket / clip off-board. Confirm a clean, stable `9D 60 18` before
trusting any transfer, and **read twice — require byte-identical dumps** — before
writing anything.

```sh
# probe
flashrom -p ch341a_spi
# read twice and compare
flashrom -p ch341a_spi -c IS25LP128 -r dump1.bin
flashrom -p ch341a_spi -c IS25LP128 -r dump2.bin
cmp dump1.bin dump2.bin                      # must be identical
# write (flashrom erases/writes only changed 4 KiB sectors, then verifies)
flashrom -p ch341a_spi -c IS25LP128 -w new_flash.bin
```

Keep the original dump as a 1:1 restore point.

### Linux host notes

The mainline **`spi_ch341`** kernel driver claims the CH341 and the raw USB node is
root-only, so flashrom's libusb backend fails with `errno 13`. One-time fixes:

```sh
echo 'blacklist spi-ch341' | sudo tee /etc/modprobe.d/blacklist-spi-ch341.conf
echo 'SUBSYSTEM=="usb", ATTRS{idVendor}=="1a86", ATTRS{idProduct}=="5512", MODE="0666"' \
  | sudo tee /etc/udev/rules.d/99-ch341-flashrom.rules
sudo udevadm control --reload-rules
sudo modprobe -r spi_ch341      # then replug the programmer
```
