const { app, BrowserWindow, ipcMain, Tray, Menu, shell, screen, globalShortcut, utilityProcess } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn, spawnSync } = require('child_process');
const Database = require('better-sqlite3');

// En desarrollo, los recursos viven junto al proyecto; empaquetado, en resources/.
const ROOT_DIR = app.isPackaged ? process.resourcesPath : path.join(__dirname, '..');
const BRAIN_DIR = app.isPackaged ? path.join(process.resourcesPath, 'brain') : path.join(ROOT_DIR, 'brain');
const ENV_FILE = path.join(BRAIN_DIR, '.env');
const ASSETS_DIR = app.isPackaged ? path.join(process.resourcesPath, 'assets') : path.join(ROOT_DIR, 'assets');

// Los datos del cerebro se guardan fuera de la instalación para sobrevivir reinstalaciones.
function brainDataDir() {
  let ud = null;
  try { ud = app.getPath('userData'); } catch { /* app no listo aún */ }
  return ud ? path.join(ud, 'brain-data') : BRAIN_DIR;
}
function venvDir() { return path.join(brainDataDir(), 'venv'); }
function venvPy() { return path.join(venvDir(), 'Scripts', 'python.exe'); }
function venvOk() { return path.join(venvDir(), 'setup.ok'); }

let db;
let tray;
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
  const env = (persisted && API_KEY_NAMES.some((k) => persisted[k])) ? persisted : readEnv();
  return !!API_KEY_NAMES.slice(0, 3).some((k) => env[k]);
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
  if (!persisted || !fs.existsSync(persisted)) return;
  const keys = readEnvFile(persisted);
  const env = readEnv();
  let changed = false;
  for (const k of API_KEY_NAMES) {
    if (keys[k] && !env[k]) { env[k] = keys[k]; changed = true; }
  }
  if (changed) {
    fs.mkdirSync(path.dirname(ENV_FILE), { recursive: true });
    fs.writeFileSync(ENV_FILE, Object.entries(env).map(([k, v]) => `${k}=${v}`).join('\n') + '\n', 'utf8');
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
  return fs.existsSync(path.join(home, 'python.exe'));
}

function startBrain() {
  if (brainProcess) return;
  const venvPython = venvPy();
  const python = venvUsable() ? venvPython : 'python';
  const args = ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8000'];
  try {
    const logOut = fs.openSync(path.join(BRAIN_DIR, 'brain.out.log'), 'a');
    const logErr = fs.openSync(path.join(BRAIN_DIR, 'brain.err.log'), 'a');
    fs.mkdirSync(brainDataDir(), { recursive: true });
    brainProcess = spawn(python, args, {
      cwd: BRAIN_DIR,
      env: { ...process.env, JARVIS_DATA_DIR: brainDataDir() },
      stdio: ['ignore', logOut, logErr],
      windowsHide: true,
    });
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
  if (fs.existsSync(VENV_OK())) return true;
  try {
      const res = spawnSync(
        venvPy(),
        ['-c', 'import fastapi, uvicorn, websockets, sounddevice, numpy, pypdf; print(1)'],
        { timeout: 25000, windowsHide: true },
      );
    if (res.status === 0) {
      try { fs.writeFileSync(VENV_OK(), 'ok'); } catch { /* noop */ }
      return true;
    }
    return false;
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

  const viaShell = tryResolve('py') || tryResolve('python') || tryResolve('python3');
  if (viaShell) return viaShell;

  const la = process.env.LOCALAPPDATA;
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

    sendSetup({ phase: 'setup', title: 'Preparando el cerebro', detail: 'Actualizando pip…', percent: 14 });
    await runCmd(venvPy(), ['-m', 'pip', 'install', '--upgrade', 'pip'], { onLine: () => {} });

    // 2. dependencias con progreso por paquete
    //    Si existe wheelhouse local (viene con el instalador), instalamos sin red.
    const wheelhouse = path.join(ROOT_DIR, 'wheelhouse');
    const hasLocal = fs.existsSync(wheelhouse) &&
      fs.readdirSync(wheelhouse).some((f) => f.endsWith('.whl'));
    const pipArgs = hasLocal
      ? ['-m', 'pip', 'install', '--no-index', '--find-links', wheelhouse, '-r', 'requirements.txt', '--no-input']
      : ['-m', 'pip', 'install', '-r', 'requirements.txt', '--no-input'];

    const reqs = fs.readFileSync(path.join(BRAIN_DIR, 'requirements.txt'), 'utf8')
      .split(/\r?\n/)
      .map((l) => l.trim())
      .filter((l) => l && !l.startsWith('#'));
    const total = Math.max(reqs.length, 1);
    let got = 0;
    sendSetup({
      phase: 'setup', title: 'Preparando el cerebro',
      detail: hasLocal ? 'Instalando dependencias (local, sin red)…' : 'Descargando dependencias (la primera vez tarda unos minutos)…',
      percent: 16,
    });
    try {
      await runCmd(venvPy(), pipArgs, {
        onLine: (line) => {
          const m = line.match(/^Collecting\s+(\S+)/);
          if (m) {
            got++;
            const pct = Math.min(84, 16 + Math.round((got / total) * 68));
            sendSetup({ phase: 'setup', title: 'Preparando el cerebro', detail: 'Instalando ' + m[1], percent: pct });
          } else if (/^Installing collected|^Successfully installed/.test(line)) {
            sendSetup({ phase: 'setup', title: 'Preparando el cerebro', detail: line, percent: Math.min(90, 84 + got * 0.1) });
          }
        },
      });
    } catch (localErr) {
      // Fallback: si el wheelhouse local falla (versiones incompatibles), bajar por red.
      if (hasLocal) {
        console.error('[JARVIS] wheelhouse local falló, reintento por red:', localErr && localErr.message);
        try {
          await runCmd(venvPy(), ['-m', 'pip', 'install', '-r', 'requirements.txt', '--no-input'], {
            onLine: (line) => {
              const m = line.match(/^Collecting\s+(\S+)/);
              if (m) {
                got++;
                const pct = Math.min(84, 16 + Math.round((got / total) * 68));
                sendSetup({ phase: 'setup', title: 'Preparando el cerebro', detail: 'Instalando ' + m[1], percent: pct });
              } else if (/^Installing collected|^Successfully installed/.test(line)) {
                sendSetup({ phase: 'setup', title: 'Preparando el cerebro', detail: line, percent: Math.min(90, 84 + got * 0.1) });
              }
            },
          });
        } catch (err) {
          throw err;
        }
      } else {
        throw localErr;
      }
    }
    sendSetup({ phase: 'setup', title: 'Preparando el cerebro', detail: 'Dependencias instaladas.', percent: 90 });

    // 3. .env y recursos
    writeEnvFromExample();
    sendSetup({ phase: 'setup', title: 'Preparando el cerebro', detail: 'Generando recursos…', percent: 94 });
    try {
      await runCmd(venvPy(), ['-c', 'import scripts.make_assets; scripts.make_assets.main()'], { onLine: () => {} });
    } catch { /* no crítico */ }

    try { fs.writeFileSync(VENV_OK(), 'ok'); } catch { /* noop */ }
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

function restoreMain() {
  hideOrb();
  if (hudWindow) {
    hudWindow.show();
    hudWindow.focus();
  }
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
  const ok = globalShortcut.register('Super+J', toggleOverlay);
  console.log('[JARVIS] Atajo Win+J ' + (ok ? 'registrado.' : 'NO se pudo registrar.'));
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
function autostartKey() {
  return ['HKCU\\Software\\Microsoft\\Windows\\CurrentVersion\\Run', '/v', 'JARVIS'];
}

function isAutostartOn() {
  try {
    const { execFileSync } = require('child_process');
    const out = execFileSync('reg', ['query', ...autostartKey()], { encoding: 'utf8', windowsHide: true });
    return out.includes(process.execPath);
  } catch {
    return false;
  }
}

function setAutostart(on) {
  try {
    const { execFileSync } = require('child_process');
    if (on) {
      execFileSync('reg', ['add', ...autostartKey(), '/t', 'REG_SZ', '/d', `"${process.execPath}"`, '/f'],
        { encoding: 'utf8', windowsHide: true });
    } else {
      execFileSync('reg', ['delete', ...autostartKey(), '/f'], { encoding: 'utf8', windowsHide: true });
    }
    return true;
  } catch (e) {
    console.error('[JARVIS] autostart:', e && e.message);
    return false;
  }
}

function createTray() {
  const iconPath = path.join(ASSETS_DIR, 'tray-icon.png');
  tray = new Tray(fs.existsSync(iconPath) ? iconPath : emptyIcon());
  tray.setToolTip('JARVIS');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Mostrar/Activar', click: () => restoreMain() },
    { label: 'Reiniciar brain', click: () => { brainProcess = null; startBrain(); } },
    { label: 'Abrir carpeta del proyecto', click: () => shell.openPath(ROOT_DIR) },
    { label: 'Iniciar con Windows', type: 'checkbox', checked: isAutostartOn(),
      click: (item) => { if (!setAutostart(item.checked)) item.checked = !item.checked; } },
    { type: 'separator' },
    { label: 'Salir', click: () => app.quit() },
  ]));
  tray.on('click', () => restoreMain());
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
  initDb();
  restorePersistedKeys();
  createTray();

  ensureBrainEnv().then(() => {
    if (!hasAnyApiKey()) {
      createWizard();
    } else {
      createHud();
      startBrain();
      waitBrainThenBridge();
    }
  });

  // Al cerrar el wizard (guardó claves o saltó), abrimos el HUD y arrancamos el brain.
  // Sin claves JARVIS igualmente funciona en modo limitado con el fallback local (Ollama).
  wizardWindow?.on('closed', () => {
    wizardWindow = null;
    if (!hudWindow) {
      createHud();
      startBrain();
      waitBrainThenBridge();
    }
  });
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
  allowLocalCameraRequests();
  Menu.setApplicationMenu(null);
  // Agrupa todas las ventanas de JARVIS bajo un único icono en la barra de tareas.
  app.setAppUserModelId('com.jarvis.assistant');
  registerOverlayHotkey();
  createOverlay(); // precarga la pizarra Win+J para abrir de forma instantánea
  bootstrap();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) bootstrap();
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
});

// ------------------------------------------------------------------ //
// IPC
// ------------------------------------------------------------------ //
ipcMain.handle('history:get', (_e, limit = 20) => {
  return db.prepare('SELECT role, content, created_at FROM messages ORDER BY id DESC LIMIT ?')
    .all(limit).reverse();
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

ipcMain.handle('setup:retry', () => {
  if (setupResolve) runSetup();
  return true;
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