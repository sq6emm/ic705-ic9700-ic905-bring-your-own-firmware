#!/usr/bin/env python3
"""Apply a firmware patch file to an official Icom .dat you downloaded yourself.

    python3 icom_patch.py patches/<name>.json  <official .dat>  <output .dat>

The patch file carries only the changes to the main-CPU image (and, optionally, to the
64 KiB bootstrap/loader at the start of the container, "bootstrap" section): our own
code and data as bytes, and every piece that already exists in the official file as a
"copy from the official image/bootstrap" reference. No Icom firmware is distributed.

The script refuses to run unless the input file has exactly the MD5 named in the patch,
and refuses to write the result unless the output has exactly the expected MD5, i.e.
you get a byte-identical copy of the build that was tested, or nothing.
"""
import hashlib
import json
import struct
import sys

from icom_rza1_decode import derive_key, find_start, autokey_decode, lzss_decompress
from ic_rza1_repack import repack, _find_md5_region, ARM_VECTORS, BOOTSTRAP


def md5(b):
    return hashlib.md5(b).hexdigest()


def extract_main(raw):
    """Main-CPU image decoded from an official .dat (same procedure as repack())."""
    return extract(raw)[0]


def extract(raw):
    """(main-CPU image, 64 KiB bootstrap) decoded from a .dat (same procedure as repack())."""
    key, _ = derive_key(raw)
    start, iv, _ = find_start(raw, key)
    digest_at = _find_md5_region(raw, start)
    if digest_at is None:
        raise ValueError("unsupported container (no single prefix-MD5 region)")
    body, _ = autokey_decode(raw[start:digest_at], start, key, iv)
    stream_off = next(o for o in range(BOOTSTRAP, BOOTSTRAP + 0x10)
                      if lzss_decompress(body[o:o + 64], 8)[0][:8] == ARM_VECTORS)
    words = [struct.unpack_from("<I", body, BOOTSTRAP + o)[0]
             for o in range(0, stream_off - BOOTSTRAP, 4)]
    image, _ = lzss_decompress(body[stream_off:], max(words))
    return image, bytes(body[:BOOTSTRAP])


def apply_ops(stock, patch):
    """Apply patch["ops"] (addresses relative to patch["load_address"]) to ``stock``.
    ``patch`` may also be a patch's "bootstrap" section (base 0, fixed size)."""
    base = patch.get("load_address", 0)
    size = patch.get("image_size", len(stock))
    img = bytearray(stock) + bytearray(max(0, size - len(stock)))
    del img[size:]
    for op in patch["ops"]:
        at = int(op["at"], 16) - base
        if "copy_from" in op:                       # copy from the OFFICIAL image
            src = int(op["copy_from"], 16) - base
            data = stock[src:src + op["len"]]
            if len(data) != op["len"]:
                raise ValueError("copy op outside the official image")
        else:
            data = bytes.fromhex(op["hex"])
        if at < 0 or at + len(data) > size:
            raise ValueError("patch op outside the image")
        img[at:at + len(data)] = data
    return bytes(img)


def main(argv):
    if len(argv) != 4:
        print(__doc__.strip().splitlines()[2])
        return 2
    patch = json.load(open(argv[1]))
    raw = open(argv[2], "rb").read()

    print("patch :", patch["name"])
    print("input :", argv[2], md5(raw))
    if md5(raw) != patch["input"]["md5"]:
        print("ERROR: input is not %s (expected MD5 %s). Download the official file from Icom."
              % (patch["input"]["file"], patch["input"]["md5"]))
        return 1

    stock, boot = extract(raw)
    if md5(stock) != patch["input"]["main_md5"]:
        print("ERROR: decoded main image does not match (expected MD5 %s)" % patch["input"]["main_md5"])
        return 1

    image = apply_ops(stock, patch)
    if md5(image) != patch["output"]["main_md5"]:
        print("ERROR: patched main image MD5 %s, expected %s" % (md5(image), patch["output"]["main_md5"]))
        return 1

    new_boot = None
    if "bootstrap" in patch:                        # optional: replaced bootstrap/loader
        bp = patch["bootstrap"]
        if md5(boot) != bp["input_md5"]:
            print("ERROR: decoded bootstrap does not match (expected MD5 %s)" % bp["input_md5"])
            return 1
        new_boot = apply_ops(boot, bp)
        if md5(new_boot) != bp["output_md5"]:
            print("ERROR: patched bootstrap MD5 %s, expected %s" % (md5(new_boot), bp["output_md5"]))
            return 1

    out = repack(raw, image, new_bootstrap=new_boot)
    print("output:", argv[3], md5(out))
    if md5(out) != patch["output"]["md5"]:
        print("ERROR: output MD5 differs from the tested build (%s); nothing written."
              % patch["output"]["md5"])
        return 1
    open(argv[3], "wb").write(out)
    print("OK: output is byte-identical to the tested build.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
