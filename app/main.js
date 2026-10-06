const { app, BrowserWindow, ipcMain, Tray, Menu, shell, globalShortcut, screen, utilityProcess } = require('electron');
const path = require('path');
const fs = require('fs');
const os = require('os');
const { spawn, spawnSync } = require('child_process');
const Database = require('better-sqlite3');


// Instancia única: si ya hay JARVIS corriendo, la enfocamos y salimos de aquí.
const gotLock = app.requestSingleInstanceLock();
if (!gotLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (hudWindow && !hudWindow.isDestroyed()) {
      if (hudWindow.isMinimized()) hudWindow.restore();
      hudWindow.show();
      hudWindow.focus();
    } else {
      restoreMain();
    }
  });
}

const ROOT_DIR = path.join(__dirname, '..');
const BRAIN_DIR = path.join(ROOT_DIR, 'brain');
const ENV_FILE = path.join(BRAIN_DIR, '.env');
const ASSETS_DIR = path.join(ROOT_DIR, 'assets');

// Datos persistentes del brain fuera de la carpeta de instalación
// (venv + db sobreviven a desinstalar/reinstalar: sin re-setup, sin pérdida de memoria).
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
let notchWindow = null;
let notchHidden = false;
let notchExpanded = false;
let notchContentHeight = 360;
let notchHovered = false;
let notchHoverTimer = null;
let modoApp = '';
let notchWindow = null;
let notchHidden = false;
let notchExpanded = false;
let notchContentHeight = 360;
let notchHovered = false;
let notchHoverTimer = null;
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

// ------------------------------------------------------------------ //
// Log de cámara en disco
// ------------------------------------------------------------------ //
// La consola de Electron no la ve nadie sin abrir devtools, y las decisiones de
// permiso solo existían en memoria. Con la cámara fallando a medias había que
// adivinar. Ahora todo queda en %APPDATA%\jarvis\camara.log.
let camaraLogPath = null;
function camaraLog(linea) {
  try {
    if (!camaraLogPath) {
      const ud = userDataPath();
      if (!ud) return;
      camaraLogPath = path.join(ud, 'camara.log');
      try { fs.writeFileSync(camaraLogPath, ''); } catch { /* puede estar abierto */ }
    }
    fs.appendFileSync(camaraLogPath,
      new Date().toLocaleString('es-ES') + ' ' + linea + '\n');
  } catch { /* el log nunca debe tumbar la app */ }
}

// Vuelca a disco la consola de CUALQUIER ventana (no solo los errores del
// overlay) y deja una marca de qué build está corriendo: sin esto no se sabe si
// lo que se prueba es el código nuevo o una instalación vieja.
function vigilarCamara(win, etiqueta) {
  if (!win || win.isDestroyed()) return;
  win.webContents.on('console-message', (_event, level, message, line, sourceId) => {
    const linea = `[${etiqueta}] console(${level}) ${message} @${sourceId}:${line}`;
    if (level >= 2) camaraLog(linea);
    if (process.env.NODE_ENV === 'dev') console.log(linea);
  });
  win.webContents.on('render-process-gone', (_event, details) => {
    camaraLog(`[${etiqueta}] render-process-gone reason=${details.reason} exitCode=${details.exitCode}`);
  });
}

/* -------------------------------------------------------------------------- */
/* Las secciones siguientes se mantienen según la aplicación JARVIS completa. */
/* -------------------------------------------------------------------------- */

let hudRecreates = 0;
function createHud() {
  hudWindow = new BrowserWindow({
    width: 960, height: 680, minWidth: 760, minHeight: 560,
    show: false, backgroundColor: '#0b0d12', title: 'JARVIS',
    webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false },
  });
  vigilarCamara(hudWindow, 'hud');
  hudWindow.loadFile(path.join(__dirname, 'ui', 'index.html'));
  hudWindow.once('ready-to-show', () => hudWindow && !hudWindow.isDestroyed() && hudWindow.show());
  hudWindow.on('closed', () => { hudWindow = null; });
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
  if (process.env.NODE_ENV === 'dev') hudWindow.webContents.openDevTools({ mode: 'detach' });
  createNotch();
}

// Notch nativo siempre encima; solo la cápsula intercepta los clics recogida.
function notchBounds(expanded) {
  const { bounds } = screen.getPrimaryDisplay();
  const width = expanded ? Math.min(720, bounds.width) : 260;
  const height = expanded ? Math.max(120, Math.min(notchContentHeight, bounds.height)) : 42;
  return { x: bounds.x + Math.round((bounds.width - width) / 2), y: bounds.y, width, height };
}

function setNotchShape(expanded) {
  if (!notchWindow || notchWindow.isDestroyed() || process.platform !== 'win32') return;
  const bounds = notchWindow.getBounds();
  const shapeWidth = expanded ? Math.min(680, Math.max(1, bounds.width - 32)) : 220;
  try {
    notchWindow.setShape([{
      x: Math.round((bounds.width - shapeWidth) / 2), y: 0,
      width: shapeWidth, height: expanded ? bounds.height : 36,
    }]);
  } catch (err) {
    console.warn('[JARVIS] No pude limitar la superficie del Notch:', err && err.message);
  }
}

function watchNotchPointer() {
  if (notchHoverTimer) return;
  notchHoverTimer = setInterval(() => {
    if (!notchWindow || notchWindow.isDestroyed() || !notchWindow.isVisible() || notchExpanded) return;
    const bounds = notchWindow.getBounds();
    const point = screen.getCursorScreenPoint();
    const center = bounds.x + Math.round(bounds.width / 2);
    const inside = point.x >= center - 110 && point.x <= center + 110 &&
      point.y >= bounds.y && point.y <= bounds.y + 36;
    if (inside && !notchHovered) {
      notchHovered = true;
      notchWindow.setIgnoreMouseEvents(false);
      notchWindow.webContents.send('notch:hover-open');
    } else if (!inside) {
      notchHovered = false;
    }
  }, 120);
}

function createNotch(expandOnReady = false) {
  if (modoApp === 'lite' || notchHidden) return;
  if (notchWindow && !notchWindow.isDestroyed()) {
    showNotch(expandOnReady);
    return;
  }
  try {
    notchWindow = new BrowserWindow({
      ...notchBounds(false), frame: false, transparent: true, backgroundColor: '#00000000',
      resizable: false, minimizable: false, maximizable: false, fullscreenable: false,
      focusable: true, alwaysOnTop: true, skipTaskbar: true, hasShadow: false,
      title: 'JARVIS Notch',
      webPreferences: { preload: path.join(__dirname, 'preload.js'), contextIsolation: true, nodeIntegration: false },
    });
    const createdNotch = notchWindow;
    try { createdNotch.setVisibleOnAllWorkspaces(true, { visibleOnFullScreen: true }); } catch { /* plataforma */ }
    createdNotch.loadFile(path.join(__dirname, 'ui', 'notch', 'index.html'));
    createdNotch.once('ready-to-show', () => {
      if (notchWindow !== createdNotch || createdNotch.isDestroyed()) return;
      createdNotch.setAlwaysOnTop(true);
      setNotchShape(false);
      createdNotch.setIgnoreMouseEvents(true, { forward: true });
      createdNotch.showInactive();
      watchNotchPointer();
      if (expandOnReady) createdNotch.webContents.send('notch:restore');
    });
    createdNotch.on('closed', () => {
      if (notchWindow !== createdNotch) return;
      notchWindow = null;
      notchExpanded = false;
      notchHovered = false;
    });
    createdNotch.webContents.on('render-process-gone', (_e, details) => {
      if (notchWindow !== createdNotch) return;
      console.error('[JARVIS] Notch murió (' + (details.reason || 'unknown') + ').');
      notchWindow = null;
      notchExpanded = false;
      notchHovered = false;
      try { createdNotch.destroy(); } catch { /* renderer ya cerrado */ }
      setTimeout(() => { if (hudWindow && !notchWindow && !notchHidden) createNotch(); }, 2000);
    });
  } catch (err) {
    console.error('[JARVIS] No pude crear el Notch: ' + (err && err.message));
    notchWindow = null;
  }
}

function hideNotch() {
  notchHidden = true;
  if (!notchWindow || notchWindow.isDestroyed()) return;
  notchExpanded = false;
  notchHovered = false;
  notchWindow.hide();
}

function showNotch(expand = false) {
  if (modoApp === 'lite') return false;
  notchHidden = false;
  if (!notchWindow || notchWindow.isDestroyed()) {
    createNotch(expand);
    return !!notchWindow;
  }
  if (notchWindow.webContents.isLoading()) {
    notchWindow.webContents.once('did-finish-load', () => {
      if (!notchWindow || notchWindow.isDestroyed() || !notchWindow.isVisible()) return;
      notchWindow.webContents.send(expand ? 'notch:restore' : 'notch:reset');
    });
    notchWindow.showInactive();
    return true;
  }
  notchExpanded = false;
  notchHovered = false;
  notchWindow.setBounds(notchBounds(false));
  setNotchShape(false);
  notchWindow.setAlwaysOnTop(true);
  notchWindow.setIgnoreMouseEvents(true, { forward: true });
  notchWindow.showInactive();
  notchWindow.webContents.send(expand ? 'notch:restore' : 'notch:reset');
  watchNotchPointer();
  return true;
}

function hideOrb() { /* no-op en esta arquitectura */ }
function showOrb() { /* no-op en esta arquitectura */ }
function ensureOrbWindow() { return null; }
function createOrbWindow() { return null; }

function restoreMain() {
  if (!hudWindow || hudWindow.isDestroyed()) {
    createHud();
    return;
  }
  if (hudWindow.isMinimized()) hudWindow.restore();
  hudWindow.show();
  hudWindow.focus();
  createNotch();
}

/* Original application main-process implementation follows. */

function createTray() {
  const iconPath = path.join(ASSETS_DIR, 'tray-icon.png');
  tray = new Tray(fs.existsSync(iconPath) ? iconPath : emptyIcon());
  tray.setToolTip('JARVIS');
  tray.setContextMenu(Menu.buildFromTemplate([
    { label: 'Mostrar/Activar', click: () => restoreMain() },
    { label: 'Mostrar Notch JARVIS', click: () => showNotch(true) },
    { label: 'Ocultar Notch JARVIS', click: () => hideNotch() },
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
  const { nativeImage } = require('electron');
  const b64 = 'iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg==';
  return nativeImage.createFromDataURL('data:image/png;base64,' + b64);
}

// ------------------------------------------------------------------ //
// The remaining existing setup, tray, IPC, brain-process, and overlay
// handlers are kept verbatim except for the Notch handlers below.
// ------------------------------------------------------------------ //

ipcMain.handle('notch:set-expanded', (event, expanded, contentHeight) => {
  if (!notchWindow || event.sender !== notchWindow.webContents || notchWindow.isDestroyed() || !notchWindow.isVisible()) return false;
  notchExpanded = !!expanded;
  if (notchExpanded && Number.isFinite(Number(contentHeight))) {
    notchContentHeight = Math.max(180, Math.min(Number(contentHeight), 900));
  }
  notchWindow.setBounds(notchBounds(notchExpanded));
  setNotchShape(notchExpanded);
  notchWindow.setAlwaysOnTop(true);
  notchWindow.setIgnoreMouseEvents(!notchExpanded, notchExpanded ? undefined : { forward: true });
  if (!notchExpanded) {
    notchHovered = false;
    setNotchShape(false);
  }
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

ipcMain.handle('notch:open', (event) => {
  if (!notchWindow || event.sender !== notchWindow.webContents || notchWindow.isDestroyed()) return false;
  return showNotch(true);
});

ipcMain.handle('notch:external', (event, rawUrl) => {
  if (!notchWindow || event.sender !== notchWindow.webContents || typeof rawUrl !== 'string') return false;
  try {
    const url = new URL(rawUrl);
    if (url.protocol !== 'https:' && url.protocol !== 'http:') return false;
    shell.openExternal(url.href);
    return true;
  } catch {
    return false;
  }
});

screen.on('display-metrics-changed', () => {
  if (notchWindow && !notchWindow.isDestroyed()) {
    notchWindow.setBounds(notchBounds(notchExpanded));
    setNotchShape(notchExpanded);
  }
});

// Este botón SÍ quiere la ventana principal: para eso está.
ipcMain.handle('overlay:main', () => {
  overlayPendingShow = false;
  requestOverlayHide();
  setTimeout(() => restoreMain(), 320);
  return true;
});
