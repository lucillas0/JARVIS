/* Emparejado por CÓDIGO v2: espera conexión antes de pedir el código. */
import { makeWASocket, useMultiFileAuthState, fetchLatestBaileysVersion, DisconnectReason } from 'baileys';
import pino from 'pino';
import path from 'node:path';
import process from 'node:process';

const SESSION = process.env.WA_SESSION_DIR ||
  path.join(process.env.APPDATA || '', 'JARVIS', 'brain-data', 'wa-session');
const NUMBER = (process.env.WA_NUMBER || '34643351642').replace(/\D/g, '');

const { state, saveCreds } = await useMultiFileAuthState(SESSION);
let version;
try { ({ version } = await fetchLatestBaileysVersion()); } catch { version = undefined; }
const sock = makeWASocket({
  version, auth: state, browser: ['JARVIS', 'Chrome', '1.0'],
  syncFullHistory: false, markOnlineOnConnect: false,
  shouldSyncHistoryMessage: () => false,
  logger: pino({ level: 'warn' }),
});
sock.ev.on('creds.update', saveCreds);
let asked = false;
sock.ev.on('connection.update', async (u) => {
  console.log('CONN_STATE:' + u.connection);
  if (u.connection === 'open') console.log('LOGIN_OK');
  if (!state.creds.registered && !asked && u.connection !== 'close') {
    asked = true;
    try {
      const code = await sock.requestPairingCode(NUMBER);
      console.log('PAIRING_CODE:' + code);
    } catch (e) {
      console.log('PAIRING_ERROR:' + String((e && e.message) || e).slice(0, 200));
    }
  }
  if (u.connection === 'close') {
    const code = u.lastDisconnect?.error?.output?.statusCode;
    console.log('CONN_CLOSED:' + code);
  }
});
await new Promise(() => {});
