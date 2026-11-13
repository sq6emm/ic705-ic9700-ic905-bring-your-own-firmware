// Icom RZ/A1 firmware container codec (IC-705 / IC-9700): autokey transform, Okumura LZSS,
// container re-seal. A line-by-line port of icom_rza1_decode.py and ic_rza1_repack.py in
// this repository; the LZSS encoder produces exactly the same bytes as the Python
// lzss_compress (same search order, tie-breaking, lazy matching and max_chain).
// MIT licence (see the repository LICENSE).

import { Md5 } from './md5.js';

export const ARM_VECTORS = Uint8Array.of(0x18, 0xf0, 0x9f, 0xe5, 0x18, 0xf0, 0x9f, 0xe5);
export const BOOTSTRAP = 0x10000;

function isVectors(a, off = 0) {
  if (a.length < off + 8) return false;
  for (let i = 0; i < 8; i++) if (a[off + i] !== ARM_VECTORS[i]) return false;
  return true;
}

function bytesEqual(a, b) {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
}

function u32(a, o) { return (a[o] | (a[o + 1] << 8) | (a[o + 2] << 16) | (a[o + 3] << 24)) >>> 0; }

function concat(parts) {
  let n = 0; for (const p of parts) n += p.length;
  const out = new Uint8Array(n); let o = 0;
  for (const p of parts) { out.set(p, o); o += p.length; }
  return out;
}

/** Recover plaintext backwards from a known final plaintext byte (0xFF anchor). */
export function autokeyReverse(data, base, key, last = 0xFF) {
  const out = new Uint8Array(data.length); let p = last;
  for (let i = data.length - 1; i >= 0; i--) { out[i] = p; p = ((data[i] ^ p) - base - i + key) & 255; }
  return [out, p];
}

export function autokeyDecode(data, base, key, previous) {
  const out = new Uint8Array(data.length);
  for (let i = 0; i < data.length; i++) {
    previous = data[i] ^ ((previous + base + i - key) & 255); out[i] = previous;
  }
  return [out, previous];
}

export function autokeyEncode(plain, base, key, previous) {
  const out = new Uint8Array(plain.length);
  for (let i = 0; i < plain.length; i++) {
    const p = plain[i]; out[i] = p ^ ((previous + base + i - key) & 255); previous = p;
  }
  return out;
}

function oob() { throw new RangeError('LZSS stream truncated (read past end)'); }

/** Okumura LZSS decoder. Returns [output, consumed]. */
export function lzssDecompress(data, expected) {
  if (!(expected > 0 && expected <= 64 * 1024 * 1024)) throw new Error('unreasonable output length');
  const ring = new Uint8Array(4096); let cursor = 0xFEE, pos = 0, flags = 0, n = 0;
  const out = new Uint8Array(expected); const L = data.length;
  while (n < expected) {
    if (pos >= L) break;
    flags >>>= 1;
    if (!(flags & 0x100)) { flags = data[pos] | 0xFF00; pos++; }
    if (flags & 1) {
      if (pos >= L) oob();
      const v = data[pos]; out[n++] = v; ring[cursor] = v; cursor = (cursor + 1) & 4095; pos++;
    } else {
      if (pos + 1 >= L) oob();
      const lo = data[pos], hi = data[pos + 1]; pos += 2;
      const index = lo | ((hi & 0xF0) << 4), length = (hi & 0x0F) + 3;
      for (let j = 0; j < length; j++) {
        if (n >= expected) break;
        const v = ring[(index + j) & 4095]; out[n++] = v; ring[cursor] = v; cursor = (cursor + 1) & 4095;
      }
    }
  }
  return [n === expected ? out : out.slice(0, n), pos];
}

/**
 * Okumura LZSS encoder, bit-identical to ic_rza1_repack.lzss_compress: window 4096, min
 * match 3, max 18, exact 3-byte-key hash chains (newest first), first longest match wins,
 * optional one-step lazy matching. onProgress(done, total) is called periodically.
 */
export function lzssCompress(data, maxChain = 256, lazy = true, onProgress = null) {
  const n = data.length;
  const heads = new Int32Array(1 << 24);           // exact 24-bit key -> last position + 1 (0 = none)
  const prev = new Int32Array(Math.max(n, 1)).fill(-1);
  let mBest = 0, mDist = 0;

  const insert = (p) => {
    if (p + 3 <= n) {
      const k = (data[p] << 16) | (data[p + 1] << 8) | data[p + 2];
      prev[p] = heads[k] - 1; heads[k] = p + 1;
    }
  };
  const match = (i) => {
    mBest = 0; mDist = 0;
    if (i + 3 > n) return;
    const k = (data[i] << 16) | (data[i + 1] << 8) | data[i + 2];
    let p = heads[k] - 1, chain = 0, best = 0, dist = 0;
    const maxLen = Math.min(18, n - i);
    while (p >= 0 && (i - p) <= 4095 && chain < maxChain) {
      if (data[p + best] === data[i + best]) {
        let l = 0;
        while (l < maxLen && data[p + l] === data[i + l]) l++;
        if (l > best) { best = l; dist = i - p; }
        if (best === maxLen) break;
      }
      p = prev[p]; chain++;
    }
    mBest = best; mDist = dist;
  };

  const out = new Uint8Array(n + (n >> 3) + 16);
  let o = 0, flagAt = 0, bit = 8, cursor = 0xFEE;
  const slot = () => { if (bit === 8) { flagAt = o; out[o++] = 0; bit = 0; } };
  const lit = (v) => { slot(); out[flagAt] |= (1 << bit); bit++; out[o++] = v; cursor = (cursor + 1) & 4095; };
  const ref = (d, l) => {
    slot(); bit++;
    const index = (cursor - d) & 4095;
    out[o++] = index & 0xFF; out[o++] = ((index >> 4) & 0xF0) | ((l - 3) & 0x0F);
    cursor = (cursor + l) & 4095;
  };

  let i = 0, nextReport = 0;
  while (i < n) {
    if (onProgress && i >= nextReport) { onProgress(i, n); nextReport = i + 65536; }
    match(i); const best = mBest, dist = mDist;
    if (best >= 3 && lazy && i + 1 < n) {
      match(i + 1);
      if (mBest > best) { lit(data[i]); insert(i); i += 1; continue; }
    }
    if (best >= 3) {
      ref(dist, best);
      for (let kk = 0; kk < best; kk++) insert(i + kk);
      i += best;
    } else {
      lit(data[i]); insert(i); i += 1;
    }
  }
  if (onProgress) onProgress(n, n);
  return out.slice(0, o);
}

/** Find a monotonically -1 run (0xFF padding under the autokey) and read KEY off it. */
export function deriveKey(raw, search = 0x8000) {
  const end = Math.min(search, raw.length - 16);
  for (let s = 0x20; s < end; s++) {
    let ok = true;
    for (let i = 0; i < 15; i++) if (((raw[s + i] - 1) & 255) !== raw[s + i + 1]) { ok = false; break; }
    if (ok) return [(raw[s] + s) & 255, s];
  }
  throw new Error('no 0xFF padding ramp found; cannot derive KEY');
}

/** Sweep the domain start; anchor on the nearest 0xFF run (bootstrap padding). */
export function findStart(raw, key) {
  for (let s = 0x10; s < 0x1000; s++) {
    for (const window of [0x1000, BOOTSTRAP]) {
      if (s + window > raw.length) continue;
      const [boot, iv] = autokeyReverse(raw.subarray(s, s + window), s, key);
      if (isVectors(boot)) {
        const [full] = autokeyDecode(raw.subarray(s, s + BOOTSTRAP), s, key, iv);
        return [s, iv, full];
      }
    }
  }
  throw new Error('ARM bootstrap not found');
}

/** Offset of the 16-byte MD5 that covers [start:offset], or null. */
export function findMd5Region(raw, start) {
  const h = new Md5(); let last = start;
  for (let p = start; p < raw.length - 16; p += 0x1000) {
    if (p > start) {
      h.update(raw.subarray(last, p)); last = p;
      if (bytesEqual(h.digest(), raw.subarray(p, p + 16))) return p;
    }
  }
  return null;
}

/** Common container analysis: key, start, iv, digest offset and the decoded main domain. */
export function openContainer(raw) {
  const [key] = deriveKey(raw);
  const [start, iv] = findStart(raw, key);
  const digestAt = findMd5Region(raw, start);
  if (digestAt === null) throw new Error('unsupported container (no single prefix-MD5 region)');
  const [body] = autokeyDecode(raw.subarray(start, digestAt), start, key, iv);
  let streamOff = null;
  for (let off = BOOTSTRAP; off < BOOTSTRAP + 0x10; off++) {
    const [probe] = lzssDecompress(body.subarray(off, off + 64), 8);
    if (isVectors(probe)) { streamOff = off; break; }
  }
  if (streamOff === null) throw new Error('main LZSS stream not located');
  const words = [];
  for (let o = 0; o < streamOff - BOOTSTRAP; o += 4) words.push(u32(body, BOOTSTRAP + o));
  return { key, start, iv, digestAt, body, streamOff, words };
}

/**
 * Return a device-valid .dat carrying newImage as the main-CPU image (port of
 * ic_rza1_repack.repack). newBootstrap (optional, 0x10000 bytes) replaces body[0:0x10000].
 */
export function repack(raw, newImage, newBootstrap = null, onProgress = null, ctx = null) {
  const { key, start, iv, digestAt, body, streamOff, words } = ctx || openContainer(raw);
  const outLen = newImage.length;
  const [, consumed] = lzssDecompress(body.subarray(streamOff), words.length ? Math.max(...words) : outLen);

  let chain = new Uint8Array(0), chainEnd;
  const twoWordHdr = (streamOff - BOOTSTRAP) === 8;
  if (twoWordHdr) {
    const oldCsz = words[0];
    let bad = oldCsz < consumed;
    if (!bad) for (let q = streamOff + consumed; q < Math.min(streamOff + oldCsz, body.length); q++) if (body[q] !== 0xFF) { bad = true; break; }
    if (bad) throw new Error('main header csz does not match the stream');
    let p = streamOff + oldCsz;
    const isFF8 = (q) => { for (let t = 0; t < 8; t++) if (body[q + t] !== 0xFF) return false; return true; };
    while (p + 8 <= body.length && !isFF8(p)) {
      const bcsz = u32(body, p), bosz = u32(body, p + 4);
      const [o2, used] = lzssDecompress(body.subarray(p + 8, p + 8 + bcsz), bosz);
      if (o2.length !== bosz || used > bcsz)
        throw new Error('unrecognised data right after the main stream at body 0x' + p.toString(16));
      p += 8 + bcsz;
    }
    chain = body.slice(streamOff + oldCsz, p);
    chainEnd = p;
  } else {
    chainEnd = streamOff + consumed;
  }
  let nextComp = chainEnd;
  while (nextComp < body.length && body[nextComp] === 0xFF) nextComp++;

  let stream = lzssCompress(newImage, 256, true, onProgress);
  if (chain.length) {
    const pad = (-stream.length % 4 + 4) % 4;
    if (pad) stream = concat([stream, new Uint8Array(pad).fill(0xFF)]);
  }
  const header = new Uint8Array(twoWordHdr ? 8 : 4);
  const dv = new DataView(header.buffer);
  if (twoWordHdr) { dv.setUint32(0, stream.length, true); dv.setUint32(4, outLen, true); }
  else dv.setUint32(0, outLen, true);
  const slot = nextComp - BOOTSTRAP;
  const mainLen = header.length + stream.length + chain.length;
  if (mainLen > slot)
    throw new Error(`re-compressed main (+chained blobs) (${mainLen}) exceeds the ${slot}-byte slot`);

  let boot = body.subarray(0, BOOTSTRAP);
  if (newBootstrap !== null) {
    if (newBootstrap.length !== BOOTSTRAP) throw new Error('bootstrap must be exactly 0x10000 bytes');
    if (!bytesEqual(newBootstrap.subarray(0x4F00, 0x4F10), boot.subarray(0x4F00, 0x4F10)))
      throw new Error('bootloader tag at 0x4f00 differs from the stock container');
    if (!isVectors(newBootstrap)) throw new Error('bootstrap does not start with the ARM vector table');
    boot = newBootstrap;
  }
  const newBody = new Uint8Array(digestAt - start);
  let o = 0;
  newBody.set(boot, o); o += BOOTSTRAP;
  newBody.set(header, o); o += header.length;
  newBody.set(stream, o); o += stream.length;
  newBody.set(chain, o); o += chain.length;
  newBody.fill(0xFF, o, BOOTSTRAP + slot); o = BOOTSTRAP + slot;
  newBody.set(body.subarray(nextComp), o); o += body.length - nextComp;
  if (o !== newBody.length) throw new Error('internal error: body length changed');

  const newDomain = autokeyEncode(newBody, start, key, iv);
  const digest = new Md5().update(newDomain).digest();
  const out = new Uint8Array(raw.length);
  out.set(raw.subarray(0, start), 0);
  out.set(newDomain, start);
  out.set(digest, digestAt);
  out.set(raw.subarray(digestAt + 16), digestAt + 16);
  return out;
}

/** (main-CPU image, 64 KiB bootstrap, container context) decoded from a .dat. */
export function extract(raw) {
  const ctx = openContainer(raw);
  const [image] = lzssDecompress(ctx.body.subarray(ctx.streamOff), Math.max(...ctx.words));
  return { image, bootstrap: ctx.body.slice(0, BOOTSTRAP), ctx };
}
