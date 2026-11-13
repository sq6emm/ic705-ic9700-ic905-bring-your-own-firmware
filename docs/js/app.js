// Page logic: file selection, recognition by MD5, release display, build via Web Worker.
// No network requests other than this site's own static files (patches/*.json).

const $ = (id) => document.getElementById(id);
const STAGES = {
  md5: ['Checking input MD5', 0, 4],
  decode: ['Decoding container', 4, 18],
  patch: ['Applying patch', 18, 22],
  compress: ['Compressing image (LZSS)', 22, 92],
  seal: ['Sealing container and verifying MD5', 92, 99],
  done: ['Done', 100, 100],
};

let index = null;          // docs/patches/index.json
let fileBuf = null;        // ArrayBuffer of the selected file
let fileName = '';
let current = null;        // matching index entry
let worker = null;
let jobId = 0;
let busy = false;

function getWorker() {
  if (!worker) worker = new Worker(new URL('./worker.js', import.meta.url), { type: 'module' });
  return worker;
}

function runWorker(msg, onProgress) {
  return new Promise((resolve, reject) => {
    const w = getWorker(); const id = ++jobId;
    const handler = (ev) => {
      const m = ev.data; if (m.id !== id) return;
      if (m.type === 'progress') { onProgress && onProgress(m.stage, m.frac); return; }
      w.removeEventListener('message', handler);
      if (m.type === 'error') reject(m); else resolve(m);
    };
    w.addEventListener('message', handler);
    w.onerror = (e) => { w.removeEventListener('message', handler); worker = null; reject({ code: 'worker', message: e.message || 'worker failed to start' }); };
    w.postMessage({ ...msg, id });
  });
}

function el(tag, attrs = {}, ...kids) {
  const e = document.createElement(tag);
  for (const [k, v] of Object.entries(attrs)) {
    if (k === 'class') e.className = v; else e.setAttribute(k, v);
  }
  for (const k of kids) e.append(k);
  return e;
}

function setStatus(node, kind, title, text) {
  node.hidden = false; node.className = 'status ' + kind; node.replaceChildren();
  node.append(el('strong', {}, title));
  if (text) node.append(el('div', {}, text));
}

function fmtSize(n) { return n.toLocaleString('en') + ' bytes (' + (n / 1048576).toFixed(2) + ' MiB)'; }

async function loadIndex() {
  const r = await fetch('patches/index.json', { cache: 'no-cache' });
  if (!r.ok) throw new Error('cannot load patches/index.json (' + r.status + ')');
  index = await r.json();
  const tb = $('supported').tBodies[0]; tb.replaceChildren();
  if (!index.releases.length) tb.append(el('tr', {}, el('td', { colspan: '4', class: 'muted' }, 'No releases published yet.')));
  for (const e of index.releases) {
    const rel = e.release;
    tb.append(el('tr', {},
      el('td', {}, `${e.input.radio} (${e.input.region})`),
      el('td', {}, el('code', {}, e.input.file), el('br'), el('code', { class: 'hash small' }, e.input.md5)),
      el('td', {}, rel.label + (rel.date ? ` (${rel.date})` : '')),
      el('td', {}, el('code', { class: 'hash small' }, rel.output_md5))));
  }
}

function resetRelease() {
  current = null; $('release').hidden = true; $('result').hidden = true; $('progress').hidden = true;
  $('accept').checked = false; updateBuildButton();
}

function updateBuildButton() {
  const ok = !!current && $('accept').checked && !busy;
  $('build').disabled = !ok;
  $('build-hint').textContent = busy ? 'Working…' : (current && !$('accept').checked ? 'Tick the box to enable.' : '');
}

async function onFile(file) {
  if (!file || busy) return;
  resetRelease();
  fileName = file.name;
  $('fileinfo').hidden = false;
  $('fi-name').textContent = file.name;
  $('fi-size').textContent = fmtSize(file.size);
  $('fi-md5').textContent = 'computing…';
  $('fi-status').hidden = true;
  if (file.size > 64 * 1048576) {
    setStatus($('fi-status'), 'err', 'Not an Icom firmware file', 'The file is far too large.');
    return;
  }
  try {
    fileBuf = await file.arrayBuffer();
    const { md5 } = await runWorker({ type: 'md5', buf: fileBuf.slice(0) });
    if (file.name !== fileName) return;
    $('fi-md5').textContent = md5;
    await indexReady;
    const entry = index && index.releases.find((e) => e.input.md5 === md5);
    if (!entry) {
      setStatus($('fi-status'), 'err', 'Not a supported official file',
        'This MD5 does not match any official Icom firmware file with a published release (see the table below). ' +
        'Use the unmodified .dat from Icom\'s website, exactly as downloaded and unzipped.');
      return;
    }
    const nameNote = entry.input.file !== file.name ? ` (file name differs, but the content is identical to ${entry.input.file})` : '';
    setStatus($('fi-status'), 'ok', `Recognised: official ${entry.input.radio} firmware ${entry.input.region} ${entry.input.version} — ${entry.input.file}`, nameNote ? nameNote.trim() : '');
    showRelease(entry);
  } catch (e) {
    setStatus($('fi-status'), 'err', 'Could not read the file', String(e.message || e));
  }
}

function showRelease(entry) {
  current = entry; const r = entry.release;
  $('rel-title').textContent = r.title;
  $('rel-meta').textContent = `Release ${r.label}` + (r.date ? ` · ${r.date}` : '') + ` · patch ${r.patch_file}`;
  const badges = $('rel-badges'); badges.replaceChildren();
  if (r.bootloader) badges.append(el('span', { class: 'badge warn' }, 'Includes custom A/B bootloader'));
  if (r.tx_outside_amateur_bands) badges.append(el('span', { class: 'badge danger' }, 'Wideband TX'));
  const ul = $('rel-features'); ul.replaceChildren();
  for (const f of r.features || []) ul.append(el('li', {}, f));
  ul.hidden = !(r.features || []).length;
  $('rel-in').textContent = entry.input.file;
  $('rel-in-md5').textContent = entry.input.md5;
  $('rel-out-md5').textContent = r.output_md5;
  $('warn-boot').hidden = !r.bootloader;
  $('warn-wb').hidden = !r.tx_outside_amateur_bands;
  $('release').hidden = false;
  updateBuildButton();
}

function showProgress(stage, frac) {
  const s = STAGES[stage] || [stage, 0, 0];
  const pct = Math.round(s[1] + (s[2] - s[1]) * (frac || 0));
  $('bar').style.width = pct + '%';
  $('progress').querySelector('.bar').setAttribute('aria-valuenow', String(pct));
  $('stage').textContent = s[0] + (stage === 'compress' ? ` — ${Math.round((frac || 0) * 100)} %` : '') + (stage === 'done' ? '' : '…');
}

async function build() {
  if (!current || busy || !fileBuf) return;
  busy = true; updateBuildButton();
  $('result').hidden = true; $('progress').hidden = false; showProgress('md5', 0);
  const t0 = performance.now();
  const tick = setInterval(() => { $('elapsed').textContent = ((performance.now() - t0) / 1000).toFixed(1) + ' s'; }, 200);
  try {
    const pr = await fetch('patches/' + encodeURIComponent(current.release.patch_file), { cache: 'no-cache' });
    if (!pr.ok) throw { code: 'fetch', message: 'cannot load the patch file (' + pr.status + ')' };
    const patch = await pr.json();
    if (patch.input.md5 !== current.input.md5 || patch.output.md5 !== current.release.output_md5)
      throw { code: 'bad_patch', message: 'patch file does not match the release index' };
    const res = await runWorker({ type: 'build', buf: fileBuf.slice(0), patch }, showProgress);
    const secs = ((performance.now() - t0) / 1000).toFixed(1);
    if (res.md5 !== current.release.output_md5) throw { code: 'output_mismatch', message: 'unexpected output MD5 ' + res.md5 };
    showProgress('done', 1);
    const name = current.release.name + '.dat';
    const url = URL.createObjectURL(new Blob([res.out], { type: 'application/octet-stream' }));
    const a = el('a', { href: url, download: name, class: 'button primary' }, 'Download ' + name);
    const box = $('result'); setStatus(box, 'ok', `Built in ${secs} s — output MD5 ${res.md5} matches the tested build.`);
    box.append(el('div', { class: 'actions' }, a));
    box.append(el('p', { class: 'muted small' }, 'Install it with Icom\'s normal SD-card firmware update procedure. Keep the official file as well — re-installing it returns the radio to stock firmware.'));
    a.click();
  } catch (e) {
    const msgs = {
      wrong_input: 'The input file is not the official file this release was made for.',
      output_mismatch: 'The result is NOT identical to the tested build, so it is not offered. Nothing was saved. Please report this (browser and version) in the GitHub repository.',
      decode_mismatch: 'The decoded firmware does not match the expected official image.',
      patch_mismatch: 'Applying the patch did not give the expected image.',
    };
    setStatus($('result'), 'err', 'Build failed — nothing to download', (msgs[e.code] ? msgs[e.code] + ' ' : '') + 'Details: ' + (e.message || e));
    $('progress').hidden = true;
  } finally {
    clearInterval(tick); busy = false; updateBuildButton();
  }
}

// ---- wiring --------------------------------------------------------------------------
const indexReady = loadIndex().catch((e) => {
  const tb = $('supported').tBodies[0];
  tb.replaceChildren(el('tr', {}, el('td', { colspan: '4' }, 'Could not load the release list: ' + e.message)));
});

$('translate').href = 'https://translate.google.com/translate?sl=en&tl=auto&u=' + encodeURIComponent(location.href.split('#')[0]);
$('file').addEventListener('change', (e) => onFile(e.target.files[0]));
const drop = $('drop');
['dragenter', 'dragover'].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.add('over'); }));
['dragleave', 'drop'].forEach((t) => drop.addEventListener(t, (e) => { e.preventDefault(); drop.classList.remove('over'); }));
drop.addEventListener('drop', (e) => onFile(e.dataTransfer.files[0]));
drop.addEventListener('keydown', (e) => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); $('file').click(); } });
window.addEventListener('dragover', (e) => e.preventDefault());
window.addEventListener('drop', (e) => e.preventDefault());
$('accept').addEventListener('change', updateBuildButton);
$('build').addEventListener('click', build);
