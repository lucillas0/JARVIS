const { app, BrowserWindow, ipcMain, Tray, Menu, shell, screen, globalShortcut, utilityProcess } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn, spawnSync } = require('child_process');
const crypto = require('crypto');
const Database = require('better-sqlite3');
const { readJarvisConversation } = require('./jarvis-history');
const { notchHitTest, roundedRectRows } = require('./notch-geometry');

// En desarrollo, los recursos viven junto al proyecto; empaquetado, en resources/.
const ROOT_DIR = app.isPackaged ? process.resourcesPath : path.join(__dirname, '..');
const BRAIN_DIR = app.isPackaged ? path.join(process.resourcesPath, 'brain') : path.join(ROOT_DIR, 'brain');
let ENV_FILE = path.join(BRAIN_DIR, '.env');
const ASSETS_DIR = app.isPackaged ? path.join(process.resourcesPath, 'assets') : path.join(ROOT_DIR, 'assets');

// Los datos del cerebro se guardan fuera de la instalación para sobrevivir reinstalaciones.
let notchWindow = null;
let notchHidden = false;
let notchExpanded = false;
let notchContentHeight = 360;
let notchHovered = false;
let notchPointerTimer = null;
let globalShortcuts = {};
let bootstrapPromise = null;
let tray = null;

function brainDataDir() {
  let ud = null;
  try { ud = app.getPath('userData'); } catch { /* app no listo aún */ }
  return ud ? path.join(ud, 'brain-data') : BRAIN_DIR;
}
function brainDatabasePath() {
  const candidates = [path.join(brainDataDir(), 'jarvis.db'), path.join(BRAIN_DIR, 'db', 'jarvis.db')];
  return candidates.find((candidate) => fs.existsSync(candidate)) || candidates[0];
}
function isNotchSender(event) {
  return !!notchWindow && !notchWindow.isDestroyed() && event.sender === notchWindow.webContents;
}
function notifyNotch(channel, payload) {
  if (!notchWindow || notchWindow.isDestroyed() || notchWindow.webContents.isDestroyed()) return;
  notchWindow.webContents.send(channel, payload);
}
function broadcastConversationChanged() {
  notifyNotch('notch:conversation-changed');
}
function openNotchApp(identifier) {
  const id = String(identifier || '').trim().toLowerCase();
  if (!id) return false;
  const shortcuts = {
    freebuff: ['freebuff'], agent_freebuff: ['freebuff'], agent_opencode: ['opencode'],
    integration_claude: ['code'], agent_cursor: ['cursor'], agent_codex: ['codex'],
    agent_antigravity: ['antigravity'],
  };
  const terms = shortcuts[id] || [id.replace(/^agent_/, '').replace(/^integration_/, '')];
  let apps = [];
  try { apps = listApps() || []; } catch { /* app index unavailable */ }
  const match = apps.find((app) => terms.some((term) =>
    String(app.id || '').toLowerCase() === term || String(app.name || '').toLowerCase() === term ||
    String(app.name || '').toLowerCase().includes(term)));
  if (match?.path) { void shell.openPath(String(match.path)); return true; }
  if (process.platform === 'win32') {
    try { spawn('cmd', ['/c', 'start', '', terms[0]], { detached: true, windowsHide: true, stdio: 'ignore' }).unref(); return true; }
    catch { /* app is not installed */ }
  }
  return false;
}
function startNewNotchConversation() {
  try {
    // Keep JARVIS as the one source of truth; archive for Coucou's local session only.
    const rows = readJarvisConversation(brainDatabasePath(), 300);
    db.prepare('INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value')
      .run('coucou_archived_conversations', JSON.stringify({ label: new Date().toISOString(), rows }));
    return true;
  } catch (error) {
    console.error('[JARVIS] No pude archivar el contexto del notch:', error.message);
    return false;
  }
}
function sendNotchMessage(message, context) {
  if (typeof message !== 'string' || !message.trim()) return Promise.reject(new Error('Escribe un mensaje antes de enviarlo.'));
  if (context?.kind === 'file' && (typeof context.content !== 'string' || !context.content.trim())) {
    return Promise.reject(new Error('No se pudo extraer contenido útil del archivo adjunto.'));
  }
  if (typeof WebSocket === 'undefined') return Promise.reject(new Error('WebSocket no está disponible en este proceso.'));
  const id = `notch-${Date.now()}-${crypto.randomUUID()}`;
  let text = message.trim();
  if (context?.kind === 'file') {
    const content = context.content.slice(0, 24000);
    text += `\n[Archivo adjunto: ${String(context.name || 'documento').slice(0, 200)}]\n${content}${context.truncated || context.content.length > 24000 ? '\n[contenido truncado]' : ''}`;
  }
  return new Promise((resolve, reject) => {
    const socket = new WebSocket('ws://127.0.0.1:8000/ws');
    let settled = false;
    const timer = setTimeout(() => finish(new Error('JARVIS tardó demasiado en responder. Comprueba el historial antes de reintentar.')), 180000);
    const finish = (error, result) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try { socket.close(); } catch { /* noop */ }
      if (error) reject(error); else resolve(result);
    };
    socket.addEventListener('open', () => {
      try { socket.send(JSON.stringify({ type: 'user_message', message: text, input_mode: 'text', speak: false, id, request_id: id })); }
      catch (error) { finish(error); }
    }, { once: true });
    socket.addEventListener('message', (event) => {
      let payload;
      try { payload = JSON.parse(event.data); } catch { return; }
      if (payload.type !== 'assistant_reply' || payload.request_id !== id || payload.persisted === false) return;
      finish(null, { text: String(payload.text || '') });
      broadcastConversationChanged();
    });
    socket.addEventListener('error', () => finish(new Error('No se pudo conectar con el Brain de JARVIS.')), { once: true });
    socket.addEventListener('close', () => { if (!settled) finish(new Error('Se perdió la conexión con el Brain de JARVIS.')); }, { once: true });
  });
}

function venvDir() { return path.join(brainDataDir(), 'venv'); }
function venvPy() {
  return path.join(venvDir(), process.platform === 'win32' ? 'Scripts' : 'bin',
    process.platform === 'win32' ? 'python.exe' : 'python3');
}
function venvOk() { return path.join(venvDir(), 'setup.ok'); }
function requirementsFile() {
  return path.join(BRAIN_DIR, process.platform === 'win32' ? 'requirements.txt' : 'requirements-desktop.txt');
}
function requirementsHash() {
  return crypto.createHash('sha256').update(fs.readFileSync(requirementsFile())).digest('hex');
}
function pythonLaunchCandidates() { return process.platform === 'win32' ? ['py', 'python', 'python3'] : ['python3', 'python']; }
function resolveVenvHome(home) {
  const candidates = process.platform === 'win32' ? ['python.exe'] : ['python3', 'python3.12', 'python3.11', 'python'];
  return candidates.some((name) => fs.existsSync(path.join(home, name)));
}
function hasApiKeyValues(env) { return API_KEY_NAMES.slice(0, 3).some((key) => env[key]); }
function legacyKeyPaths() {
  const paths = [path.join(BRAIN_DIR, '.env')];
  if (process.env.APPDATA) paths.push(path.join(process.env.APPDATA, 'JARVIS', 'keys.env'));
  return paths;
}

let db;
let hudWindow = null;
let orbWindow = null;
let wizardWindow = null;
let splashWindow = null;
let overlayWindow = null;
let overlayPendingShow = false;
let brainProcess = null;
let perfInterval = null;
let lastCpuInfo = null;
let overlayToggleLock = false;
let waBridgeProc = null;
let waBridgeTimer = null;
let waBridgeTries = 0;
let waQuitting = false;

// ------------------------------------------------------------------ //
// Base de datos (historial) — sección 12.1
// ------------------------------------------------------------------ //
function initDb() {
  const userPath = app.getPath('userData');
  if (!fs.existsSync(userPath)) fs.mkdirSync(userPath, { recursive: true });
  db = new Database(path.join(userPath, 'jarvis.db'));
  db.exec(`
    CREATE TABLE IF NOT EXISTS messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      role TEXT NOT NULL,
      content TEXT NOT NULL,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
  `);
}

// ------------------------------------------------------------------ //
// Config en .env
// ------------------------------------------------------------------ //
function userDataPath() {
  try { return app.getPath('userData'); } catch { return null; }
}

// Copia persistente de las claves fuera de la carpeta de instalación.
// Sobrevive a desinstalar/reinstalar (los datos de usuario no se borran).
function persistEnvPath() {
  const ud = userDataPath();
  return ud ? path.join(ud, 'keys.env') : null;
}

function setupWizardSkippedPath() {
  const ud = userDataPath();
  return ud ? path.join(ud, 'setup-wizard-skipped') : null;
}

function setupWizardSkipped() {
  const marker = setupWizardSkippedPath();
  return Boolean(marker && fs.existsSync(marker));
}

function markSetupWizardSkipped() {
  const marker = setupWizardSkippedPath();
  if (!marker) throw new Error('No se pudo localizar la carpeta de datos de usuario.');
  fs.mkdirSync(path.dirname(marker), { recursive: true });
  fs.writeFileSync(marker, '1\n', 'utf8');
}

function clearSetupWizardSkipped() {
  const marker = setupWizardSkippedPath();
  if (marker && fs.existsSync(marker)) fs.unlinkSync(marker);
}

function migrateUserConfig() {
  const ud = userDataPath();
  if (!ud) return;
  const target = path.join(ud, 'brain.env');
  const source = path.join(BRAIN_DIR, '.env');
  fs.mkdirSync(ud, { recursive: true });
  if (!fs.existsSync(target) && fs.existsSync(source)) fs.copyFileSync(source, target);
  ENV_FILE = target;
}

function readEnvFile(file) {
  if (!file || !fs.existsSync(file)) return {};
  const out = {};
  for (const line of fs.readFileSync(file, 'utf8').split(/\r?\n/)) {
    const m = line.match(/^\s*([A-Z0-9_]+)\s*=\s*(.*)\s*$/);
    if (m) out[m[1]] = m[2].trim().replace(/^"(.*)"$/, '$1');
  }
  return out;
}

function readEnv() {
  return readEnvFile(ENV_FILE);
}

const API_KEY_NAMES = ['GROQ_API_KEY', 'GEMINI_API_KEY', 'OPENROUTER_API_KEY', 'WEATHER_API_KEY', 'DISCORD_BOT_TOKEN', 'DISCORD_OWNER_ID', 'VBOX_GUEST_USER', 'VBOX_GUEST_PASSWORD', 'CAMPUS_USER', 'CAMPUS_PASSWORD', 'RASPBERRY_HOST', 'RASPBERRY_PORT', 'RASPBERRY_USER', 'RASPBERRY_PASSWORD', 'NEXTCLOUD_URL', 'NEXTCLOUD_USER', 'NEXTCLOUD_PASSWORD'];

function hasAnyApiKey() {
  const persisted = persistEnvPath() && readEnvFile(persistEnvPath());
  if (hasApiKeyValues(persisted || {})) return true;
  return legacyKeyPaths().some((file) => hasApiKeyValues(readEnvFile(file)));
}

function persistKeys(env) {
  const file = persistEnvPath();
  if (!file) return;
  fs.mkdirSync(path.dirname(file), { recursive: true });
  const keys = {};
  for (const k of API_KEY_NAMES) {
    if (env[k]) keys[k] = String(env[k]).trim();
  }
  fs.writeFileSync(file, Object.entries(keys).map(([k, v]) => `${k}=${v}`).join('\n') + '\n', 'utf8');
}

// Tras reinstalar, el brain/.env viene como viene de fábrica: traemos las claves
// persistidas y las volcamos al .env que lee el brain.
function restorePersistedKeys() {
  const persisted = persistEnvPath();
  if (!persisted) return;
  const keys = readEnvFile(persisted);
  const env = readEnv();
  for (const source of legacyKeyPaths()) {
    const legacy = readEnvFile(source);
    for (const key of API_KEY_NAMES) {
      if (!keys[key] && legacy[key]) keys[key] = legacy[key];
    }
  }
  for (const key of API_KEY_NAMES) {
    if (!keys[key] && env[key]) keys[key] = env[key];
    if (keys[key] && !env[key]) env[key] = keys[key];
  }
  if (Object.keys(keys).length) {
    fs.mkdirSync(path.dirname(persisted), { recursive: true });
    fs.writeFileSync(persisted, Object.entries(keys).map(([key, value]) => `${key}=${value}`).join('\n') + '\n', 'utf8');
  }
  if (Object.keys(env).length) {
    fs.mkdirSync(path.dirname(ENV_FILE), { recursive: true });
    fs.writeFileSync(ENV_FILE, Object.entries(env).map(([key, value]) => `${key}=${value}`).join('\n') + '\n', 'utf8');
  }
}

// ------------------------------------------------------------------ //
// Brain (proceso Python en segundo plano)
// ------------------------------------------------------------------ //
function venvUsable() {
  const venvPython = venvPy();
  const cfg = path.join(venvDir(), 'pyvenv.cfg');
  if (!fs.existsSync(venvPython) || !fs.existsSync(cfg)) return false;
  const m = fs.readFileSync(cfg, 'utf8').match(/^home\s*=\s*(.+)$/m);
  if (!m) return false;
  const home = m[1].trim();
  return resolveVenvHome(home);
}

function startBrain() {
  if (brainProcess) return;
  try { fs.mkdirSync(brainDataDir(), { recursive: true }); } catch (err) {
    console.error('[JARVIS] No pude crear el directorio de datos:', err.message);
    return;
  }
  const venvPython = venvPy();
  const python = venvUsable() ? venvPython : (process.platform === 'win32' ? 'python' : 'python3');
  const args = ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8000'];
  try {
    const logOut = fs.openSync(path.join(brainDataDir(), 'brain.out.log'), 'a');
    const logErr = fs.openSync(path.join(brainDataDir(), 'brain.err.log'), 'a');
    brainProcess = spawn(python, args, {
      cwd: BRAIN_DIR,      env: { ...process.env, JARVIS_DATA_DIR: brainDataDir(), JARVIS_USER_DATA_DIR: userDataPath() || brainDataDir(), JARVIS_ENV_FILE: ENV_FILE },
      stdio: ['ignore', logOut, logErr],
      windowsHide: true,
    });
    fs.closeSync(logOut);
    fs.closeSync(logErr);
    brainProcess.unref();
    brainProcess.on('error', (err) => {
      console.error('[JARVIS] No pude lanzar el brain:', err.message);
    });
    console.log('[JARVIS] Brain lanzado (' + python + ').');
  } catch (err) {
    console.error('[JARVIS] No pude lanzar el brain:', err);
  }
}

// ------------------------------------------------------------------ //
// Instalación del venv del brain al primer arranque (pantalla de carga)
// ------------------------------------------------------------------ //
const VENV_OK = venvOk;
let setupResolve = null;

function venvReady() {
  if (!venvUsable()) return false;
  try {
    return fs.readFileSync(VENV_OK(), 'utf8').trim() === requirementsHash();
  } catch {
    return false;
  }
}

function findPython() {
  const tryResolve = (cmd) => {
    try {
      const res = spawnSync(cmd, ['-c', 'import sys; print(sys.executable)'], {
        encoding: 'utf8', timeout: 15000, windowsHide: true,
      });
      if (res.status === 0 && res.stdout) {
        const p = res.stdout.trim().split(/\r?\n/)[0];
        if (p && fs.existsSync(p)) return p;
      }
    } catch { /* fallback */ }
    return null;
  };

  const viaShell = pythonLaunchCandidates().map(tryResolve).find(Boolean);
  if (viaShell) return viaShell;
  if (process.platform !== 'win32') return null;

  const la = process.env.LOCALAPPDATA;
  if (process.platform !== 'win32') return null;
  const fallbacks = [
    la && path.join(la, 'Programs', 'Python', 'Python312', 'python.exe'),
    la && path.join(la, 'Programs', 'Python', 'Python311', 'python.exe'),
    process.env.ProgramFiles && path.join(process.env.ProgramFiles, 'Python312', 'python.exe'),
    process.env.ProgramFiles && path.join(process.env.ProgramFiles, 'Python311', 'python.exe'),
  ].filter(Boolean);
  return fallbacks.find((p) => fs.existsSync(p)) || null;
}

function sendSetup(evt) {
  if (splashWindow && !splashWindow.isDestroyed()) {
    splashWindow.webContents.send('setup:progress', evt);
  }
}

function stripAnsi(s) {
  return String(s).replace(/\x1b\[[0-9;]*m/g, '').replace(/\r?\n/g, '').trim();
}

// Ejecuta un comando heredando la salida, líneas útiles a splash.
function runCmd(cmd, args, opts) {
  opts = opts || {};
  return new Promise((resolve, reject) => {
    const child = spawn(cmd, args, { cwd: opts.cwd || BRAIN_DIR, windowsHide: true, shell: opts.shell });
    let buf = '';
    const feed = (chunk) => {
      buf += chunk.toString();
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = stripAnsi(buf.slice(0, i));
        buf = buf.slice(i + 1);
        if (line && opts.onLine) opts.onLine(line);
      }
    };
    child.stdout && child.stdout.on('data', feed);
    child.stderr && child.stderr.on('data', feed);
    child.on('error', (err) => reject(err));
    child.on('close', (code) => {
      if (code === 0) resolve();
      else reject(new Error('Proceso terminó con código ' + code));
    });
  });
}

function writeEnvFromExample() {
  const src = path.join(BRAIN_DIR, '.env.example');
  const dst = ENV_FILE;
  if (!fs.existsSync(dst) && fs.existsSync(src)) {
    try { fs.copyFileSync(src, dst); } catch { /* noop */ }
  }
}

// ------------------------------------------------------------------ //
// Progreso de la instalacion
// ------------------------------------------------------------------ //
// pip habla de dos maneras distintas segun como se instale, y antes solo se
// escuchaba una de ellas, que es por lo que la barra se quedaba clavada:
//
//   por red:   "Collecting numpy"
//   con ruedas: "Processing .../numpy-2.1.0-....whl"   <- no hay Collecting
//
// Ademas, entre linea y linea puede pasar un buen rato (descomprimir una rueda
// grande, compilar algo), asi que ademas del progreso real se deja que la barra
// avance sola poco a poco. Nunca se retrocede y nunca se pasa del tope.
function crearSeguimiento(desde, hasta, total) {
  const vistos = new Set();
  const estado = { percent: desde, ultimo: Date.now() };
  // El denominador se satura a proposito: si pip avisa de mas paquetes de los
  // que contabamos, la barra se queda cerca del tope en vez de reventarse.
  const tanto = (n) => Math.min(1, n / Math.max(total, 1));

  const sube = (nuevo, detalle) => {
    const objetivo = Math.max(estado.percent, Math.min(hasta, nuevo));
    if (objetivo !== estado.percent) {
      estado.percent = objetivo;
      estado.ultimo = Date.now();
    }
    sendSetup({
      phase: 'setup',
      title: 'Preparando el cerebro',
      detail: detalle || 'Instalando dependencias…',
      percent: estado.percent,
    });
  };

  return {
    /** Una linea de pip: se leea lo que venga. */
    linea(linea) {
      const m = /^(?:Collecting|Downloading)\s+(\S+)/.exec(linea);
      if (m) {
        vistos.add(m[1]);
        sube(desde + Math.round(tanto(vistos.size) * (hasta - desde)), 'Instalando ' + m[1]);
        return;
      }
      const w = /^Processing\s+.*?([A-Za-z0-9_.-]+?)-[\d.]+.*\.whl/.exec(linea);
      if (w) {
        vistos.add(w[1]);
        sube(desde + Math.round(tanto(vistos.size) * (hasta - desde)), 'Instalando ' + w[1]);
        return;
      }
      if (/^Installing collected packages:/.test(linea)) {
        // pip ya sabe cuantos va a instalar: cuenta y adelanta, pero sin
        // saltarse hasta el final. Lo que viene ahora (descomprimir y montar
        // 180 ruedas) es la parte lenta de verdad, asi que se reserva sitio
        // para ella en vez de llenar la barra aqui.
        const n = linea.slice(linea.indexOf(':') + 1).split(',').filter((s) => s.trim()).length;
        if (n > 0) {
          for (let i = 0; i < n; i++) vistos.add('x' + i);
          sube(desde + Math.round(tanto(n) * (hasta - desde) * 0.55), 'Montando los paquetes…');
        }
        return;
      }
      if (/^Successfully installed/.test(linea)) {
        // Ultima linea de pip: aqui ya esta todo instalado.
        sube(hasta, 'Dependencias instaladas');
      }
    },
    /** Si pip calla, la barra sigue respirando. */
    tic() {
      if (Date.now() - estado.ultimo < 1200) return;
      const techo = Math.min(hasta - 4, desde + Math.round(vistos.size / total * (hasta - desde)) + 6);
      if (estado.percent < techo) sube(techo);
    },
    percent() { return estado.percent; },
  };
}

async function runSetup() {
  sendSetup({ phase: 'setup', title: 'Preparando el cerebro', detail: 'Buscando Python…', percent: 2 });
  const python = findPython();
  if (!python) {
    sendSetup({
      phase: 'error',
      title: 'Python no encontrado',
      detail: 'JARVIS necesita Python 3.11/3.12 para preparar su cerebro. Instálalo desde python.org y vuelve a intentarlo.',
      percent: 0,
    });
    return;
  }

  try {
    // 1. venv (en un directorio persistente del usuario, no en la instalación)
    if (!fs.existsSync(venvPy())) {
      fs.rmSync(venvDir(), { recursive: true, force: true });
      fs.mkdirSync(brainDataDir(), { recursive: true });
      sendSetup({ phase: 'setup', title: 'Preparando el cerebro', detail: 'Creando entorno virtual…', percent: 6 });
      await runCmd(python, ['-m', 'venv', venvDir()]);
      sendSetup({ phase: 'setup', title: 'Preparando el cerebro', detail: 'Entorno virtual listo.', percent: 10 });
    } else {
      sendSetup({ phase: 'setup', title: 'Preparando el cerebro', detail: 'Entorno virtual ya existe.', percent: 10 });
    }

    // 2. dependencias (pip conserva su caché entre arranques; no actualizarlo cada vez).
    const currentHash = requirementsHash();
    const wheelhouse = app.isPackaged
      ? path.join(ROOT_DIR, 'wheelhouse')
      : path.join(ROOT_DIR, 'dist-assets', 'wheelhouse');
    //    Si hay ruedas en el disco (vienen con el instalador), se instala sin red.
    const hasLocal = fs.existsSync(wheelhouse) &&
      fs.readdirSync(wheelhouse).some((f) => f.endsWith('.whl'));
    const requirements = requirementsFile();
    const reqPath = path.basename(requirements);
    const pipArgs = hasLocal
      ? ['-m', 'pip', 'install', '--no-index', '--find-links', wheelhouse, '-r', reqPath, '--no-input']
      : ['-m', 'pip', 'install', '-r', reqPath, '--no-input'];

    const reqs = fs.readFileSync(requirements, 'utf8')
      .split(/\r?\n/)
      .map((l) => l.trim())
      .filter((l) => l && !l.startsWith('#'));
    const total = Math.max(reqs.length, 1);
    // Con ruedas incluidas pip avisa una vez por rueda instalada, no por
    // requisito. Si contamos los requisitos como denominador se dispara al
    // principio, asi que se cuenta lo que hay de verdad en la carpeta.
    const piezas = hasLocal
      ? fs.readdirSync(wheelhouse).filter((f) => f.endsWith('.whl')).length
      : total;
    const seg = crearSeguimiento(16, 90, Math.max(piezas, total));
    sendSetup({
      phase: 'setup', title: 'Preparando el cerebro',
      detail: hasLocal
        ? 'Instalando dependencias incluidas (sin internet)…'
        : 'Descargando dependencias (la primera vez tarda unos minutos)…',
      percent: 16,
    });
    // Si pip tarda en decir nada, la barra sigue avanzando sola.
    const tic = setInterval(() => seg.tic(), 900);
    try {
      try {
        await runCmd(venvPy(), pipArgs, { onLine: (line) => seg.linea(line) });
      } catch (localErr) {
        // Si las ruedas locales no valen para esta plataforma, se cae a la red.
        if (!hasLocal) throw localErr;
        console.error('[JARVIS] las ruedas incluidas no sirvieron, voy por red:', localErr && localErr.message);
        // Sin tocar el porcentaje: la barra nunca retrocede, solo avanza.
        seg.linea('Las ruedas incluidas no valen para esta plataforma; descargando…');
        await runCmd(venvPy(), ['-m', 'pip', 'install', '-r', reqPath, '--no-input'], {
          onLine: (line) => seg.linea(line),
        });
      }
    } finally {
      clearInterval(tic);
    }
    sendSetup({ phase: 'setup', title: 'Preparando el cerebro', detail: 'Dependencias instaladas.', percent: 90 });

    // 3. .env y recursos
    writeEnvFromExample();
    sendSetup({ phase: 'setup', title: 'Preparando el cerebro', detail: 'Generando recursos…', percent: 94 });
    try {
      await runCmd(venvPy(), ['-c', 'import scripts.make_assets; scripts.make_assets.main()'], { onLine: () => {} });
    } catch { /* no crítico */ }

    try { fs.writeFileSync(VENV_OK(), currentHash); } catch { /* noop */ }
    sendSetup({ phase: 'done', title: 'Cerebro listo', detail: 'Arrancando JARVIS…', percent: 100 });
    setTimeout(() => {
      if (splashWindow && !splashWindow.isDestroyed()) splashWindow.close();
      splashWindow = null;
      const done = setupResolve;
      setupResolve = null;
      if (done) done();
    }, 700);
  } catch (err) {
    sendSetup({
      phase: 'error',
      title: 'No pude preparar el cerebro',
      detail: String((err && err.message) || err).slice(0, 260),
      percent: 0,
    });
  }
}

// Punto de entrada: asegura el venv, mostrando la pantalla de carga si hace falta.
function ensureBrainEnv() {
  return new Promise((resolve) => {
    if (venvReady()) return resolve();
    setupResolve = resolve;
    createSplash();
    runSetup();
  });
}

// ------------------------------------------------------------------ //
// Ventanas
// ------------------------------------------------------------------ //
function createHud() {
  if (hudWindow && !hudWindow.isDestroyed()) return hudWindow;
  hudWindow = new BrowserWindow({
    width: 960,
    height: 680,
    minWidth: 640,
    minHeight: 480,
    frame: true,
    transparent: false,
    backgroundColor: '#0a0500',
    resizable: true,
    autoHideMenuBar: true,
    icon: path.join(ASSETS_DIR, process.platform === 'win32' ? 'icon.ico' : 'icon.png'),
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  hudWindow.loadFile(path.join(__dirname, 'ui', 'index.html'));
  hudWindow.removeMenu();
  hudWindow.once('ready-to-show', () => hudWindow.show());
  hudWindow.on('closed', () => { hudWindow = null; });
  let hudRecreates = 0;
  hudWindow.webContents.on('render-process-gone', (_e, details) => {
    console.error('[JARVIS] HUD murió (' + (details.reason || 'unknown') + ').');
    const dead = hudWindow;
    hudWindow = null;
    try { dead.destroy(); } catch { /* ignore */ }
    if (hudRecreates < 3) {
      hudRecreates++;
      setTimeout(() => { if (!hudWindow) createHud(); }, 2000);
    }
  });

  if (process.env.NODE_ENV === 'dev') {
    hudWindow.webContents.openDevTools({ mode: 'detach' });
  }
  return hudWindow;
}

// El mini JARVIS solo vive en la pizarra Win+J (overlay). No se crea ningún
// orbe flotante propio: estas funciones quedan inertes por compatibilidad.
function createOrbWindow() {
  if (orbWindow) return;
  orbWindow = null;
}

function ensureOrbWindow() {
  if (orbWindow && !orbWindow.isDestroyed()) return;
  orbWindow = null;
}

function hideOrb() {
  // noop
}

function showOrb() {
  // noop
}

// Después de mostrar/crear el orbe hay que forzar el re-render para que el contexto
// WebGL de la ventana transparente no se quede en blanco. (Inerte: sin orbe propio.)
function forceOrbRender() {
  // noop
}

// Abre o esconde la isla. Se usa desde el menu de la bandeja, que es la via
// de salida cuando la isla esta escondida y no hay ninguna ventana a la que
// volver con el raton.
function toggleNotch() {
  if (!notchWindow || notchWindow.isDestroyed()) return;
  if (notchWindow.isVisible()) {
    notchWindow.hide();
  } else {
    notchWindow.showInactive();
  }
}

function restoreMain() {
  hideOrb();
  if (hudWindow && !hudWindow.isDestroyed()) {
    if (hudWindow.isMinimized()) hudWindow.restore();
    hudWindow.show();
    hudWindow.focus();
  } else {
    createHud();
  }
}

// Coucou mantiene la ventana fija en el panel (720x320), pegada al techo del
  // monitor y centrada en horizontal. Al plegarse no cambia de tamano: lo unico
  // que se mueve es la region de entrada. Asi que se dimensiona una vez aqui.
  function notchBounds(expanded) {
    const { bounds } = screen.getPrimaryDisplay();
    // Keep a fixed panel origin: renderer geometry and global pointer positions
    // must be translated using the same window bounds in compact and open modes.
    void expanded;
    return {
      x: bounds.x + Math.round((bounds.width - COUCOU_PANEL_W) / 2),
      y: bounds.y,
      width: COUCOU_PANEL_W,
      height: COUCOU_PANEL_H,
    };
  }

function updateNotchPanelBounds() {
  if (!notchWindow || notchWindow.isDestroyed()) return;
  const { bounds } = screen.getPrimaryDisplay();
  try {
    notchWindow.setBounds({ x: bounds.x + Math.round((bounds.width - COUCOU_PANEL_W) / 2),
      y: bounds.y, width: COUCOU_PANEL_W, height: COUCOU_PANEL_H });
    if (coucouColapsada) aplicaRegionEntrada();
  } catch { /* window may close during a display change */ }
}

function resetNotchRendererState() {
  coucouRect = null;
  coucouColapsada = false;
  notchExpanded = false;
  notchHovered = false;
}

function handleIslandShortcut(config) {
  if (!notchWindow || notchWindow.isDestroyed()) { showNotch(true); return; }
  if (!notchWindow.isVisible()) showNotch(true);
  if (config.evento === 'island:toggle' && notchExpanded) {
    notifyNotch('notch:auto-collapse');
    return;
  }
  if (!notchExpanded && (config.evento === 'island:tab' || config.evento === 'island:send')) notifyNotch('notch:restore');
  notifyNotch(config.evento, config.dato || null);
}

const NOTCH_SETTINGS_KEY = 'coucou_notch_settings';
function sanitizeNotchSettings(settings) {
  const allowed = new Set(['soundEnabled','soundVolume','autoCloseInterval','openOnHover','absenceInterval','activeIntegrations','mainPill','screen','autostart','hooksInstalled','model','showPlanInNotch','planRelayInstalled','showCodexPlanInNotch','chatProvider','chatModels','ollamaUrl','lmstudioUrl','customUrl','shortcuts','mochiOutfit','pillColors','language']);
  if (!settings || typeof settings !== 'object' || Array.isArray(settings)) return {};
  return Object.fromEntries(Object.entries(settings).filter(([key]) => allowed.has(key)));
}
function saveNotchSettings(settings) {
  const safe = sanitizeNotchSettings(settings);
  try {
    db.prepare('INSERT INTO settings (key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value = excluded.value')
      .run(NOTCH_SETTINGS_KEY, JSON.stringify(safe));
    notifyNotch('settings-changed', safe);
    return true;
  } catch (error) { console.error('[JARVIS] No pude guardar preferencias del notch:', error.message); return false; }
}

ipcMain.handle('notch:focus-pill', (event, id) => {
  if (!isNotchSender(event) || typeof id !== 'string') return false;
  notifyNotch('notch:focus-pill', id);
  return true;
});
ipcMain.handle('notch:reset-conversation', (event) => {
  if (!isNotchSender(event)) return false;
  return startNewNotchConversation();
});
ipcMain.handle('notch:send-message', (event, message, context) => {
  if (!isNotchSender(event)) throw new Error('Emisor del notch no autorizado.');
  return sendNotchMessage(message, context);
});
ipcMain.handle('notch:get-conversation', (event, limit = 100) => {
  if (!isNotchSender(event)) return [];
  try { return readJarvisConversation(brainDatabasePath(), limit); }
  catch (error) { console.error('[JARVIS] No pude leer el historial:', error.message); return []; }
});

function setNotchShape(expanded) {
  if (!notchWindow || notchWindow.isDestroyed() || process.platform !== 'win32') return;
  const bounds = notchWindow.getBounds();
  const width = expanded ? COUCOU_PANEL_W : 288;
  const height = Math.max(1, bounds.height);
  const radius = expanded ? 22 : 14;
  try {
    notchWindow.setShape(roundedRectRows(width, height, radius,
      Math.round((bounds.width - width) / 2), 0));
  } catch (error) {
    console.warn('[JARVIS] No pude redondear el Notch nativo:', error.message);
  }
}

function notchPointInside(point) {
      if (!notchWindow || notchWindow.isDestroyed()) return false;
      const bounds = notchWindow.getBounds();
      const local = { x: point.x - bounds.x, y: point.y - bounds.y };
      if (coucouColapsada) {
        const wakeX = Math.round((bounds.width - COUCOU_STRIP_W) / 2);
        return local.x >= wakeX && local.x < wakeX + COUCOU_STRIP_W && local.y >= 0 && local.y < COUCOU_STRIP_H;
      }
      if (coucouRect) {
        const r = coucouRect;
        return local.x >= r.x && local.x < r.x + r.w && local.y >= r.y && local.y < r.y + r.h;
      }
      return notchHitTest(local, { x: 0, y: 0, width: bounds.width, height: bounds.height });
    }

    // ¿El puntero está sobre la isla? El rectángulo lo manda el renderer.
    function punteroSobreElNotch() {
      if (!notchWindow || notchWindow.isDestroyed()) return false;
      return notchPointInside(screen.getCursorScreenPoint());
    }

// El raton lo vigila el proceso principal, pero la maquina de estados es
    // del renderer (como en Coucou, donde lo lleva Rust): aqui solo se decide
    // si el raton atraviesa la ventana. El renderer recibe los eventos igual
    // porque se pide 'forward' al activar el modo transparente.
    function watchNotchPointer() {
      if (notchPointerTimer) return;
      let atras = null;
      notchPointerTimer = setInterval(() => {
        if (!notchWindow || notchWindow.isDestroyed()) { atras = null; return; }
        if (!notchWindow.isVisible()) { atras = null; return; }
        const dentro = punteroSobreElNotch();
        if (dentro === atras) return;
        atras = dentro;
        try {
          notchWindow.setIgnoreMouseEvents(!dentro, { forward: true });
          const bounds = notchWindow.getBounds();
          const cursor = screen.getCursorScreenPoint();
          notifyNotch(dentro ? 'notch:hover-open' : 'notch:hover-out', {
            x: cursor.x - bounds.x, y: cursor.y - bounds.y, inside: dentro,
          });
        } catch (err) { /* la ventana pudo cerrarse */ }
      }, 100);
    }

// Recoge el island a la capsula. Lo hace el proceso principal porque es quien
// sabe de verdad donde esta el raton; el renderer solo pinta.
function encogeElNotch() {
  if (!notchWindow || notchWindow.isDestroyed() || !notchExpanded) return;
  notchExpanded = false;
  try {
    notchWindow.webContents.send('notch:reset');
    notchWindow.setBounds(notchBounds(false));
    setNotchShape(false);
    notchWindow.setIgnoreMouseEvents(true, { forward: true });
  } catch (err) {
    console.error('[JARVIS] No pude encoger el Notch:', err.message);
  }
}

// ── Constantes de la isla de Coucou ─────────────────────────────────────────
// island.rs las declara en el mismo sitio: PANEL 720x320 y la franja 240x6.
const COUCOU_PANEL_W = 720;
const COUCOU_PANEL_H = 320;
const COUCOU_STRIP_W = 240;
const COUCOU_STRIP_H = 6;
const HIT_MARGIN = 14;
let coucouRect = null;
let coucouColapsada = false;
function createNotch(expandOnReady = false) {
  if (process.platform === 'win32' && notchHidden) return;
  if (notchWindow && !notchWindow.isDestroyed()) {
    showNotch(expandOnReady);
    return;
  }
  try {
    const win = new BrowserWindow({
      // Coucou abre la ventana con el panel entero (720x320) y luego la isla
        // empuja su rectangulo real; asi no hay un destello de ventana corta.
        ...notchBounds(true),
      frame: false,
      transparent: true,
      backgroundColor: '#00000000',
      resizable: false,
      minimizable: false,
      maximizable: false,
      fullscreenable: false,
      focusable: true,
      alwaysOnTop: true,
      skipTaskbar: true,
      hasShadow: false,
      title: 'JARVIS Notch',
      webPreferences: {
        preload: path.join(__dirname, 'preload.js'),
        contextIsolation: true,
        nodeIntegration: false,
      },
    });
    notchWindow = win;
    try { win.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true }); } catch { /* plataforma */ }
    win.loadFile(path.join(__dirname, 'ui', 'notch', 'index.html'));
    win.once('ready-to-show', () => {
      if (notchWindow !== win || win.isDestroyed()) return;
      setNotchShape(false);
      win.setIgnoreMouseEvents(true, { forward: true });
      win.showInactive();
      watchNotchPointer();
      if (expandOnReady) win.webContents.send('notch:restore');
    });
    win.on('closed', () => {
      if (notchWindow !== win) return;
      notchWindow = null;
      notchExpanded = false;
      notchHovered = false;
    });
  } catch (err) {
    console.error('[JARVIS] No pude crear el Notch:', err.message);
    notchWindow = null;
  }
}

function hideNotch() {
  notchHidden = true;
  notchExpanded = false;
  notchHovered = false;
  if (notchWindow && !notchWindow.isDestroyed()) {
    // Aviso al renderer: al ocultarse tiene que soltar sus temporizadores y
    // parar la animacion. El notch espera este evento y antes no le llegaba,
    // asi que se quedaba creyendo que estaba a la vista.
    try { notchWindow.webContents.send('notch:hidden'); } catch { /* noop */ }
    notchWindow.hide();
  }
}

function showNotch(expand = false) {
  notchHidden = false;
  if (!notchWindow || notchWindow.isDestroyed()) {
    createNotch(expand);
    return !!notchWindow;
  }
  notchExpanded = false;
  notchHovered = false;
  notchWindow.setBounds(notchBounds(false));
  resetNotchRendererState();
  setNotchShape(false);
  notchWindow.setIgnoreMouseEvents(true, { forward: true });
  notchWindow.showInactive();
  // Si venia oculto, el renderer se enteraba y aqui se le avisa de que vuelve.
  if (notchWindow.webContents.isLoading()) {
    notchWindow.webContents.once('did-finish-load', () => {
      if (notchWindow && !notchWindow.isDestroyed() && notchWindow.isVisible()) {
        notchWindow.webContents.send(expand ? 'notch:restore' : 'notch:reset');
      }
    });
  } else {
    notchWindow.webContents.send(expand ? 'notch:restore' : 'notch:reset');
  }
  watchNotchPointer();
  return true;
}

// El cierre del island lo lleva ahora el renderer (ui/notch/notch.js), con
// los mismos tiempos que Coucou: abierto -> capsula a los 15 s y -> oculto a
// los 60 s. Aqui ya no hay temporizadores que compitan con el suyo.

function bootstrapOnce() {
  if (bootstrapPromise) return bootstrapPromise;
  bootstrapPromise = Promise.resolve().then(() => {
    initDb();
    migrateUserConfig();
    restorePersistedKeys();
    createTray();
    createNotch();
    return ensureBrainEnv();
  }).then((needsBrainSetup) => {
    if (!hasAnyApiKey() && !setupWizardSkipped()) {
      createWizard();
      return;
    }
    if (!needsBrainSetup || hasAnyApiKey() || setupWizardSkipped()) {
      createHud();
      startBrain();
      waitBrainThenBridge();
    }
  }).catch((err) => {
    console.error('[JARVIS] No se pudo iniciar la app:', err);
    bootstrapPromise = null;
  });
  return bootstrapPromise;
}

function createWizard() {
  wizardWindow = new BrowserWindow({
    width: 680,
    height: 640,
    frame: false,
    resizable: false,
    backgroundColor: '#0b0d10',
    icon: path.join(ASSETS_DIR, 'icon.png'),
    skipTaskbar: true,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      allowScriptsToClose: true,
    },
  });
  wizardWindow.loadFile(path.join(__dirname, 'setup-wizard', 'index.html'));
  wizardWindow.setMenuBarVisibility(false);
  wizardWindow.once('closed', () => {
    wizardWindow = null;
    if (!hudWindow) {
      createHud();
      startBrain();
      waitBrainThenBridge();
    }
  });
}

// Pantalla de carga: se muestra solo si el brain necesita instalar sus dependencias.
function createSplash() {
  if (splashWindow) return;
  const { workArea } = require('electron').screen.getPrimaryDisplay();
  const W = 520;
  const H = 420;
  splashWindow = new BrowserWindow({
    width: W,
    height: H,
    x: workArea.x + Math.round((workArea.width - W) / 2),
    y: workArea.y + Math.round((workArea.height - H) / 2),
    frame: false,
    resizable: false,
    alwaysOnTop: true,
    backgroundColor: '#050200',
    icon: path.join(ASSETS_DIR, 'icon.png'),
    skipTaskbar: true,
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      allowScriptsToClose: true,
    },
  });
  splashWindow.loadFile(path.join(__dirname, 'splash', 'index.html'));
  splashWindow.once('ready-to-show', () => splashWindow.show());
  splashWindow.on('closed', () => { splashWindow = null; });
}

// ------------------------------------------------------------------ //
// Overlay "pizarra": Win+J → ventana semitransparente sobre el escritorio
// ------------------------------------------------------------------ //
function restoreOverlayState() {
  restoreMain();
}

let overlayCreating = false;

function createOverlay() {
  if (overlayWindow && !overlayWindow.isDestroyed()) return;
  if (overlayCreating) return;
  overlayCreating = true;
  try {
    const wa = screen.getPrimaryDisplay().workArea;
    overlayWindow = new BrowserWindow({
    width: wa.width,
    height: wa.height,
    x: wa.x,
    y: wa.y,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    resizable: false,
    fullscreenable: false,
    skipTaskbar: true,
    backgroundColor: '#00000000',
    icon: path.join(ASSETS_DIR, 'icon.png'),
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
      webSecurity: false,
    },
  });
  overlayWindow.loadFile(path.join(__dirname, 'ui', 'overlay', 'index.html'));
  overlayWindow.webContents.on('render-process-gone', (_e, details) => {
    console.error('[JARVIS] Overlay renderer murió (' + (details.reason || 'unknown') + ') — se reintentará en el próximo Win+J.');
    overlayWindow = null;
    overlayCreating = false;
    stopPerfPolling();
  });
  overlayWindow.once('ready-to-show', () => {
    overlayCreating = false;
    if (overlayPendingShow) {
      overlayPendingShow = false;
      overlayWindow.show();
      startPerfPolling();
    }
  });
  overlayWindow.on('closed', () => {
    overlayWindow = null;
    overlayCreating = false;
    stopPerfPolling();
  });
  } catch (err) {
    console.error('[JARVIS] No pude crear el overlay: ' + (err && err.message));
    overlayWindow = null;
    overlayCreating = false;
  }
}

function showOverlayNow() {
  if (!overlayWindow || overlayWindow.isDestroyed()) {
    overlayPendingShow = true;
    createOverlay();
    return;
  }
  if (!overlayWindow.isVisible()) {
    if (overlayWindow.webContents.isLoading()) {
      overlayPendingShow = true;
    } else {
      overlayPendingShow = false;
      overlayWindow.show();
      startPerfPolling();
    }
  }
}

function toggleOverlay() {
  if (overlayToggleLock) return;
  overlayToggleLock = true;
  setTimeout(() => { overlayToggleLock = false; }, 250);
  if (overlayWindow && !overlayWindow.isDestroyed() && overlayWindow.isVisible()) {
    overlayPendingShow = false;
    overlayWindow.hide();
    stopPerfPolling();
    restoreOverlayState();
  } else {
    hideOrb();
    showOverlayNow();
  }
}

function registerOverlayHotkey() {
    if (process.platform !== 'win32') return;
    const ok = globalShortcut.register('Super+J', toggleOverlay);
    console.log('[JARVIS] Atajo Win+J ' + (ok ? 'registrado.' : 'NO se pudo registrar.'));

    /* Los atajos se envían a la máquina de estados del renderer; así se usa
       el mismo flujo que los clics y el botón de plegado. */
    const atajos = {
      'Super+Shift+J': { evento: 'island:toggle', que: 'abrir o cerrar el island' },
      'Super+Shift+1': { evento: 'island:tab', dato: 'home', que: 'ir a Inicio' },
      'Super+Shift+2': { evento: 'island:tab', dato: 'chat', que: 'ir al chat' },
      'Super+Shift+3': { evento: 'island:tab', dato: 'new', que: 'ir a Tarea' },
      'Super+Shift+4': { evento: 'island:tab', dato: 'activity', que: 'ir a Actividad' },
      'Super+Shift+Enter': { evento: 'island:send', que: 'enviar lo que este escrito' },
      'Super+Shift+M': { evento: 'island:mute', que: 'silenciar los sonidos' },
    };
    globalShortcuts = {};
    for (const [combo, cfg] of Object.entries(atajos)) {
      const registrado = globalShortcut.register(combo, () => mandaAtajo(cfg));
      globalShortcuts[combo] = { ...cfg, registrado };
      if (!registrado) console.log('[JARVIS] Atajo ' + combo + ' (' + cfg.que + ') NO se pudo registrar.');
    }
    console.log('[JARVIS] Atajos del island: ' +
      Object.values(globalShortcuts).filter((a) => a.registrado).length + ' de ' +
      Object.keys(globalShortcuts).length + ' registrados.');
  }

  function mandaAtajo(cfg) {
    handleIslandShortcut(cfg);
  }

// ---- overlay ----
let overlayBackend = null;
function getOverlayBackend() {
  if (!overlayBackend) overlayBackend = require('./overlay-backend.js');
  return overlayBackend;
}

function listApps() { return getOverlayBackend().listApps(); }
function recentFiles() { return getOverlayBackend().recentFiles(); }
function listDir(dir) { return getOverlayBackend().listDir(dir || null); }

function pollPerf() {
  const data = getOverlayBackend().perfSnapshot();
  if (overlayWindow && !overlayWindow.isDestroyed()) {
    overlayWindow.webContents.send('perf:update', data);
  }
}

function startPerfPolling() {
  if (perfInterval) return;
  pollPerf();
  perfInterval = setInterval(pollPerf, 1000);
}

function stopPerfPolling() {
  if (perfInterval) { clearInterval(perfInterval); perfInterval = null; }
}

function openOverlayItem(item) {
  if (!item) return;
  try {
    // El overlay es alwaysOnTop y fullscreen: al abrir una app/archivo lo
    // cerramos para que lo abierto se vea en ese espacio, no detrás.
    overlayPendingShow = false;
    if (overlayWindow && !overlayWindow.isDestroyed()) {
      overlayWindow.hide();
    }
    stopPerfPolling();
    const wins = BrowserWindow.getAllWindows();
    for (const w of wins) {
      if (!w.isDestroyed()) w.blur();
    }
    shell.openPath(item.path || item.target || item);
  } catch { /* noop */ }
}

// ------------------------------------------------------------------ //
// Puente WhatsApp self-chat: arranca en silencio (utilityProcess, sin
// ventana) cuando el brain responde al health. Sesión persistente en
// brain-data/wa-session (sobrevive reinstalaciones). Log en userData.
// ------------------------------------------------------------------ //
function waLog(line) {
  try {
    fs.appendFileSync(path.join(app.getPath('userData'), 'wa-bridge.log'),
      new Date().toISOString() + ' ' + line + '\n');
  } catch { /* noop */ }
}

function waBridgeEntry() {
  const cands = [];
  try {
    if (process.resourcesPath) cands.push(path.join(process.resourcesPath, 'wa-bridge', 'bridge.js'));
  } catch { /* noop */ }
  cands.push(path.join(__dirname, '..', 'wa-bridge', 'bridge.js')); // dev
  for (const c of cands) {
    try { if (c && fs.existsSync(c)) return c; } catch { /* noop */ }
  }
  return null;
}

function startWaBridge() {
  if (waQuitting || waBridgeProc) return;
  const entry = waBridgeEntry();
  if (!entry) { waLog('puente no empaquetado: salto'); return; }
  try {
    const sessionDir = path.join(brainDataDir(), 'wa-session');
    try { fs.mkdirSync(sessionDir, { recursive: true }); } catch { /* noop */ }
    // Migración única: si la persistente está vacía y hay sesión emparejada
    // en el workspace, copiarla para no re-escanear el QR.
    try {
      if (!fs.existsSync(path.join(sessionDir, 'creds.json'))) {
        const srcDir = path.join(__dirname, '..', 'wa-bridge', 'session');
        if (fs.existsSync(path.join(srcDir, 'creds.json'))) {
          for (const f of fs.readdirSync(srcDir)) {
            try { fs.copyFileSync(path.join(srcDir, f), path.join(sessionDir, f)); } catch { /* noop */ }
          }
          waLog('sesión migrada desde workspace');
        }
      }
    } catch { /* noop */ }
    waBridgeProc = utilityProcess.fork(entry, [], {
      serviceName: 'JARVIS WhatsApp',
      stdio: 'pipe',
      env: {
        ...process.env,
        WA_SESSION_DIR: sessionDir,
        WA_BRAIN_URL: 'http://127.0.0.1:8000',
        WA_DEBUG: '0',
      },
    });
    waLog('puente arrancado (pid=' + waBridgeProc.pid + ')');
    if (waBridgeProc.stdout) waBridgeProc.stdout.on('data', (d) => waLog('[out] ' + String(d).trim()));
    if (waBridgeProc.stderr) waBridgeProc.stderr.on('data', (d) => waLog('[err] ' + String(d).trim()));
    waBridgeProc.on('exit', (code) => {
      waLog('puente salió (code=' + code + ')');
      waBridgeProc = null;
      if (!waQuitting && code !== 0 && waBridgeTries < 5) {
        waBridgeTries++;
        waBridgeTimer = setTimeout(() => { if (!waQuitting) startWaBridge(); }, 15000);
      }
    });
  } catch (e) {
    waLog('no pude arrancar el puente: ' + (e && e.message));
  }
}

function waitBrainThenBridge(attempts = 0) {
  // Puente WA DESACTIVADO por decisión del señor: WhatsApp solo lectura
  // (integración pywinauto del ritual). Para reactivar: WA_BRIDGE=1.
  if (process.env.WA_BRIDGE !== '1') {
    waLog('puente WA desactivado (solo lectura)');
    return;
  }
  if (waQuitting) return;
  const done = (ok) => {
    if (ok) { startWaBridge(); return; }
    if (waQuitting || attempts > 45) { waLog('brain sin health: puente no arrancado'); return; }
    waBridgeTimer = setTimeout(() => waitBrainThenBridge(attempts + 1), 4000);
  };
  try {
    const req = require('http').get('http://127.0.0.1:8000/health', (res) => {
      try { res.resume(); } catch { /* noop */ }
      done(res.statusCode === 200);
    });
    req.on('error', () => done(false));
    req.setTimeout(4000, () => { try { req.destroy(); } catch { /* noop */ } done(false); });
  } catch { done(false); }
}

// ------------------------------------------------------------------ //
// Tray
// ------------------------------------------------------------------ //
function autostartEntryPath() {
  const home = app.getPath('home');
  if (process.platform === 'darwin') return path.join(home, 'Library', 'LaunchAgents', 'com.jarvis.assistant.plist');
  if (process.platform === 'linux') return path.join(home, '.config', 'autostart', 'jarvis.desktop');
  return null;
}

function isAutostartOn() {
  if (process.platform === 'win32') {
    try {
      const { execFileSync } = require('child_process');
      const key = ['HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run', '/v', 'JARVIS'];
      return execFileSync('reg', ['query', ...key], { encoding: 'utf8', windowsHide: true }).includes(process.execPath);
    } catch { return false; }
  }
  const entry = autostartEntryPath();
  return !!entry && fs.existsSync(entry);
}

function setAutostart(on) {
  try {
    if (process.platform === 'win32') {
      const { execFileSync } = require('child_process');
      const key = ['HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run', '/v', 'JARVIS'];
      if (on) {
        execFileSync('reg', ['add', ...key, '/t', 'REG_SZ', '/d', `"${process.execPath}"`, '/f'],
          { encoding: 'utf8', windowsHide: true });
      } else {
        execFileSync('reg', ['delete', ...key, '/f'], { encoding: 'utf8', windowsHide: true });
      }
      return true;
    }
    const entry = autostartEntryPath();
    if (!entry) return false;
    if (!on) {
      if (fs.existsSync(entry)) fs.unlinkSync(entry);
      return true;
    }
    fs.mkdirSync(path.dirname(entry), { recursive: true });
    if (process.platform === 'darwin') {
      const escapeXml = (value) => String(value).replace(/&/g, '&amp;').replace(/</g, '&lt;')
        .replace(/>/g, '&gt;').replace(/\"/g, '&quot;').replace(/'/g, '&apos;');
      fs.writeFileSync(entry,
        `<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n<plist version="1.0"><dict><key>Label</key><string>com.jarvis.assistant</string><key>ProgramArguments</key><array><string>${escapeXml(process.execPath)}</string></array><key>RunAtLoad</key><true/></dict></plist>\n`, 'utf8');
    } else {
      const quoteDesktop = (value) => String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"')
        .replace(/`/g, '\\`').replace(/\$/g, '\\$');
      fs.writeFileSync(entry,
        `[Desktop Entry]\nType=Application\nName=JARVIS\nExec="${quoteDesktop(process.execPath)}"\nTerminal=false\nX-GNOME-Autostart-enabled=true\n`, 'utf8');
    }
    return true;
  } catch (err) {
    console.error('[JARVIS] autostart:', err && err.message);
    return false;
  }
}

function trayIconPath() {
  // En macOS el menu de la barra necesita una imagen de plantilla (negra con
  // alfa) para poder invertirse sola con el tema claro y el oscuro. Con un PNG
  // normal se ve gris apagado y parece que no esta.
  if (process.platform === 'darwin') {
    const t = path.join(ASSETS_DIR, 'tray-iconTemplate.png');
    if (fs.existsSync(t)) return t;
  }
  return path.join(ASSETS_DIR, 'tray-icon.png');
}

function createTray() {
  const iconPath = trayIconPath();
  try {
    const img = fs.existsSync(iconPath) ? iconPath : emptyIcon();
    tray = new Tray(img);
    tray.setToolTip('JARVIS — clic para mostrarlo, clic derecho para el menu');
    const platformName = process.platform === 'darwin' ? 'macOS' : process.platform === 'win32' ? 'Windows' : 'Linux';
    const menu = Menu.buildFromTemplate([
      { label: 'Mostrar JARVIS', click: () => restoreMain() },
      { label: 'Abrir / cerrar la isla', click: () => toggleNotch() },
      { type: 'separator' },
      { label: 'Reiniciar cerebro', click: () => { brainProcess = null; startBrain(); } },
      { label: 'Abrir carpeta del proyecto', click: () => shell.openPath(ROOT_DIR) },
      { label: `Iniciar con ${platformName}`, type: 'checkbox', checked: isAutostartOn(),
        click: (item) => { if (!setAutostart(item.checked)) item.checked = !item.checked; } },
      { type: 'separator' },
      { label: 'Salir de JARVIS', accelerator: 'CommandOrControl+Q', click: () => app.quit() },
    ]);
    tray.setContextMenu(menu);
    // En macOS el clic abre el menu, que es lo esperable; en el resto de
    // sistemas el clic muestra la ventana.
    if (process.platform === 'darwin') {
      tray.on('click', () => tray.popUpContextMenu(menu));
    } else {
      tray.on('click', () => restoreMain());
    }
  } catch (err) {
    tray = null;
    console.warn('[JARVIS] No pude crear el icono de bandeja:', err.message);
  }
}

function emptyIcon() {
  // Icono PNG 1x1 en memoria si no existe asset.
  const { nativeImage } = require('electron');
  const b64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
  return nativeImage.createFromDataURL('data:image/png;base64,' + b64);
}

// ------------------------------------------------------------------ //
// First-run
// ------------------------------------------------------------------ //
function bootstrap() {
  return bootstrapOnce();
}

function allowLocalCameraRequests() {
  const ses = require('electron').session.defaultSession;
  const isLocalFile = (url) => typeof url === 'string' && /^file:\/\//i.test(url);
  const canUseCamera = (webContents, permission, origin, details, allowUnknownType = false) => {
    let pageUrl = '';
    try { pageUrl = webContents && webContents.getURL(); } catch { /* ventana cerrada */ }
    const requestingUrl = details && details.requestingUrl;
    const securityOrigin = origin || (details && details.securityOrigin);
    const requestSource = requestingUrl || securityOrigin || pageUrl;
    const localApp = isLocalFile(requestSource);
    const mediaTypes = Array.isArray(details && details.mediaTypes) ? details.mediaTypes : [];
    const mediaType = details && details.mediaType;
    const asksForAudio = mediaTypes.includes('audio') || mediaType === 'audio';
    const asksForVideo = mediaTypes.includes('video') || mediaType === 'video';

    // En Electron el metadato de tipo puede omitirse. En ese caso solo se
    // permite la solicitud de nuestra página local; si Electron declara audio,
    // se deniega. Las vistas piden video y no solicitan audio.
    const checkHasUnknownType = !mediaTypes.length &&
      (!mediaType || mediaType === 'unknown');
    return permission === 'media' && localApp && !asksForAudio &&
      (asksForVideo || (allowUnknownType && checkHasUnknownType));
  };
  ses.setPermissionRequestHandler((webContents, permission, callback, details) => {
    const allowed = canUseCamera(webContents, permission, null, details, true);
    if (permission === 'media') {
      console.info('[JARVIS] Solicitud de cámara', JSON.stringify({
        allowed,
        origin: details && details.securityOrigin,
        mediaTypes: details && details.mediaTypes,
        mediaType: details && details.mediaType,
        requestingUrl: details && details.requestingUrl,
      }));
    }
    callback(allowed);
  });
  ses.setPermissionCheckHandler((webContents, permission, requestingOrigin, details) =>
    canUseCamera(webContents, permission, requestingOrigin, details, true));
}

app.whenReady().then(() => {
  // El notch se recoloca al cambiar resolucion o escala. Va aqui porque
  // 'screen' no existe hasta que la app esta lista.
  screen.on('display-metrics-changed', () => {
    if (notchWindow && !notchWindow.isDestroyed()) {
      updateNotchPanelBounds();
      setNotchShape(notchExpanded);
    }
  });
  allowLocalCameraRequests();
  Menu.setApplicationMenu(null);
  // Agrupa todas las ventanas de JARVIS bajo un único icono en la barra de tareas.
  if (process.platform === 'win32') app.setAppUserModelId('com.jarvis.assistant');
  if (process.platform === 'win32') {
    registerOverlayHotkey();
    createOverlay(); // pizarra de escritorio exclusiva de Windows
  }
  bootstrap();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) {
      bootstrapPromise = null;
      bootstrap();
    } else if (process.platform === 'darwin') {
      restoreMain();
    }
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('render-process-gone', (_e, _wc, details) => {
  console.error('[JARVIS] Renderer murió (' + (details.reason || 'unknown') + ').');
});

app.on('gpu-process-crashed', (_e, killed) => {
  console.error('[JARVIS] GPU murió (killed=' + killed + ').');
});

app.on('before-quit', () => {
  waQuitting = true;
  try { if (waBridgeTimer) clearTimeout(waBridgeTimer); } catch { /* ignore */ }
  try { if (waBridgeProc) waBridgeProc.kill(); } catch { /* ignore */ }
  try { if (brainProcess) brainProcess.kill(); } catch { /* ignore */ }
  try { if (notchPointerTimer) clearInterval(notchPointerTimer); } catch { /* ignore */ }
  try { if (db) db.close(); } catch { /* ignore */ }
});

// ------------------------------------------------------------------ //
// IPC
// ------------------------------------------------------------------ //
ipcMain.handle('history:get', (_e, limit = 20) => {
  return db.prepare('SELECT role, content, created_at FROM messages ORDER BY id DESC LIMIT ?')
    .all(limit).reverse();
});

ipcMain.handle('notch:jarvis-history', (event, limit = 100) => {
  if (!isNotchSender(event)) return [];
  try { return readJarvisConversation(brainDatabasePath(), limit); }
  catch (error) { console.error('[JARVIS] No pude leer el historial:', error.message); return []; }
});

ipcMain.handle('notch:get-settings', (event) => {
  if (!isNotchSender(event)) return {};
  try { return sanitizeNotchSettings(JSON.parse(db.prepare('SELECT value FROM settings WHERE key = ?').get(NOTCH_SETTINGS_KEY)?.value || '{}')); }
  catch { return {}; }
});
ipcMain.handle('notch:save-settings', (event, settings) => isNotchSender(event) && saveNotchSettings(settings));
ipcMain.handle('notch:settings', (event) => isNotchSender(event) && (restoreMain(), true));
ipcMain.handle('notch:log', (event, message) => {
  if (!isNotchSender(event)) return false;
  console.info('[Coucou]', String(message).slice(0, 500));
  return true;
});

ipcMain.handle('history:add', (_e, { role, content }) => {
  db.prepare('INSERT INTO messages (role, content) VALUES (?, ?)').run(role, content);
  return true;
});

ipcMain.handle('window:toggleFullscreen', () => {
  if (!hudWindow) return false;
  hudWindow.setFullScreen(!hudWindow.isFullScreen());
  return hudWindow.isFullScreen();
});

ipcMain.handle('window:restore', () => restoreMain());

ipcMain.handle('notch:set-expanded', (event, expanded, contentHeight) => {
  if (!isNotchSender(event) || !notchWindow.isVisible()) return false;
  notchExpanded = !!expanded;
  if (notchExpanded && Number.isFinite(Number(contentHeight))) notchContentHeight = Math.max(120, Math.min(Number(contentHeight), 900));
  updateNotchPanelBounds();
  if (process.platform === 'win32') aplicaRegionEntrada();
  else setNotchShape(notchExpanded);
  notchWindow.setAlwaysOnTop(true);
  notchWindow.setIgnoreMouseEvents(!notchExpanded, notchExpanded ? undefined : { forward: true });
  if (!notchExpanded) notchHovered = false;
  return true;
});

ipcMain.handle('notch:hide', (event) => {
  if (!notchWindow || event.sender !== notchWindow.webContents) return false;
  hideNotch();
  return true;
});

ipcMain.handle('notch:main', (event) => {
  if (!notchWindow || event.sender !== notchWindow.webContents) return false;
  restoreMain();
  return true;
});

ipcMain.handle('notch:restore', (event) => {
  if (!notchWindow || event.sender !== notchWindow.webContents) return false;
  return showNotch(true);
});

ipcMain.handle('notch:toggle', () => {
  if (notchWindow && !notchWindow.isDestroyed() && notchWindow.isVisible()) hideNotch();
  else showNotch(true);
  return true;
});

ipcMain.handle('notch:open', (event) => {
  if (!notchWindow || event.sender !== notchWindow.webContents || notchWindow.isDestroyed()) return false;
  return showNotch(true);
});

ipcMain.handle('notch:external', (event, rawUrl) => {
  const trustedSender = (notchWindow && event.sender === notchWindow.webContents) ||
    (wizardWindow && event.sender === wizardWindow.webContents);
  if (!trustedSender || typeof rawUrl !== 'string') return false;
  try {
    const url = new URL(rawUrl);
    if (!['https:', 'http:'].includes(url.protocol)) return false;
    shell.openExternal(url.href);
    return true;
  } catch { return false; }
});

// El notch llama a openApp() para los accesos rapidos (OpenCode, Freebuff...)
// y el puente no lo tenia: al pulsarlos, '.then' sobre undefined hacia fallar
// la cadena entera y el Notch se quedaba a medias.
ipcMain.handle('notch:open-app', (event, id) => {
  if (!isNotchSender(event)) return false;
  try { return openNotchApp(id); }
  catch (error) { console.warn('[JARVIS] No pude abrir la app del notch:', error.message); return false; }
});

// OJO: esto NO puede quedarse aqui arriba. El modulo 'screen' de Electron
// solo existe despues del evento 'ready'; registrarlo al cargar el script hacia
// petar el arranque entero (no habia ventanas ni cerebro: se quedaba en un solo
// proceso sin hacer nada). Va dentro de app.whenReady(), mas abajo.

ipcMain.handle('setup:retry', () => {
  if (setupResolve) runSetup();
  return true;
});

ipcMain.handle('env:skip-wizard', (event) => {
  if (!wizardWindow || event.sender !== wizardWindow.webContents) return { ok: false };
  try {
    markSetupWizardSkipped();
    return { ok: true };
  } catch (err) {
    return { ok: false, error: err.message };
  }
});

ipcMain.handle('env:save', (_e, keys) => {
  // Copia de seguridad y reescribe .env del brain
  if (fs.existsSync(ENV_FILE)) {
    fs.copyFileSync(ENV_FILE, ENV_FILE + '.bak');
  }
  const env = readEnv();
  for (const [k, v] of Object.entries(keys || {})) {
    if (v !== undefined && v !== '') env[k] = String(v).trim();
  }
  const body = Object.entries(env)
    .map(([k, v]) => `${k}=${v}`)
    .join('\n') + '\n';
  fs.mkdirSync(path.dirname(ENV_FILE), { recursive: true });
  fs.writeFileSync(ENV_FILE, body, 'utf8');
  persistKeys(env);
  if (hasApiKeyValues(env)) clearSetupWizardSkipped();
  return { ok: true, path: ENV_FILE, hasKeys: hasAnyApiKey() };
});

ipcMain.handle('env:get', () => {
  const env = readEnv();
  return {
    GROQ_API_KEY: env.GROQ_API_KEY || '',
    GEMINI_API_KEY: env.GEMINI_API_KEY || '',
    OPENROUTER_API_KEY: env.OPENROUTER_API_KEY || '',
  };
});

ipcMain.handle('app:quit', () => app.quit());

// ---- overlay ----
ipcMain.handle('overlay:close', () => {
  overlayPendingShow = false;
  if (overlayWindow) overlayWindow.hide();
  stopPerfPolling();
  return true;
});

ipcMain.handle('overlay:main', () => {
  overlayPendingShow = false;
  if (overlayWindow) overlayWindow.hide();
  stopPerfPolling();
  restoreOverlayState();
  return true;
});

ipcMain.handle('overlay:apps', () => listApps());
ipcMain.handle('overlay:recent', () => recentFiles());
ipcMain.handle('overlay:dir', (_e, dir) => listDir(dir));
ipcMain.handle('overlay:open', (_e, item) => { openOverlayItem(item); return true; });
// ── Puente de Coucou (island real) ──────────────────────────────────────────
// Coucou no redimensiona la ventana al plegar la isla: la ventana se queda
// siempre en PANEL (720x320) pegada al techo del monitor y lo unico que cambia
// es la region de entrada, que sigue al rectangulo que empuja la isla mas un
// margen de 14 px (island.rs: refresh_click_through + HIT_MARGIN).

function panelCentrado() {
  const { bounds } = screen.getPrimaryDisplay();
  return {
    x: bounds.x + Math.round((bounds.width - COUCOU_PANEL_W) / 2),
    y: bounds.y,
    width: COUCOU_PANEL_W,
    height: COUCOU_PANEL_H,
  };
}

/** Aplica la region de entrada; es lo unico que Rust cambia al plegar. */
function aplicaRegionEntrada() {
  if (!notchWindow || notchWindow.isDestroyed() || process.platform !== 'win32') return;
  let region;
  if (coucouColapsada) {
    region = roundedRectRows(COUCOU_STRIP_W, COUCOU_STRIP_H, 0,
      Math.round((COUCOU_PANEL_W - COUCOU_STRIP_W) / 2), 0);
  } else if (coucouRect && coucouRect.w > 0) {
    const x0 = Math.max(0, coucouRect.x - HIT_MARGIN);
    const y0 = Math.max(0, coucouRect.y - HIT_MARGIN);
    const x1 = coucouRect.x + coucouRect.w + HIT_MARGIN;
    const y1 = coucouRect.y + coucouRect.h + HIT_MARGIN;
    region = roundedRectRows(Math.min(COUCOU_PANEL_W, x1 - x0), y1 - y0, 14,
      x0, y0);
  } else {
    region = [];
  }
  try { notchWindow.setShape(region); } catch (err) { /* ventana cerrada */ }
}

ipcMain.handle('notch:island-rect', (event, x, y, width, height) => {
  if (!isNotchSender(event)) return false;
  const values = [x, y, width, height].map(Number);
  if (!values.every(Number.isFinite)) return false;
  const [rawX, rawY, rawW, rawH] = values;
  const w = Math.max(0, Math.min(COUCOU_PANEL_W, Math.round(rawW)));
  const h = Math.max(0, Math.min(COUCOU_PANEL_H, Math.round(rawH)));
  const antes = coucouRect;
  coucouRect = {
    x: Math.max(0, Math.min(COUCOU_PANEL_W - w, Math.round(rawX))),
    y: Math.max(0, Math.min(COUCOU_PANEL_H - h, Math.round(rawY))),
    w, h,
  };
  // Solo cuando cambia de verdad: setShape en cada fotograma es lo que hacia
  // que la ventana parpadeara y no llegara a plegarse.
  if (!antes || antes.x !== coucouRect.x || antes.y !== coucouRect.y ||
      antes.w !== coucouRect.w || antes.h !== coucouRect.h) {
    aplicaRegionEntrada();
  }
  return true;
});

ipcMain.handle('notch:collapsed', (event, collapsed) => {
  if (!isNotchSender(event)) return false;
  coucouColapsada = !!collapsed;
  aplicaRegionEntrada();
  return true;
});

ipcMain.handle('notch:focus-window', (event, focused) => {
  if (!isNotchSender(event)) return false;
  try {
    if (focused) notchWindow.focus();
    else notchWindow.blur();
  } catch (err) { /* nada */ }
  return true;
});

ipcMain.handle('notch:open-path', async (event, targetPath) => {
  if (!isNotchSender(event) || typeof targetPath !== 'string') return false;
  try { await shell.openPath(targetPath); return true; } catch { return false; }
});

ipcMain.handle('notch:save-png', async (event, data, name) => {
  if (!isNotchSender(event)) return false;
  try {
    const base64 = String(data).replace(/^data:image\/png;base64,/, '');
    const dir = path.join(app.getPath('pictures'), 'JARVIS');
    await fs.promises.mkdir(dir, { recursive: true });
    const destino = path.join(dir, String(name || 'resumen') + '.png');
    await fs.promises.writeFile(destino, Buffer.from(base64, 'base64'));
    shell.showItemInFolder(destino);
    return destino;
  } catch (err) {
    return 'error: ' + err.message;
  }
});

ipcMain.handle('notch:quit', (event) => {
  if (!isNotchSender(event)) return false;
  app.quit();
  return true;
});
