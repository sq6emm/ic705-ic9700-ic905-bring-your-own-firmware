// Apply a patch JSON (patches/*.json) to an official Icom .dat -- port of icom_patch.py.
// The result is returned only if its MD5 equals the MD5 of the tested build.
// MIT licence (see the repository LICENSE).

import { md5hex } from './md5.js';
import { extract, repack } from './codec.js';

export class PatchError extends Error {
  constructor(code, message, details = {}) { super(message); this.code = code; this.details = details; }
}

function parseHexInt(s) {
  if (typeof s === 'number') return s;
  const v = parseInt(String(s).replace(/^0x/i, ''), 16);
  if (!Number.isFinite(v) || !/^(0x)?[0-9a-f]+$/i.test(String(s))) throw new PatchError('bad_patch', 'bad hex number in patch: ' + s);
  return v;
}

function hexToBytes(h) {
  if (h.length % 2 || /[^0-9a-f]/i.test(h)) throw new PatchError('bad_patch', 'bad hex data in patch');
  const out = new Uint8Array(h.length / 2);
  for (let i = 0; i < out.length; i++) out[i] = parseInt(h.substr(2 * i, 2), 16);
  return out;
}

/** Apply patch.ops (addresses relative to patch.load_address) to stock. */
export function applyOps(stock, patch) {
  const base = patch.load_address ?? 0;
  const size = patch.image_size ?? stock.length;
  const img = new Uint8Array(size);
  img.set(stock.subarray(0, Math.min(size, stock.length)));
  for (const op of patch.ops) {
    const at = parseHexInt(op.at) - base;
    let data;
    if ('copy_from' in op) {                          // copy from the OFFICIAL image
      const src = parseHexInt(op.copy_from) - base;
      if (src < 0) throw new PatchError('bad_patch', 'copy op outside the official image');
      data = stock.subarray(src, src + op.len);
      if (data.length !== op.len) throw new PatchError('bad_patch', 'copy op outside the official image');
    } else {
      data = hexToBytes(op.hex);
    }
    if (at < 0 || at + data.length > size) throw new PatchError('bad_patch', 'patch op outside the image');
    img.set(data, at);
  }
  return img;
}

/**
 * Build the patched .dat. raw: Uint8Array of the official file; patch: parsed patch JSON.
 * progress(stage, fraction) is called as work proceeds. Returns { out, md5 }.
 * Throws PatchError (code: wrong_input, decode_mismatch, output_mismatch, ...) otherwise.
 */
export function buildPatched(raw, patch, progress = () => {}) {
  progress('md5', 0);
  const inMd5 = md5hex(raw);
  if (inMd5 !== patch.input.md5)
    throw new PatchError('wrong_input', `input is not ${patch.input.file} (MD5 ${inMd5}, expected ${patch.input.md5})`,
      { got: inMd5, expected: patch.input.md5 });

  progress('decode', 0);
  const { image: stock, bootstrap: boot, ctx } = extract(raw);
  const stockMd5 = md5hex(stock);
  if (stockMd5 !== patch.input.main_md5)
    throw new PatchError('decode_mismatch', `decoded main image MD5 ${stockMd5}, expected ${patch.input.main_md5}`);

  progress('patch', 0);
  const image = applyOps(stock, patch);
  const imgMd5 = md5hex(image);
  if (imgMd5 !== patch.output.main_md5)
    throw new PatchError('patch_mismatch', `patched main image MD5 ${imgMd5}, expected ${patch.output.main_md5}`);

  let newBoot = null;
  if (patch.bootstrap) {
    const bp = patch.bootstrap;
    const bMd5 = md5hex(boot);
    if (bMd5 !== bp.input_md5)
      throw new PatchError('decode_mismatch', `decoded bootstrap MD5 ${bMd5}, expected ${bp.input_md5}`);
    newBoot = applyOps(boot, bp);
    const nbMd5 = md5hex(newBoot);
    if (nbMd5 !== bp.output_md5)
      throw new PatchError('patch_mismatch', `patched bootstrap MD5 ${nbMd5}, expected ${bp.output_md5}`);
  }

  progress('compress', 0);
  const out = repack(raw, image, newBoot, (d, t) => progress('compress', d / t), ctx);
  progress('seal', 1);
  const outMd5 = md5hex(out);
  if (outMd5 !== patch.output.md5)
    throw new PatchError('output_mismatch', `output MD5 ${outMd5} differs from the tested build (${patch.output.md5})`,
      { got: outMd5, expected: patch.output.md5 });
  progress('done', 1);
  return { out, md5: outMd5 };
}
