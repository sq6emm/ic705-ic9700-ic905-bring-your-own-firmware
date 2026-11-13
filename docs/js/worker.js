// Web Worker: all heavy work (MD5, decode, patch, LZSS compress, re-seal) runs here.
// Nothing is sent anywhere; the result goes back to the page only.
import { md5hex } from './md5.js';
import { buildPatched } from './patch.js';

self.onmessage = (ev) => {
  const msg = ev.data;
  try {
    if (msg.type === 'md5') {
      const t0 = performance.now();
      const md5 = md5hex(new Uint8Array(msg.buf));
      self.postMessage({ type: 'md5', id: msg.id, md5, ms: performance.now() - t0 });
    } else if (msg.type === 'build') {
      const t0 = performance.now();
      let last = 0;
      const progress = (stage, frac) => {
        const now = performance.now();
        if (stage !== 'compress' || now - last > 100 || frac === 1) {
          last = now; self.postMessage({ type: 'progress', id: msg.id, stage, frac });
        }
      };
      const { out, md5 } = buildPatched(new Uint8Array(msg.buf), msg.patch, progress);
      self.postMessage({ type: 'built', id: msg.id, md5, ms: performance.now() - t0, out: out.buffer }, [out.buffer]);
    }
  } catch (e) {
    self.postMessage({ type: 'error', id: msg.id, code: e.code || 'internal', message: String(e.message || e), details: e.details || {} });
  }
};
