#!/usr/bin/env python3
"""Re-pack a (modified) Icom RZ/A1 main-CPU image back into a device-valid .dat.

The two primitives are exact inverses of the decoder's stages and are model-independent:

  * ``lzss_compress``  -- Okumura LZSS encoder (lazy matching); its output is decoded
    bit-for-bit by ``icom_rza1_decode.lzss_decompress`` and is typically ~2% smaller
    than Icom's own, so a re-packed main image fits even the tightly-packed models.
  * ``autokey_encode`` -- inverse of the autokey byte transform (feedback on the
    previous plaintext byte).

``repack`` splices a (re-compressed) main image into the container and re-seals the
single MD5 that covers the main domain. Because only the main component changes, any
other components keep their own (unchanged) integrity.

Verified identity re-pack (same image -> valid container that re-decodes to the identical
image, only the main slot and the 16-byte digest change):

  IC-705  (MD5 region 0x9c0000)   IC-9700 (0x800000)   IC-7610 (0x400000)

IC-905 uses a different, multi-component integrity layout (no single prefix MD5) and is
not yet covered by ``repack`` -- its decode works, its re-seal is a follow-up.

No firmware is included; supply your own .dat from Icom's official update.
"""
import hashlib
import struct

from icom_rza1_decode import (ARM_VECTORS, BOOTSTRAP, autokey_decode, derive_key,
                              find_start, lzss_decompress)


def autokey_encode(plain, base, key, previous):
    out = bytearray()
    for i, p in enumerate(plain):
        out.append(p ^ ((previous + base + i - key) & 255)); previous = p
    return bytes(out)


def lzss_compress(data, max_chain=256, lazy=True):
    """Okumura LZSS encoder matching icom_rza1_decode.lzss_decompress (window 4096,
    min match 3, max 18, matches only real history). Greedy 3-gram hash chains with
    optional one-step lazy matching."""
    n = len(data); heads = {}; chain_prev = [-1] * max(n, 1)

    def insert(p):
        if p + 3 <= n:
            k = (data[p] << 16) | (data[p + 1] << 8) | data[p + 2]
            chain_prev[p] = heads.get(k, -1); heads[k] = p

    def match(i):
        if i + 3 > n:
            return 0, 0
        k = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2]
        p = heads.get(k, -1); chain = 0; max_len = min(18, n - i); best = 0; dist = 0
        while p >= 0 and (i - p) <= 4095 and chain < max_chain:
            if data[p + best] == data[i + best]:
                l = 0
                while l < max_len and data[p + l] == data[i + l]:
                    l += 1
                if l > best:
                    best = l; dist = i - p
                if best == max_len:
                    break
            p = chain_prev[p]; chain += 1
        return best, dist

    tokens = []; i = 0
    while i < n:
        best, dist = match(i)
        if best >= 3 and lazy and i + 1 < n and match(i + 1)[0] > best:
            tokens.append((1, data[i])); insert(i); i += 1; continue
        if best >= 3:
            tokens.append((0, dist, best))
            for kk in range(best):
                insert(i + kk)
            i += best
        else:
            tokens.append((1, data[i])); insert(i); i += 1

    out = bytearray(); cursor = 0xFEE; j = 0
    while j < len(tokens):
        group = tokens[j:j + 8]; flag = 0; body = bytearray()
        for b, t in enumerate(group):
            if t[0] == 1:
                flag |= (1 << b); body.append(t[1]); cursor = (cursor + 1) & 4095
            else:
                _, d, l = t; index = (cursor - d) & 4095
                body.append(index & 0xFF)
                body.append(((index >> 4) & 0xF0) | ((l - 3) & 0x0F))
                cursor = (cursor + l) & 4095
        out.append(flag); out += body; j += 8
    return bytes(out)


def _find_md5_region(raw, start):
    """Offset of the 16-byte MD5 that covers [start:offset], or None."""
    h = hashlib.md5(); last = start
    for p in range(start, len(raw) - 16, 0x1000):
        if p > start:
            h.update(raw[last:p]); last = p
            if h.copy().digest() == raw[p:p + 16]:
                return p
    return None


def repack(raw, new_image):
    """Return a device-valid .dat carrying ``new_image`` as the main-CPU image.

    Works for radios with a single prefix MD5 over the main domain (IC-705, IC-9700,
    IC-7610). Raises ValueError for a layout it does not cover (e.g. IC-905) or if the
    re-compressed image does not fit the main slot.
    """
    key, _ = derive_key(raw)
    start, iv, _ = find_start(raw, key)
    digest_at = _find_md5_region(raw, start)
    if digest_at is None:
        raise ValueError("no single prefix-MD5 region (multi-component model, e.g. IC-905)")
    body, _ = autokey_decode(raw[start:digest_at], start, key, iv)

    # locate the main LZSS header/stream just past the 64 KiB bootstrap
    stream_off = None
    for off in range(BOOTSTRAP, BOOTSTRAP + 0x10):
        probe, _ = lzss_decompress(body[off:off + 64], 8)
        if probe[:8] == ARM_VECTORS:
            stream_off = off; break
    if stream_off is None:
        raise ValueError("main LZSS stream not located")
    words = [struct.unpack_from("<I", body, BOOTSTRAP + o)[0] for o in range(0, stream_off - BOOTSTRAP, 4)]
    out_len = len(new_image)
    _, consumed = lzss_decompress(body[stream_off:], max(words) if words else out_len)
    next_comp = stream_off + consumed
    while next_comp < len(body) and body[next_comp] == 0xFF:
        next_comp += 1

    stream = lzss_compress(new_image)
    header = struct.pack("<II", len(stream), out_len) if (stream_off - BOOTSTRAP) == 8 \
        else struct.pack("<I", out_len)
    main = header + stream
    slot = next_comp - BOOTSTRAP
    if len(main) > slot:
        raise ValueError("re-compressed main (%d) exceeds the %d-byte slot" % (len(main), slot))
    main += b"\xff" * (slot - len(main))
    new_body = body[:BOOTSTRAP] + main + body[next_comp:]
    assert len(new_body) == digest_at - start
    new_domain = autokey_encode(new_body, start, key, iv)
    digest = hashlib.md5(new_domain).digest()
    return raw[:start] + new_domain + digest + raw[digest_at + 16:]


def _selftest(path):
    raw = open(path, "rb").read()
    key, _ = derive_key(raw); start, iv, _ = find_start(raw, key)
    digest_at = _find_md5_region(raw, start)
    body, _ = autokey_decode(raw[start:digest_at], start, key, iv)
    stream_off = next(o for o in range(BOOTSTRAP, BOOTSTRAP + 0x10)
                      if lzss_decompress(body[o:o + 64], 8)[0][:8] == ARM_VECTORS)
    words = [struct.unpack_from("<I", body, BOOTSTRAP + o)[0] for o in range(0, stream_off - BOOTSTRAP, 4)]
    image, _ = lzss_decompress(body[stream_off:], max(words))
    new = repack(raw, image)                           # identity re-pack
    assert len(new) == len(raw)
    assert hashlib.md5(new[start:digest_at]).digest() == new[digest_at:digest_at + 16]
    b2, _ = autokey_decode(new[start:digest_at], start, key, iv)
    img2, _ = lzss_decompress(b2[stream_off:], max(words))
    assert img2 == image
    print("identity re-pack OK: valid MD5, re-decodes to the identical image")


if __name__ == "__main__":
    import sys
    _selftest(sys.argv[1])
