# IC-705 DAT — recovered autokey and LZSS

Container format and byte codec for the Icom IC-705 firmware `.dat`, recovered and
round-trip-verified against all IC-705 E releases and the J release.

## Decode

```sh
python3 ic705_decode.py 705E140.dat decoded_firmware/705E140_verified --verify-independent
```

This performs actual deobfuscation and decompression. Older `ic705.py` split/view
commands predate the discovery; their plus/minus views are not the full transform.

## Header and integrity

The literal header is 0x64 bytes, magic `44 00 77 55`. Integers are LE32.

```
len = 0x64 + f38 + f44 + f4c + f54 + f5c + 66
66 = four 16-byte digest fields + two literal footer bytes
```

| Field | Confirmed role |
|---|---|
| 0x38 | Main stored/autokey-domain size: 0x9c0000 |
| 0x40 | Constant-region span: 0x3c0000; internal images require further mapping |
| 0x44 / 0x48 | Aux1 stored LZSS / decoded size: 939 / 896; omitted in J |
| 0x4c / 0x50 | Aux2 stored LZSS / decoded size: 18995 / 65536; omitted in J |
| 0x54 / 0x58 | DSP stored LZSS / decoded size; output 2097152 |
| 0x5c / 0x60 | WLAN stored LZSS / decoded image size; output 4188200 in E140 |

E140 layout:

| Element | Offset | Size |
|---|---:|---:|
| Main autokey domain | 0x64 | 0x9c0000 |
| MD5(main stored bytes) | 0x9c0064 | 16 |
| Aux1 | 0x9c0074 | 939 |
| Aux2 | 0x9c041f | 18995 |
| MD5(aux1+aux2 stored bytes) | 0x9c4e52 | 16 |
| DSP | 0x9c4e62 | 1452294 |
| MD5(DSP stored bytes) | 0xb27768 | 16 |
| WLAN | 0xb27778 | 583010 |
| MD5(WLAN stored bytes) | 0xbb5cda | 16 |
| Literal footer | 0xbb5cea | 2 (`40 75`) |

J omits aux and retains a zero digest placeholder. Header/digests/footer are
literal; each payload group is an independent autokey domain. Aux1+aux2 share one.

## Recovered byte transform

For each domain, counter starts at **0x4b** and increments modulo 256:

```c
plain = stored ^ (uint8_t)(previous_plain + counter);
previous_plain = plain;
counter++;
```

For the main body this is equivalently
`plain = stored XOR ((previous_plain + absolute_file_offset - 0x19) & 255)`.
Counter restarts at MD5-domain boundaries. Do not decrypt MD5 fields or carry
counter/state indiscriminately across the entire DAT.

Main initial feedback is recovered backwards using known FF padding at the end
of the first 64KiB bootstrap area:

```
previous_plain = ((stored XOR current_plain) - absolute_file_offset + 0x19) & 255
```

Tail first plaintext LZSS flag is FF in verified files, giving
`iv = ((stored[0] XOR 0xff) - 0x4b) & 255`. Full input consumption, exact output
lengths, independent decoders and matching auxiliary copies validate this inference.
The original encoder's IV-selection rule remains unknown.

Padding plaintext is **FF**, not zero. Old additive ramps were a special case
of the feedback cipher. The actual algorithm is also present in recovered
Thumb stub instructions at 0x200002d4..0x200002e8.

## LZSS

Ordinary Okumura style, confirmed in the actual IC-705 ARM boot loader:

- Zero-initialized 4096-byte ring, initial cursor 0xfee.
- Flag bytes LSB-first; 1 literal, 0 two-byte match.
- Match offset `lo | ((hi & 0xf0) << 4)`, absolute ring index.
- Match length `(hi & 15)+3`, overlap supported.
- Termination by explicit output length, not an end marker.

The original loader may read ahead beyond the last emitted byte. Main stored
streams retain up to a few FF bytes beyond safe-decoder consumption, recorded
in manifests. Tail streams consume their full stored lengths.

## Main structure and import addresses

| Item | Mapping |
|---|---|
| Bootstrap | DAT 0x64..0x10064 maps to flash 0x18000000 |
| Loader copy | Bootstrap +0x4000, length 0x9bc, RAM 0x20004000 |
| Loader entry / LZSS routine | 0x20004840 / 0x200044dc |
| Main header | DAT 0x10064: stored length, decoded length (two LE32) |
| Main LZSS | DAT 0x1006c onward after autokey |
| Main decoded image | **RAM 0x20005000**, ARMv7-A little endian |
| Embedded aux length table | DAT 0x262064, four LE32 equal f44/f48/f4c/f50 |
| Embedded aux1 / aux2 LZSS | DAT 0x262074 / 0x263064, matching tail plaintext |
| Aux1 decoded code | **RAM 0x20000008**, Thumb, entry pointer 0x200002b1 |
| Aux2 decoded firmware | **Flash 0x08000000**, Cortex-M/Thumb, reset 0x08000e71 |

E140 main: stored2071976, decoded3558876 bytes. Valid vectors, IC-705 identifiers,
version strings. Aux1=896, aux2=65536, DSP=2097152, WLAN=4188200 decoded bytes.
DSP requires architecture/subimage mapping. WLAN is a ProgrammingImage package
containing /sys/BTL_sflash.ptc and /sys/factory.img paths.

Decoder outputs manifests, raw images, and ARM ELF32 wrappers with correct load
and entry addresses for bootstrap/loader/main/aux1/aux2. See decoded_firmware/README.md.

## Validation and limits

Independent LZSS implementations agree byte-for-byte. Header lengths, recovered
loader instructions, vectors/literal pools and duplicated aux plaintext corroborate
these images. SHA-256 values are in manifests. Recovered C routines are manually
reconstructed from the actual disassembly.

Decoding does not establish acceptance of modified updates by the radio. Future
rebuild work must handle all four MD5 domains and the unresolved IV policy.
