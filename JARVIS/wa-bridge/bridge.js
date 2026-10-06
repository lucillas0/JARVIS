/* Puente WhatsApp self-chat para JARVIS (Baileys, ESM).
 *
 * Uso:  node bridge.js   (o npm start)
 * 1ª vez: escanea el QR con WhatsApp > Dispositivos vinculados.
 * Escribe en tu chat "Mensajes a ti mismo" y JARVIS responde ahí.
 * (Arranca el puente ANTES de escribir; también pesca lo de ~15 min previos.)
 *
 * Solo responde a tu propio self-chat. Anti-bucle: ignora los ecos de sus
 * propios envíos y no re-responde a mensajes ya procesados (persistidos).
 */
import { makeWASocket, DisconnectReason, useMultiFileAuthState, fetchLatestBaileysVersion, downloadMediaMessage } from 'baileys';
import qrcode from 'qrcode-terminal';
import pino from 'pino';
import fs from 'node:fs';
import path from 'node:path';
import process from 'node:process';
import { fileURLToPath } from 'node:url';

const BRIDGE_DIR = path.dirname(fileURLToPath(import.meta.url));

const BRAIN_URL = process.env.WA_BRAIN_URL || 'http://127.0.0.1:8000';
const SESSION_DIR = process.env.WA_SESSION_DIR || path.join(BRIDGE_DIR, 'session');
const PREFIX = (process.env.WA_PREFIX || '').trim(); // ej. "!" → solo responde a "!hola"
const BRAIN_TIMEOUT_MS = parseInt(process.env.WA_BRAIN_TIMEOUT || '180000', 10);
const LOOKBACK_SEC = parseInt(process.env.WA_LOOKBACK_SEC || '900', 10); // pesca lo reciente aunque escribieras antes de arrancar
const DEBUG = (process.env.WA_DEBUG || '1') === '1'; // 1 = log de cada mensaje candidato
const MAX_MEDIA_BYTES = 20 * 1024 * 1024; // audios/fotos mayores se ignoran

// Propietario: variable WA_OWNER o owner.json (solo dígitos con prefijo).
// El puente SOLO funciona si la sesión vinculada es este número.
function loadOwner() {
  const env = (process.env.WA_OWNER || '').replace(/\D/g, '');
  if (env) return env;
  try {
    const raw = fs.readFileSync(path.join(BRIDGE_DIR, 'owner.json'), 'utf8');
    return (JSON.parse(raw).owner || '').replace(/\D/g, '');
  } catch {
    return '';
  }
}
const OWNER = loadOwner();
if (!OWNER) {
  console.error('Sin propietario: define WA_OWNER o crea owner.json con tu número. Me apago.');
  process.exit(3);
}

const sentIds = new Set(); // ids enviados por el puente (ecos a ignorar)
const MAX_SENT_IDS = 500;

// Ids ya respondidos (persisten entre arranques para no re-responder).
const PROCESSED_FILE = path.join(SESSION_DIR, 'processed.json');
let processedIds = new Set();
function loadProcessed() {
  try {
    const arr = JSON.parse(fs.readFileSync(PROCESSED_FILE, 'utf8'));
    if (Array.isArray(arr)) processedIds = new Set(arr.slice(-1000));
  } catch { /* primera vez */ }
}
function saveProcessed() {
  try {
    fs.mkdirSync(SESSION_DIR, { recursive: true });
    fs.writeFileSync(PROCESSED_FILE, JSON.stringify([...processedIds].slice(-1000)));
  } catch { /* noop */ }
}
loadProcessed();

const log = (...a) => console.log(new Date().toISOString().slice(11, 19), ...a);

function bareUser(jid) {
  if (!jid) return '';
  return String(jid).split('@')[0].split(':')[0];
}

function trackSent(id) {
  sentIds.add(id);
  if (sentIds.size > MAX_SENT_IDS) {
    const first = sentIds.values().next().value;
    sentIds.delete(first);
  }
}

async function askBrain(payload) {
  const ctrl = new AbortController();
  const t = setTimeout(() => ctrl.abort(), BRAIN_TIMEOUT_MS);
  try {
    const res = await fetch(`${BRAIN_URL}/wa`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
      signal: ctrl.signal,
    });
    if (!res.ok) throw new Error(`brain HTTP ${res.status}`);
    const data = await res.json();
    if (!data.ok) throw new Error(data.reason || 'brain dijo no-ok');
    return data;
  } finally {
    clearTimeout(t);
  }
}

function extractText(msg) {
  const m = msg?.message;
  if (!m) return '';
  return (m.conversation || m.extendedTextMessage?.text || m.imageMessage?.caption || '').trim();
}

function mediaKind(msg) {
  const m = msg?.message;
  if (!m) return '';
  if (m.audioMessage) return m.audioMessage.ptt ? 'voice' : 'audio';
  if (m.imageMessage) return 'image';
  return '';
}

async function downloadMedia(msg) {
  const buf = await downloadMediaMessage(msg, 'buffer', {});
  if (!buf || buf.length > MAX_MEDIA_BYTES) throw new Error('medio vacío o demasiado grande');
  return buf;
}

async function connect() {
  fs.mkdirSync(SESSION_DIR, { recursive: true });
  const { state, saveCreds } = await useMultiFileAuthState(SESSION_DIR);
  const { version } = await fetchLatestBaileysVersion().catch(() => ({ version: undefined }));

  const sock = makeWASocket({
    version,
    auth: state,
    browser: ['JARVIS', 'Chrome', '1.0'],
    syncFullHistory: false,
    shouldSyncHistoryMessage: () => false,
    markOnlineOnConnect: false,
    logger: pino({ level: 'warn' }), // sin PII en logs (nombres/avises)
  });
  sock.ev.on('creds.update', saveCreds);

  sock.ev.on('connection.update', (u) => {
    const { connection, lastDisconnect, qr } = u;
    if (qr) {
      log('Escanea este QR con WhatsApp > Dispositivos vinculados:');
      qrcode.generate(qr, { small: true });
    }
    if (connection === 'open') {
      const meBare = bareUser(sock.user?.id);
      if (meBare !== OWNER) {
        log(`Sesión vinculada al número ${meBare}, pero el propietario es ${OWNER}. Me apago sin responder a nadie.`);
        log(`Si cambiaste de número, actualiza owner.json (o WA_OWNER) y borra ${SESSION_DIR}.`);
        process.exit(3);
      }
      log('Propietario verificado:', OWNER, '| self-chat activo.');
    }
    if (connection === 'close') {
      const code = lastDisconnect?.error?.output?.statusCode;
      const loggedOut = code === DisconnectReason.loggedOut;
      log('Conexión cerrada.', loggedOut ? 'Sesión cerrada.' : 'Reintentando en 5s…');
      if (loggedOut) {
        log(`Borra la carpeta ${SESSION_DIR} y reinicia para re-emparejar.`);
        process.exit(2);
      }
      setTimeout(connect, 5000);
    }
  });

  sock.ev.on('messages.upsert', async ({ type, messages }) => {
    if (type !== 'notify' && type !== 'append') return;
    const meBare = bareUser(sock.user?.id);
    const meLid = bareUser(sock.user?.lid);
    if (meBare !== OWNER) return; // segunda capa: solo el propietario
    const nowSec = Math.floor(Date.now() / 1000);
    for (const msg of messages || []) {
      try {
        const key = msg.key || {};
        const remote = key.remoteJid || '';
        const alt = msg.remoteJidAlt || '';
        const ts = Number(msg.messageTimestamp || 0);
        const text = extractText(msg);
        const kind = mediaKind(msg);
        const isSelf = bareUser(remote) === meBare || bareUser(remote) === meLid ||
          (alt && (bareUser(alt) === meBare || bareUser(alt) === meLid));
        if (DEBUG) {
          log(`DBG type=${type} kind=${kind || 'text'} remote=${remote} alt=${alt || '-'} fromMe=${!!key.fromMe} id=${key.id || '-'} ts=${ts} self=${isSelf} text=${JSON.stringify(text.slice(0, 40))}`);
        }
        if (key.id && sentIds.has(key.id)) { sentIds.delete(key.id); continue; } // eco propio
        if (key.remoteJid === 'status@broadcast') continue;
        if (!isSelf) continue; // solo self-chat, nunca chats ajenos
        if (key.id && processedIds.has(key.id)) continue; // ya respondido antes
        if (ts && ts < nowSec - LOOKBACK_SEC) continue; // más viejo que la ventana
        if (!text && !kind) continue;
        if (PREFIX && !text.startsWith(PREFIX)) continue;
        const clean = PREFIX ? text.slice(PREFIX.length).trim() : text;

        // Descargar medio si lo hay (audio/foto)
        let payload = { message: clean, sender: bareUser(remote) || meBare };
        if (kind === 'voice' || kind === 'audio') {
          try {
            const buf = await downloadMedia(msg);
            payload = { message: '', sender: payload.sender, audio_b64: buf.toString('base64'), voice_reply: true };
            log('→ nota de voz (%d KB).', Math.round(buf.length / 1024));
          } catch (e) {
            log('media audio ignorado:', e.message);
            continue;
          }
        } else if (kind === 'image') {
          try {
            const buf = await downloadMedia(msg);
            payload = { message: clean, sender: payload.sender, image_b64: buf.toString('base64') };
            log('→ foto (%d KB) caption=%s', Math.round(buf.length / 1024), JSON.stringify(clean.slice(0, 60)));
          } catch (e) {
            log('media imagen ignorada:', e.message);
            continue;
          }
        } else {
          if (!clean) continue;
          log('→', clean.slice(0, 80));
        }

        if (key.id) { processedIds.add(key.id); saveProcessed(); }
        // Barómetro: <4s directo; si piensa, avisa y cada 25s informa.
        const TEXT_ACK = 'Ahora voy, señor, voy a investigarlo.';
        const PROGRESS = 'Sigo en proceso, señor.';
        let data = null;
        let slowTimer = null;
        let progTimer = null;
        try {
          const brainP = askBrain(payload);
          slowTimer = setTimeout(() => {
            sock.sendMessage(remote, { text: TEXT_ACK }).catch(() => {});
            progTimer = setInterval(() => {
              sock.sendMessage(remote, { text: PROGRESS }).catch(() => {});
            }, 25000);
          }, 4000);
          data = await brainP;
        } catch (e) {
          log('brain error:', e.message);
        } finally {
          if (slowTimer) clearTimeout(slowTimer);
          if (progTimer) clearInterval(progTimer);
        }
        const reply = String(data?.reply || '').trim().replace(/\[\[VOICE_(JOIN|LEAVE)\]\]/g, '').trim()
          || 'Disculpe, señor, ahora mismo no alcanzo al cerebro. Pruebe en un minuto.';
        // Si te habló por voz, responde por voz; si no, texto (con fallback a texto)
        if (data?.audio_b64) {
          try {
            const sent = await sock.sendMessage(remote, {
              audio: Buffer.from(data.audio_b64, 'base64'),
              mimetype: 'audio/ogg; codecs=opus',
              ptt: true,
            });
            if (sent?.key?.id) trackSent(sent.key.id);
            log('← nota de voz enviada.');
            continue;
          } catch (e) {
            log('envío de voz falló, mando texto:', e.message);
          }
        }
        const sent = await sock.sendMessage(remote, { text: reply.slice(0, 60000) });
        if (sent?.key?.id) trackSent(sent.key.id);
        log('← respuesta enviada (%d chars).', reply.length);
      } catch (e) {
        log('msg error:', e.message);
      }
    }
  });

  return sock;
}

connect().catch((e) => {
  console.error('Arranque fallido:', e.message);
  process.exit(1);
});
