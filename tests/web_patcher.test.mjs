// Tests for the browser patcher (docs/js/*.js) -- the same ES modules the page loads.
//
//   node --test tests/                      (Node >= 18; run from the repository root)
//
// Synthetic tests always run. Tests on official firmware run only when the official .dat
// files are present (repository root, current directory or $ICOM_DAT_DIR); they are never
// part of this repository. Reference values marked "python" were produced by the Python
// tools in this repository (ic_rza1_repack.lzss_compress / repack, icom_patch.extract).

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync, existsSync, readdirSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'node:url';

import { md5hex, Md5 } from '../docs/js/md5.js';
import { lzssCompress, lzssDecompress, autokeyEncode, autokeyDecode, extract, repack } from '../docs/js/codec.js';
import { buildPatched, PatchError } from '../docs/js/patch.js';

const ROOT = join(dirname(fileURLToPath(import.meta.url)), '..');
const enc = (s) => new TextEncoder().encode(s);

// Python reference values (python3 + ic_rza1_repack.py / icom_patch.py of this repository).
const PY_SYNTHETIC = {
  "mode0_20000": {
    "data_md5": "08dd33f9949104539adbcc10e5d18f8b",
    "lzss_md5": "2e528c9303e7374f63d9b503cbf91a58",
    "lzss_nolazy_md5": "9546e9ca7869292a99e829f022ced368",
    "lzss_chain8_md5": "d7f0599ff36b19a1809696e87c451f8d"
  },
  "mode1_5000": {
    "data_md5": "be0dbef85aeaff1725731885076ca5dd",
    "lzss_md5": "0169ea0e6cb2068ca3fecaee08b28dae",
    "lzss_nolazy_md5": "0169ea0e6cb2068ca3fecaee08b28dae",
    "lzss_chain8_md5": "0169ea0e6cb2068ca3fecaee08b28dae"
  },
  "mode2_30000": {
    "data_md5": "146b221f926557f1cb7de3fd1dedfc9d",
    "lzss_md5": "450cef75096379c8116325e42ec0012d",
    "lzss_nolazy_md5": "1829bdbb4f2c9acca7b29a84f76d10bf",
    "lzss_chain8_md5": "3cb5d7084bf7583f920c2028b3cd45c7"
  }
};
const PY_OFFICIAL = {
  "2c9c1d7f3fc80ef173e01d841cd1248a": {
    "file": "705E140.dat",
    "main_md5": "62fbe39f29bcf0b3cc341b861912047d",
    "boot_md5": "9f97d0004c205a5d5bb247578098e153",
    "lzss_main_md5": "6ecc176013fd8c7aee0384c4ab3e2d85",
    "lzss_main_len": 2033048,
    "identity_repack_md5": "3fa7ef7ba63479555e7bf4f7121fcb87"
  },
  "b605171288db4a74e0992a8f2afcb1c3": {
    "file": "705J140.dat",
    "main_md5": "c8202dfae07bca21b2d28f6015346863",
    "boot_md5": "9f97d0004c205a5d5bb247578098e153",
    "lzss_main_md5": "ff6843e4cdfce76d8cd12a3a22798372",
    "lzss_main_len": 2032772,
    "identity_repack_md5": "5a1f881ea2c1148c3693b4ae656bff75"
  },
  "6f04fd2248ba39254ed01b30b52ce36a": {
    "file": "9700E150.dat",
    "main_md5": "eea365d28c007ce44aded0a37955913f",
    "boot_md5": "eec896d4b9a7d254dc8723df4ed4cf16",
    "lzss_main_md5": "556fac866132efb822ca17c92f1a0261",
    "lzss_main_len": 2141695,
    "identity_repack_md5": "21678ad65b992c4e1580fff970079dcd"
  }
};

function lcg(n, seed, mode) {
  let x = seed; const out = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    x = (Math.imul(x, 1103515245) + 12345) & 0x7fffffff;
    const r = x >>> 16;
    if (mode === 0) out[i] = r & 3;
    else if (mode === 1) out[i] = r & 255;
    else out[i] = (i > 40 && (r & 7)) ? out[i - 1 - ((r >>> 3) % 40)] : 97 + (r % 26);
  }
  return out;
}

function findDat(name) {
  for (const d of [process.env.ICOM_DAT_DIR, ROOT, process.cwd()]) {
    if (d && existsSync(join(d, name))) return join(d, name);
  }
  return null;
}

test('md5 known vectors and incremental/clone', () => {
  assert.equal(md5hex(new Uint8Array(0)), 'd41d8cd98f00b204e9800998ecf8427e');
  assert.equal(md5hex(enc('abc')), '900150983cd24fb0d6963f7d28e17f72');
  assert.equal(md5hex(enc('The quick brown fox jumps over the lazy dog')), '9e107d9d372bb6826bd81d3542a419d6');
  const big = lcg(100003, 7, 1);
  const h = new Md5();
  for (let o = 0; o < big.length; o += 777) {
    h.update(big.subarray(o, o + 777));
    if (o === 7770) assert.equal(h.clone().hexdigest(), md5hex(big.subarray(0, 7770 + 777)));
  }
  assert.equal(h.hexdigest(), md5hex(big));
});

test('autokey encode/decode are exact inverses', () => {
  const data = lcg(9000, 2, 1);
  for (const [base, key, iv] of [[0x64, 0x19, 0x9b], [0x38, 0x2b, 0x00], [0x88, 0x7f, 0xff]]) {
    const e = autokeyEncode(data, base, key, iv);
    const [d] = autokeyDecode(e, base, key, iv);
    assert.deepEqual(d, data);
  }
});

test('lzss round trip on small and synthetic inputs', () => {
  for (const s of ['X', 'XY', 'XYZ', 'ABCABCABC', 'A'.repeat(64), 'hello hello hello world world']) {
    const b = enc(s);
    assert.deepEqual(lzssDecompress(lzssCompress(b), b.length)[0], b);
  }
  for (const [mode, n] of [[0, 8000], [1, 5000], [2, 30000]]) {
    const d = lcg(n, 99 + mode, mode);
    assert.deepEqual(lzssDecompress(lzssCompress(d), n)[0], d);
  }
});

test('lzss encoder is byte-identical to Python on synthetic inputs', () => {
  const keys = Object.keys(PY_SYNTHETIC);
  assert.ok(keys.length > 0, 'reference table empty');
  for (const k of keys) {
    const [, mode, n] = k.match(/^mode(\d+)_(\d+)$/).map(Number);
    const d = lcg(n, 12345 + mode, mode);
    const ref = PY_SYNTHETIC[k];
    assert.equal(md5hex(d), ref.data_md5, k + ' data');
    assert.equal(md5hex(lzssCompress(d)), ref.lzss_md5, k + ' lazy');
    assert.equal(md5hex(lzssCompress(d, 256, false)), ref.lzss_nolazy_md5, k + ' greedy');
    assert.equal(md5hex(lzssCompress(d, 8)), ref.lzss_chain8_md5, k + ' max_chain=8');
  }
});

test('docs/patches/index.json: one release per input, patch files present and matching', () => {
  const idx = JSON.parse(readFileSync(join(ROOT, 'docs/patches/index.json'), 'utf8'));
  const seen = new Set();
  for (const e of idx.releases) {
    assert.ok(!seen.has(e.input.md5), 'duplicate input ' + e.input.md5); seen.add(e.input.md5);
    const p = JSON.parse(readFileSync(join(ROOT, 'docs/patches', e.release.patch_file), 'utf8'));
    assert.equal(p.input.md5, e.input.md5);
    assert.equal(p.output.md5, e.release.output_md5);
    assert.equal(e.release.bootloader, 'bootstrap' in p);
  }
  for (const f of readdirSync(join(ROOT, 'docs/patches'))) {
    if (f !== 'index.json') assert.ok(idx.releases.some((e) => e.release.patch_file === f), 'unreferenced ' + f);
  }
});

// ---- official firmware (skipped when absent) -------------------------------------------
const OFFICIAL = { '705E140.dat': null, '705J140.dat': null, '9700E150.dat': null };
for (const name of Object.keys(OFFICIAL)) OFFICIAL[name] = findDat(name);
const loaded = {};
const load = (name) => (loaded[name] ??= new Uint8Array(readFileSync(OFFICIAL[name])));

for (const name of Object.keys(OFFICIAL)) {
  test(`decode -> LZSS encode -> identity repack matches Python: ${name}`, { skip: !OFFICIAL[name] && 'official file not present' }, () => {
    const raw = load(name);
    const ref = PY_OFFICIAL[md5hex(raw)];
    assert.ok(ref, 'no Python reference for this file (not the official release?)');
    const { image, bootstrap } = extract(raw);
    assert.equal(md5hex(image), ref.main_md5);
    assert.equal(md5hex(bootstrap), ref.boot_md5);
    const comp = lzssCompress(image);
    assert.equal(comp.length, ref.lzss_main_len);
    assert.equal(md5hex(comp), ref.lzss_main_md5);
    assert.deepEqual(lzssDecompress(comp, image.length)[0], image);
    assert.equal(md5hex(repack(raw, image)), ref.identity_repack_md5);
  });
}

const patchDir = join(ROOT, 'patches');
for (const f of readdirSync(patchDir).filter((f) => f.endsWith('.json')).sort()) {
  const patch = JSON.parse(readFileSync(join(patchDir, f), 'utf8'));
  const dat = OFFICIAL[patch.input.file] ?? findDat(patch.input.file);
  test(`patch ${f} -> expected output MD5 ${patch.output.md5}`, { skip: !dat && 'official file not present' }, () => {
    const raw = new Uint8Array(readFileSync(dat));
    const stages = new Set();
    const { out, md5 } = buildPatched(raw, patch, (s) => stages.add(s));
    assert.equal(md5, patch.output.md5);
    assert.equal(md5hex(out), patch.output.md5);
    assert.equal(out.length, raw.length);
    assert.ok(stages.has('compress') && stages.has('done'));
  });

  test(`patch ${f} rejects a modified input`, { skip: !dat && 'official file not present' }, () => {
    const raw = new Uint8Array(readFileSync(dat));
    raw[4096] ^= 1;
    assert.throws(() => buildPatched(raw, patch), (e) => e instanceof PatchError && e.code === 'wrong_input');
  });
}

test('a patch refuses another radio\'s official file', { skip: !(OFFICIAL['9700E150.dat'] && OFFICIAL['705E140.dat']) && 'official files not present' }, () => {
  const p9700 = JSON.parse(readFileSync(join(patchDir, '9700E150_trv16c.json'), 'utf8'));
  assert.throws(() => buildPatched(load('705E140.dat'), p9700), (e) => e instanceof PatchError && e.code === 'wrong_input');
});
