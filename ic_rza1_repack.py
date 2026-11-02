#!/usr/bin/env python3
"""Re-pack a (modified) Icom RZ/A1 main-CPU image back into a device-valid .dat.

The two primitives are the exact inverses of the decoder's stages and are
model-independent:

  * ``lzss_compress``  -- Okumura LZSS encoder; its output is decoded bit-for-bit by
    ``icom_rza1_decode.lzss_decompress`` (verified on real firmware images).
  * ``autokey_encode`` -- inverse of the autokey byte transform (feedback on the
    previous plaintext byte).

``repack_ic705`` is the worked, verified container splice for the IC-705 (single main
domain, MD5 region ``[0x64 : 0x64+0x9c0000]``, const block at file ``0x600064``). Other
models share the primitives but have their own container offsets.

Proven (identity re-pack, same image): the result has the same length, a valid MD5, and
decodes back to the identical image; only the main slot (re-compressed) and the 16-byte
MD5 digest change -- bootstrap, const/FPGA block, DSP and WLAN stay byte-identical.

No firmware is included; supply your own .dat from Icom's official update.
"""
import hashlib
import struct

from icom_rza1_decode import autokey_decode, lzss_decompress, find_start, derive_key


def autokey_encode(plain, base, key, previous):
    out = bytearray()
    for i, p in enumerate(plain):
        out.append(p ^ ((previous + base + i - key) & 255)); previous = p
    return bytes(out)


def lzss_compress(data, max_chain=128):
    """Okumura LZSS encoder matching icom_rza1_decode.lzss_decompress.

    4096-byte window, min match 3, max 18; matches only real history (distance <= bytes
    emitted so far), so the zero-initialised ring is never referenced. Greedy longest
    match via 3-gram hash chains.
    """
    n = len(data); heads = {}; chain_prev = [-1] * max(n, 1)

    def insert(p):
        if p + 3 <= n:
            k = (data[p] << 16) | (data[p + 1] << 8) | data[p + 2]
            chain_prev[p] = heads.get(k, -1); heads[k] = p

    tokens = []; i = 0
    while i < n:
        best_len = 0; best_dist = 0
        if i + 3 <= n:
            k = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2]
            p = heads.get(k, -1); chain = 0; max_len = min(18, n - i)
            while p >= 0 and (i - p) <= 4095 and chain < max_chain:
                l = 0
                while l < max_len and data[p + l] == data[i + l]:
                    l += 1
                if l > best_len:
                    best_len = l; best_dist = i - p
                if best_len == max_len:
                    break
                p = chain_prev[p]; chain += 1
        if best_len >= 3:
            tokens.append((0, best_dist, best_len))
            for kk in range(best_len):
                insert(i + kk)
            i += best_len
        else:
            tokens.append((1, data[i])); insert(i); i += 1

    out = bytearray(); cursor = 0xFEE; j = 0
    while j < len(tokens):
        group = tokens[j:j + 8]; flag = 0; body = bytearray()
        for b, t in enumerate(group):
            if t[0] == 1:
                flag |= (1 << b); body.append(t[1]); cursor = (cursor + 1) & 4095
            else:
                _, dist, length = t; index = (cursor - dist) & 4095
                body.append(index & 0xFF)
                body.append(((index >> 4) & 0xF0) | ((length - 3) & 0x0F))
                cursor = (cursor + length) & 4095
        out.append(flag); out += body; j += 8
    return bytes(out)


# --- IC-705 container (verified constants) ---------------------------------------
IC705 = dict(start=0x64, key=0x19, md5_region=0x9C0000, const=0x600000)


def repack_ic705(raw, new_image):
    """Return a device-valid IC-705 .dat carrying ``new_image`` as the main-CPU image.

    ``new_image`` must be a full decompressed main image (it is re-compressed and
    re-sealed). The const/DSP/WLAN components are preserved exactly.
    """
    c = IC705; start = c["start"]; key = c["key"]
    digest_at = start + c["md5_region"]; const = c["const"]
    s, iv, _ = find_start(raw, key)
    assert s == start
    body, _ = autokey_decode(raw[start:digest_at], start, key, iv)
    _, out_len = struct.unpack_from("<II", body, 0x10000)
    if len(new_image) != out_len:
        out_len = len(new_image)                       # allow a resized image
    stream = lzss_compress(new_image)
    main = struct.pack("<II", len(stream), out_len) + stream
    slot = const - 0x10000
    if len(main) > slot:
        raise ValueError("re-compressed main does not fit the %d-byte slot" % slot)
    main += b"\xff" * (slot - len(main))
    new_body = body[:0x10000] + main + body[const:]
    assert len(new_body) == c["md5_region"]
    new_domain = autokey_encode(new_body, start, key, iv)
    digest = hashlib.md5(new_domain).digest()
    return raw[:start] + new_domain + digest + raw[digest_at + 16:]


def _selftest(path="705E140.dat"):
    raw = open(path, "rb").read()
    s, iv, _ = find_start(raw, IC705["key"])
    body, _ = autokey_decode(raw[0x64:0x64 + IC705["md5_region"]], 0x64, IC705["key"], iv)
    comp, out_len = struct.unpack_from("<II", body, 0x10000)
    image, _ = lzss_decompress(body[0x10008:0x10008 + comp], out_len)
    new = repack_ic705(raw, image)                     # identity re-pack
    assert len(new) == len(raw)
    assert hashlib.md5(new[0x64:0x64 + IC705["md5_region"]]).digest() == new[0x64 + IC705["md5_region"]:0x64 + IC705["md5_region"] + 16]
    b2, _ = autokey_decode(new[0x64:0x64 + IC705["md5_region"]], 0x64, IC705["key"], iv)
    c2, o2 = struct.unpack_from("<II", b2, 0x10000)
    img2, _ = lzss_decompress(b2[0x10008:0x10008 + c2], o2)
    assert img2 == image
    print("identity re-pack OK: valid MD5, re-decodes to the identical image")


if __name__ == "__main__":
    import sys
    _selftest(*sys.argv[1:2])
