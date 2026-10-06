const { app, BrowserWindow, ipcMain, Tray, Menu, shell } = require('electron');
const path = require('path');
const fs = require('fs');
const { spawn, spawnSync } = require('child_process');
const Database = require('better-sqlite3');

const ROOT_DIR = path.join(__dirname, '..');
const BRAIN_DIR = path.join(ROOT_DIR, 'brain');
const ENV_FILE = path.join(BRAIN_DIR, '.env');
const ASSETS_DIR = path.join(ROOT_DIR, 'assets');

let db;
let tray;
let hudWindow = null;
let orbWindow = null;
let wizardWindow = null;
let splashWindow = null;
let brainProcess = null;

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

const API_KEY_NAMES = ['GROQ_API_KEY', 'GEMINI_API_KEY', 'OPENROUTER_API_KEY', 'WEATHER_API_KEY'];

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
  const venvPython = path.join(BRAIN_DIR, 'venv', 'Scripts', 'python.exe');
  const cfg = path.join(BRAIN_DIR, 'venv', 'pyvenv.cfg');
  if (!fs.existsSync(venvPython) || !fs.existsSync(cfg)) return false;
  const m = fs.readFileSync(cfg, 'utf8').match(/^home\s*=\s*(.+)$/m);
  if (!m) return false;
  const home = m[1].trim();
  return fs.existsSync(path.join(home, 'python.exe'));
}

function startBrain() {
  if (brainProcess) return;
  const venvPython = path.join(BRAIN_DIR, 'venv', 'Scripts', 'python.exe');
  const python = venvUsable() ? venvPython : 'python';
  const args = ['-m', 'uvicorn', 'main:app', '--host', '127.0.0.1', '--port', '8000'];
  try {
    const logOut = fs.openSync(path.join(BRAIN_DIR, 'brain.out.log'), 'a');
    const logErr = fs.openSync(path.join(BRAIN_DIR, 'brain.err.log'), 'a');
    brainProcess = spawn(python, args, {
      cwd: BRAIN_DIR,
      detached: true,
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
const VENV_PY = path.join(BRAIN_DIR, 'venv', 'Scripts', 'python.exe');
const VENV_OK = path.join(BRAIN_DIR, 'venv', 'setup.ok');
let setupResolve = null;

function venvReady() {
  if (!venvUsable()) return false;
  if (fs.existsSync(VENV_OK)) return true;
  try {
    const res = spawnSync(
      VENV_PY,
      ['-c', 'import fastapi, uvicorn, websockets, sounddevice, numpy; print(1)'],
      { timeout: 25000, windowsHide: true },
    );
    if (res.status === 0) {
      try { fs.writeFileSync(VENV_OK, 'ok'); } catch { /* noop */ }
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
    // 1. venv
    if (!fs.existsSync(VENV_PY)) {
      fs.rmSync(path.join(BRAIN_DIR, 'venv'), { recursive: true, force: true });
      sendSetup({ phase: 'setup', title: 'Preparando el cerebro', detail: 'Creando entorno virtual…', percent: 6 });
      await runCmd(python, ['-m', 'venv', 'venv']);
      sendSetup({ phase: 'setup', title: 'Preparando el cerebro', detail: 'Entorno virtual listo.', percent: 10 });
    } else {
      sendSetup({ phase: 'setup', title: 'Preparando el cerebro', detail: 'Entorno virtual ya existe.', percent: 10 });
    }

    sendSetup({ phase: 'setup', title: 'Preparando el cerebro', detail: 'Actualizando pip…', percent: 14 });
    await runCmd(VENV_PY, ['-m', 'pip', 'install', '--upgrade', 'pip'], { onLine: () => {} });

    // 2. dependencias con progreso por paquete
    const reqs = fs.readFileSync(path.join(BRAIN_DIR, 'requirements.txt'), 'utf8')
      .split(/\r?\n/)
      .map((l) => l.trim())
      .filter((l) => l && !l.startsWith('#'));
    const total = Math.max(reqs.length, 1);
    let got = 0;
    sendSetup({ phase: 'setup', title: 'Preparando el cerebro', detail: 'Descargando dependencias (la primera vez tarda unos minutos)…', percent: 16 });
    await runCmd(VENV_PY, ['-m', 'pip', 'install', '-r', 'requirements.txt', '--no-input'], {
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
    sendSetup({ phase: 'setup', title: 'Preparando el cerebro', detail: 'Dependencias instaladas.', percent: 90 });

    // 3. .env y recursos
    writeEnvFromExample();
    sendSetup({ phase: 'setup', title: 'Preparando el cerebro', detail: 'Generando recursos…', percent: 94 });
    try {
      await runCmd(VENV_PY, ['-c', 'import scripts.make_assets; scripts.make_assets.main()'], { onLine: () => {} });
    } catch { /* no crítico */ }

    try { fs.writeFileSync(VENV_OK, 'ok'); } catch { /* noop */ }
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
    show: false,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  hudWindow.loadFile(path.join(__dirname, 'ui', 'index.html'));
  hudWindow.once('ready-to-show', () => hudWindow.show());
  hudWindow.on('minimize', (e) => { e.preventDefault(); showOrb(); });
  hudWindow.on('closed', () => { hudWindow = null; });
  createOrbWindow();

  if (process.env.NODE_ENV === 'dev') {
    hudWindow.webContents.openDevTools({ mode: 'detach' });
  }
}

// Ventana del orbe flotante (aparece al minimizar la ventana principal)
function createOrbWindow() {
  if (orbWindow) return;
  const workArea = require('electron').screen.getPrimaryDisplay().workArea;
  orbWindow = new BrowserWindow({
    width: 170,
    height: 170,
    frame: false,
    transparent: true,
    alwaysOnTop: true,
    resizable: false,
    skipTaskbar: true,
    show: false,
    backgroundColor: '#00000000',
    x: workArea.x + workArea.width - 186,
    y: workArea.y + workArea.height - 186,
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });
  orbWindow.loadFile(path.join(__dirname, 'ui', 'orb.html'));
  orbWindow.setAlwaysOnTop(true, 'screen-saver');
}

function showOrb() {
  if (!hudWindow) return;
  hudWindow.hide();
  if (orbWindow) orbWindow.show();
}

function restoreMain() {
  if (orbWindow) orbWindow.hide();
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
// Tray
// ------------------------------------------------------------------ //
function createTray() {
  const iconPath = path.join(ASSETS_DIR, 'tray-icon.png');
  tray = new Tray(fs.existsSync(iconPath) ? iconPath : emptyIcon());
  tray.setToolTip('JARVIS');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Mostrar/Activar', click: () => restoreMain() },
    { label: 'Reiniciar brain', click: () => { brainProcess = null; startBrain(); } },
    { label: 'Abrir carpeta del proyecto', click: () => shell.openPath(ROOT_DIR) },
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
    }
  });

  // Al cerrar el wizard (guardó claves o saltó), abrimos el HUD y arrancamos el brain.
  // Sin claves JARVIS igualmente funciona en modo limitado con el fallback local (Ollama).
  wizardWindow?.on('closed', () => {
    wizardWindow = null;
    if (!hudWindow) {
      createHud();
      startBrain();
    }
  });
}

app.whenReady().then(() => {
  bootstrap();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) bootstrap();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
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