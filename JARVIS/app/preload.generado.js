// GENERADO por tools/generar-preload.mjs. No editar a mano: se regenera.
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

// El token del cerebro, para que los renderers puedan abrir su WebSocket.
// El brain ya no acepta una conexion sin el (ver brain/auth.py).
//
// Va aparte y en su propio objeto, y no dentro de jarvisAPI, por una razon:
// jarvisAPI.getEnv() devuelve las claves de API y jarvisAPI.quit() cierra la
// aplicacion, y el preload es el MISMO para las cinco ventanas. El overlay, que
// abre <webview> con URLs que elige el modelo, venia heredando las dos cosas.
// Separar el token deja claro que no es lo mismo que un permiso, y permite
// darselo solo a quien lo necesita (ver main.js y el puente del notch).


// El notch abre su propio WebSocket (jarvis-coucou.js) y el puente de Coucou
// tambien (core/bridge.js), asi que necesita el token por su cuenta. Se expone
// aparte y con otro nombre para que quede claro que es lo mismo y no dos
// permisos distintos.

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
