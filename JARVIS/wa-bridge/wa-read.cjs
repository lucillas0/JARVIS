/* Lector WhatsApp SOLO LECTURA (ritual JARVIS).
 * No envía nada, no marca leído, no responde. Imprime JSON y sale.
 * Sesión: WA_SESSION_DIR (persistente en brain-data/wa-session).
 */
const { makeWASocket, useMultiFileAuthState, fetchLatestBaileysVersion } = require('baileys');
const pino = require('pino');
const path = require('path');

const SESSION = process.env.WA_SESSION_DIR ||
  path.join(process.env.APPDATA || '', 'JARVIS', 'brain-data', 'wa-session');
const SYNC = process.env.WA_SYNC === '1';
const WINDOW_MS = parseInt(process.env.WA_READ_WINDOW_MS || (SYNC ? '45000' : '18000'), 10);

function textOf(m) {
  const mm = (m && m.message) || {};
  return (mm.conversation || (mm.extendedTextMessage && mm.extendedTextMessage.text) ||
    (mm.imageMessage && mm.imageMessage.caption) || '').trim();
}

async function main() {
  const { state, saveCreds } = await useMultiFileAuthState(SESSION);
  let version;
  try { ({ version } = await fetchLatestBaileysVersion()); } catch { version = undefined; }
  const sock = makeWASocket({
    version, auth: state, browser: ['JARVIS', 'Chrome', '1.0'],
    syncFullHistory: SYNC, markOnlineOnConnect: false,
    shouldSyncHistoryMessage: () => SYNC,
    logger: pino({ level: 'warn' }),
  });
  sock.ev.on('creds.update', saveCreds);
  const chats = new Map(); // id -> {name, unread}
  const last = new Map();  // id -> {text, ts, fromMe}
  const upsertChat = (c) => {
    if (!c || !c.id || c.id === 'status@broadcast') return;
    const prev = chats.get(c.id) || {};
    chats.set(c.id, {
      name: c.name || prev.name || null,
      unread: (c.unreadCount == null ? (prev.unread || 0) : c.unreadCount),
    });
  };
  sock.ev.on('messaging-history.set', ({ chats: cs }) => (cs || []).forEach(upsertChat));
  sock.ev.on('chats.upsert', (cs) => (cs || []).forEach(upsertChat));
  sock.ev.on('chats.update', (cs) => (cs || []).forEach((u) => {
    if (!u || !u.id) return;
    const prev = chats.get(u.id) || {};
    chats.set(u.id, {
      name: (u.name !== undefined ? u.name : prev.name) || null,
      unread: (u.unreadCount == null ? (prev.unread || 0) : u.unreadCount),
    });
  }));
  sock.ev.on('messages.upsert', ({ type, messages }) => {
    if (type !== 'notify') return;
    for (const m of messages || []) {
      const id = m.key && m.key.remoteJid;
      if (!id || id === 'status@broadcast') continue;
      const t = textOf(m);
      if (!t) continue;
      const prev = last.get(id);
      const ts = Number(m.messageTimestamp || 0);
      if (!prev || ts >= (prev.ts || 0)) {
        last.set(id, { text: t.slice(0, 160), ts, fromMe: !!(m.key && m.key.fromMe) });
      }
    }
  });
  await new Promise((resolve) => {
    const to = setTimeout(resolve, WINDOW_MS);
    sock.ev.on('connection.update', (u) => {
      if (u.connection === 'close') { clearTimeout(to); resolve(); }
    });
  });
  const out = [];
  for (const [id, c] of chats) {
    const l = last.get(id) || {};
    out.push({ id, name: c.name || null, unread: c.unread || 0,
               last_text: l.text || null, last_ts: l.ts || 0,
               last_from_me: !!l.fromMe });
  }
  out.sort((a, b) => ((b.unread || 0) - (a.unread || 0)) || ((b.last_ts || 0) - (a.last_ts || 0)));
  console.log(JSON.stringify({ ok: true, chats: out.slice(0, 25), total: out.length }));
  try { sock.end(undefined); } catch {}
  process.exit(0);
}
main().catch((e) => {
  try { console.log(JSON.stringify({ ok: false, error: String((e && e.message) || e).slice(0, 200) })); } catch {}
  process.exit(1);
});
