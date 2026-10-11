const net = require('net');
const tls = require('tls');
const crypto = require('crypto');
const { URL } = require('url');
class CdpSocket {
  constructor(address) {
    this.url = new URL(address);
    this.buffer = Buffer.alloc(0);
    this.callbacks = new Map();
    this.nextId = 1;
    this.maskSeed = crypto.randomBytes(4);
    this.closed = false;
  }
  async connect() {
    const secure = this.url.protocol === 'wss:';
    const socket = secure
      ? tls.connect({ host: this.url.hostname, port: Number(this.url.port || 443), servername: this.url.hostname })
      : net.connect(Number(this.url.port || 80), this.url.hostname);
    this.socket = socket;
    await new Promise((resolve, reject) => {
      socket.once(secure ? 'secureConnect' : 'connect', resolve);
      socket.once('error', reject);
    });
    const key = crypto.randomBytes(16).toString('base64');
    socket.write(`GET ${this.url.pathname}${this.url.search} HTTP/1.1\r\nHost: ${this.url.host}\r\nUpgrade: websocket\r\nConnection: Upgrade\r\nSec-WebSocket-Key: ${key}\r\nSec-WebSocket-Version: 13\r\n\r\n`);
    await new Promise((resolve, reject) => {
      let handshake = Buffer.alloc(0);
      const onData = (chunk) => {
        handshake = Buffer.concat([handshake, chunk]);
        const end = handshake.indexOf('\r\n\r\n');
        if (end < 0) return;
        socket.off('data', onData);
        const head = handshake.subarray(0, end).toString('latin1');
        if (!/^HTTP\/1\.1 101\b/.test(head)) return reject(new Error(`CDP websocket upgrade failed: ${head}`));
        this.buffer = Buffer.concat([this.buffer, handshake.subarray(end + 4)]);
        resolve();
      };
      socket.on('data', onData);
      socket.once('error', reject);
    });
    socket.on('data', (chunk) => { this.buffer = Buffer.concat([this.buffer, chunk]); this.#consume(); });
    socket.on('close', () => { this.closed = true; for (const { reject } of this.callbacks.values()) reject(new Error('CDP socket closed')); this.callbacks.clear(); });
  }
  #consume() {
    while (this.buffer.length >= 2) {
      const first = this.buffer[0], second = this.buffer[1];
      let length = second & 0x7f, offset = 2;
      if (length === 126) { if (this.buffer.length < 4) return; length = this.buffer.readUInt16BE(2); offset = 4; }
      else if (length === 127) { if (this.buffer.length < 10) return; const big = this.buffer.readBigUInt64BE(2); if (big > BigInt(Number.MAX_SAFE_INTEGER)) throw new Error('CDP frame too large'); length = Number(big); offset = 10; }
      const masked = !!(second & 0x80);
      if (masked) offset += 4;
      if (this.buffer.length < offset + length) return;
      let data = this.buffer.subarray(offset, offset + length);
      if (masked) { const mask = this.buffer.subarray(offset - 4, offset); data = Buffer.from(data); for (let i = 0; i < data.length; i++) data[i] ^= mask[i % 4]; }
      this.buffer = this.buffer.subarray(offset + length);
      const opcode = first & 0x0f;
      if (opcode === 0x8) { this.#frame(0x8, data); this.socket.end(); this.closed = true; return; }
      if (opcode === 0x9) { this.#frame(0xA, data); continue; }
      if (opcode !== 0x1) continue;
      const message = JSON.parse(data.toString('utf8'));
      if (!message.id || !this.callbacks.has(message.id)) continue;
      const { resolve, reject, timer } = this.callbacks.get(message.id);
      clearTimeout(timer);
      this.callbacks.delete(message.id);
      if (message.error) reject(new Error(message.error.message));
      else if (message.result?.exceptionDetails) reject(new Error(message.result.exceptionDetails.text));
      else resolve(message.result?.result?.value);
    }
  }
  #frame(opcode, payload) {
    const data = Buffer.from(payload || []), n = data.length;
    let header;
    if (n < 126) { header = Buffer.alloc(2); header[1] = 0x80 | n; }
    else if (n < 65536) { header = Buffer.alloc(4); header[1] = 0x80 | 126; header.writeUInt16BE(n, 2); }
    else { header = Buffer.alloc(10); header[1] = 0x80 | 127; header.writeBigUInt64BE(BigInt(n), 2); }
    header[0] = 0x80 | opcode;
    const mask = crypto.randomBytes(4), masked = Buffer.alloc(n);
    for (let i = 0; i < n; i++) masked[i] = data[i] ^ mask[i % 4];
    this.socket.write(Buffer.concat([header, mask, masked]));
  }
  send(method, params = {}) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => { this.callbacks.delete(id); reject(new Error(`CDP timeout: ${method}`)); }, 8000);
      this.callbacks.set(id, { resolve, reject, timer });
      this.#frame(0x1, Buffer.from(JSON.stringify({ id, method, params })));
    });
  }
  close() { if (this.socket && !this.closed) { this.#frame(0x8, Buffer.alloc(0)); this.socket.end(); } }
}
module.exports = { CdpSocket };
