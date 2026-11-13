// MD5 (RFC 1321), incremental, with state cloning. Written for this project; MIT licence
// (see the repository LICENSE). No dependencies.

const S = [7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22, 7, 12, 17, 22,
  5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20, 5, 9, 14, 20,
  4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23, 4, 11, 16, 23,
  6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21, 6, 10, 15, 21];
const K = new Int32Array(64);
for (let i = 0; i < 64; i++) K[i] = Math.floor(Math.abs(Math.sin(i + 1)) * 4294967296) | 0;

const W = new Int32Array(16);

function block(st, b, o) {
  for (let i = 0; i < 16; i++, o += 4) W[i] = b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24);
  let a = st[0], bb = st[1], c = st[2], d = st[3];
  for (let i = 0; i < 64; i++) {
    let f, g;
    if (i < 16) { f = (bb & c) | (~bb & d); g = i; }
    else if (i < 32) { f = (d & bb) | (~d & c); g = (5 * i + 1) & 15; }
    else if (i < 48) { f = bb ^ c ^ d; g = (3 * i + 5) & 15; }
    else { f = c ^ (bb | ~d); g = (7 * i) & 15; }
    const t = d; d = c; c = bb;
    const x = (a + f + K[i] + W[g]) | 0;
    bb = (bb + ((x << S[i]) | (x >>> (32 - S[i])))) | 0;
    a = t;
  }
  st[0] = (st[0] + a) | 0; st[1] = (st[1] + bb) | 0; st[2] = (st[2] + c) | 0; st[3] = (st[3] + d) | 0;
}

export class Md5 {
  constructor() {
    this.st = Int32Array.of(0x67452301, 0xefcdab89 | 0, 0x98badcfe | 0, 0x10325476);
    this.buf = new Uint8Array(64); this.n = 0; this.len = 0;
  }
  clone() {
    const m = new Md5(); m.st.set(this.st); m.buf.set(this.buf); m.n = this.n; m.len = this.len; return m;
  }
  update(data) {
    let o = 0; const L = data.length; this.len += L;
    if (this.n) {
      const take = Math.min(64 - this.n, L);
      this.buf.set(data.subarray(0, take), this.n); this.n += take; o = take;
      if (this.n < 64) return this;
      block(this.st, this.buf, 0); this.n = 0;
    }
    for (; o + 64 <= L; o += 64) block(this.st, data, o);
    if (o < L) { this.buf.set(data.subarray(o), 0); this.n = L - o; }
    return this;
  }
  digest() {
    const m = this.clone(); const bits = m.len * 8;
    const pad = new Uint8Array(((m.n < 56) ? 56 : 120) - m.n + 8); pad[0] = 0x80;
    const lo = bits >>> 0, hi = Math.floor(bits / 4294967296) >>> 0, p = pad.length - 8;
    for (let i = 0; i < 4; i++) { pad[p + i] = (lo >>> (8 * i)) & 255; pad[p + 4 + i] = (hi >>> (8 * i)) & 255; }
    m.update(pad);
    const out = new Uint8Array(16);
    for (let i = 0; i < 16; i++) out[i] = (m.st[i >> 2] >>> (8 * (i & 3))) & 255;
    return out;
  }
  hexdigest() { return toHex(this.digest()); }
}

export function toHex(b) {
  let s = ''; for (let i = 0; i < b.length; i++) s += (b[i] < 16 ? '0' : '') + b[i].toString(16); return s;
}

export function md5hex(data) { return new Md5().update(data).hexdigest(); }
