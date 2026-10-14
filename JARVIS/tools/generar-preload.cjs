// Genera preload.js con un puente POR VENTANA.
//
// El problema que arregla: antes habia un unico preload para las cinco ventanas,
// asi que todas heredaban todos los canales. El overlay, que abre <webview> con
// URLs que elige el modelo y que va con webSecurity:false, tenia acceso a
// jarvisAPI.getEnv() (las claves de API de tu cuenta) y a jarvisAPI.quit()
// (cerrar la aplicacion) sin necesitarlos para nada.
//
// La idea es minima: cada ventana se identifica con su rol mediante
// additionalArguments, y el preload solo expone lo que ese rol necesita.

const fs = require('fs');
const path = require('path');

// __dirname es JARVIS/tools, asi que la app esta un nivel arriba.
const APP = path.join(__dirname, '..', 'app');
const OUT = path.join(APP, 'preload.generado.js');

CABECERA = `// GENERADO por tools/generar-preload.mjs. No editar a mano: se regenera.
// El original esta en preload.js y es la plantilla; este es el que se carga.
'use strict';
const { contextBridge, ipcRenderer } = require('electron');

// El rol lo pasa la ventana al crearse (webPreferences.additionalArguments).
const _rol = (process.argv.find((a) => a.startsWith('--jarvis-role=')) || '').split('=')[1] || '';
function es(...roles) { return roles.includes(_rol); }

function subscribe(channel, callback) {
  if (typeof callback !== 'function') return () => {};
  const listener = (_event, payload) => callback(payload);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
}

`;

const PLANTILLA = process.argv[2] || path.join(APP, 'preload.js');
const cuerpo = fs.readFileSync(PLANTILLA, 'utf8');

// Se quita el bloque de exposicion del original y se reconstruye por rol.
// Antes de nada, todo lo que hay por encima del primer exposeInMainWorld: son
// los require y el helper subscribe, que ya van en la cabecera.
const marcas = ['jarvisAPI', 'jarvisNet', 'jarvisNotchNet', 'jarvisSetupAPI',
               'overlayAPI', 'jarvisNotchAPI'];
let sinCuerpo = cuerpo;
const corte = sinCuerpo.indexOf('contextBridge.exposeInMainWorld');
if (corte > 0) sinCuerpo = sinCuerpo.slice(corte);
for (const m of marcas) {
  // El cierre va indentado en unos bloques ("  });") y sin indentar en otros,
  // asi que se admiten las dos formas.
  const re = new RegExp(`contextBridge\\.exposeInMainWorld\\('${m}'[\\s\\S]*?\\n[ \\t]*\\}\\);`, 'm');
  const antes = sinCuerpo;
  sinCuerpo = sinCuerpo.replace(re, '');
  if (sinCuerpo === antes) console.warn('  aviso: no se quito el bloque ' + m);
}

const ROLES = `
/* ── El HUD ──────────────────────────────────────────────────────────────
 * Solo necesita el token para su WebSocket. Antes heredaba las claves de API
 * y el cierre de la app sin usarlas para nada.
 */
if (es('hud')) {
  contextBridge.exposeInMainWorld('jarvisNet', {
    token: () => ipcRenderer.invoke('jarvis:token'),
    query: () => ipcRenderer.invoke('jarvis:token-query'),
  });
}

/* ── La pizarra (overlay) ───────────────────────────────────────────────
 * Esta es la que abre <webview> con URLs que decide el modelo, con lo que
 * cualquier contenido que se colase en la pagina tendria aqui su puente.
 * Se queda solo con lo que la pizarra usa de verdad.
 */
if (es('overlay')) {
  contextBridge.exposeInMainWorld('jarvisNet', {
    token: () => ipcRenderer.invoke('jarvis:token'),
    query: () => ipcRenderer.invoke('jarvis:token-query'),
  });
  contextBridge.exposeInMainWorld('overlayAPI', {
    close: () => ipcRenderer.invoke('overlay:close'),
    main: () => ipcRenderer.invoke('overlay:main'),
    getApps: () => ipcRenderer.invoke('overlay:apps'),
    getRecent: () => ipcRenderer.invoke('overlay:recent'),
    listDir: (dir) => ipcRenderer.invoke('overlay:dir', dir || null),
    open: (item) => ipcRenderer.invoke('overlay:open', item),
    onPerf: (cb) => subscribe('perf:update', cb),
    // El puente de Coucou, que se engancha a la pizarra.
    onWorkspace: (cb) => subscribe('workspace:state', cb),
  });
}

/* ── La isla (notch) ────────────────────────────────────────────────────
 * Es la unica que habla con el usuario por chat, asi que necesita su puente
 * entero. Aun asi, no ve las claves ni el cierre de la app.
 */
if (es('notch')) {
  contextBridge.exposeInMainWorld('jarvisNotchAPI', __notch());
  contextBridge.exposeInMainWorld('jarvisNotchNet', {
    query: () => ipcRenderer.invoke('jarvis:token-query'),
  });
}

/* ── El asistente de instalacion ────────────────────────────────────────
 * Es la unica que puede escribir claves, y solo las que esten en la lista
 * blanca. Sin getEnv(): no tiene por que poder LEERLAS.
 */
if (es('wizard')) {
  contextBridge.exposeInMainWorld('jarvisAPI', {
    saveKeys: (keys) => ipcRenderer.invoke('env:save', keys),
    skipKeySetup: () => ipcRenderer.invoke('env:skip-wizard'),
    openExternal: (url) => ipcRenderer.invoke('notch:external', url),
  });
}

/* ── La pantalla de carga ───────────────────────────────────────────────
 * Tres canales: avanzar, reintentar y salir. Nada mas.
 */
if (es('splash')) {
  contextBridge.exposeInMainWorld('jarvisSetupAPI', {
    retry: () => ipcRenderer.invoke('setup:retry'),
    quit: () => ipcRenderer.invoke('app:quit'),
    onProgress: (cb) => subscribe('setup:progress', cb),
  });
}

/* ── Sin rol: no se expone nada ─────────────────────────────────────────
 * Una ventana que se abriese sin declararlo se queda sin puente, que es lo
 * que tiene que pasar. Antes heredaba el de todos.
 */
`;

const NOTCH = `
function __notch() {
  return {
    getConversation: (limit) => ipcRenderer.invoke('notch:get-conversation', limit),
    sendMessage: (message, context) => ipcRenderer.invoke('notch:send-message', message, context),
    resetConversation: () => ipcRenderer.invoke('notch:reset-conversation'),
    focusPill: (id) => ipcRenderer.invoke('notch:focus-pill', id),
    getSettings: () => ipcRenderer.invoke('notch:get-settings'),
    saveSettings: (s) => ipcRenderer.invoke('notch:save-settings', s),
    openSettings: () => ipcRenderer.invoke('notch:settings'),
    log: (message) => ipcRenderer.invoke('notch:log', message),
    setExpanded: (expanded, height) => ipcRenderer.invoke('notch:set-expanded', expanded, height),
    hide: () => ipcRenderer.invoke('notch:hide'),
    openMain: () => ipcRenderer.invoke('notch:main'),
    restore: () => ipcRenderer.invoke('notch:restore'),
    open: () => ipcRenderer.invoke('notch:open'),
    openExternal: (url) => ipcRenderer.invoke('notch:external', url),
    openApp: (id) => ipcRenderer.invoke('notch:open-app', id),
    onEvent: (cb) => subscribe('brain:event', cb),
    onHoverOpen: (cb) => subscribe('notch:hover-open', cb),
    onHoverOut: (cb) => subscribe('notch:hover-out', cb),
    onConversationChanged: (cb) => subscribe('notch:conversation-changed', cb),
    onIslandToggle: (cb) => subscribe('island:toggle', cb),
    onIslandTab: (cb) => subscribe('island:tab', cb),
    onIslandSend: (cb) => subscribe('island:send', cb),
    onIslandMute: (cb) => subscribe('island:mute', cb),
    onRestore: (cb) => subscribe('notch:restore', cb),
    onReset: (cb) => subscribe('notch:reset', cb),
    onHide: (cb) => subscribe('notch:hidden', cb),
    onAutoCollapse: (cb) => subscribe('notch:auto-collapse', cb),
    setIslandRect: (x, y, w, h) => ipcRenderer.invoke('notch:island-rect', x, y, w, h),
    setCollapsed: (c) => ipcRenderer.invoke('notch:collapsed', c),
    focusWindow: (f) => ipcRenderer.invoke('notch:focus-window', f),
    openPath: (p) => ipcRenderer.invoke('notch:open-path', p),
    savePng: (data, name) => ipcRenderer.invoke('notch:save-png', data, name),
    quit: () => ipcRenderer.invoke('notch:quit'),
  };
}
`;

const salida = CABECERA + sinCuerpo.trim() + '\n' + NOTCH + ROLES;
fs.writeFileSync(OUT, salida, 'utf8');
console.log('escrito ' + OUT + ' (' + salida.split('\n').length + ' lineas)');