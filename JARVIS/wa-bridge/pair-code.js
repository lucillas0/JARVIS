/* Emparejado por CÓDIGO v2: espera conexión antes de pedir el código. */
import { makeWASocket, useMultiFileAuthState, fetchLatestBaileysVersion, DisconnectReason } from 'baileys';
import pino from 'pino';
import path from 'node:path';
import process from 'node:process';

const SESSION = process.env.WA_SESSION_DIR ||
  path.join(process.env.APPDATA || '', 'JARVIS', 'brain-data', 'wa-session');
// Sin numero por defecto. Antes ponia el del dueño hardcodeado, y asi su
// telefono de verdad quedaba escrito en el repositorio para todo el que lo
// mirara. Ahora se lee de owner.json (que ya no se versiona) o del entorno, y
// si no esta se dice en vez de inventarse uno.
const NUMBER = (process.env.WA_NUMBER || '').replace(/\D/g, '');
if (!NUMBER) {
  console.error(
    'Falta el numero del propietario.\n' +
    '  Ponlo en uno de estos sitios:\n' +
    '    · WA_NUMBER=34643351642 en el entorno, o\n' +
    '    · owner.json junto a este archivo:  { "owner": "34643351642" }\n' +
    'El numero va con prefijo y solo digitos (pais sin +).');
  process.exit(1);
}

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
