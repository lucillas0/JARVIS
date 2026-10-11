const { contextBridge, ipcRenderer } = require('electron');

function subscribe(channel, callback) {
  if (typeof callback !== 'function') return () => {};
  const listener = (_event, payload) => callback(payload);
  ipcRenderer.on(channel, listener);
  return () => ipcRenderer.removeListener(channel, listener);
}

contextBridge.exposeInMainWorld('jarvisAPI', {
  getHistory: (limit) => ipcRenderer.invoke('history:get', limit),
  saveKeys: (keys) => ipcRenderer.invoke('env:save', keys),
  skipKeySetup: () => ipcRenderer.invoke('env:skip-wizard'),
  saveKey: (name, value) => ipcRenderer.invoke('env:save', { [name]: value }),
  openExternal: (url) => ipcRenderer.invoke('notch:external', url),
  toggleNotch: () => ipcRenderer.invoke('notch:toggle'),
  addHistory: (message) => ipcRenderer.invoke('history:add', message),
  toggleFullscreen: () => ipcRenderer.invoke('window:toggleFullscreen'),
  restore: () => ipcRenderer.invoke('window:restore'),
  retrySetup: () => ipcRenderer.invoke('setup:retry'),
  saveEnv: (keys) => ipcRenderer.invoke('env:save', keys),
  getEnv: () => ipcRenderer.invoke('env:get'),
  quit: () => ipcRenderer.invoke('app:quit'),
  onEvent: (callback) => subscribe('brain:event', callback),
  onSetupProgress: (callback) => subscribe('setup:progress', callback),
});

contextBridge.exposeInMainWorld('jarvisSetupAPI', {
  retry: () => ipcRenderer.invoke('setup:retry'),
  quit: () => ipcRenderer.invoke('app:quit'),
  onProgress: (callback) => subscribe('setup:progress', callback),
});

contextBridge.exposeInMainWorld('overlayAPI', {
  close: () => ipcRenderer.invoke('overlay:close'),
  main: () => ipcRenderer.invoke('overlay:main'),
  getApps: () => ipcRenderer.invoke('overlay:apps'),
  getRecent: () => ipcRenderer.invoke('overlay:recent'),
  listDir: (dir) => ipcRenderer.invoke('overlay:dir', dir || null),
  open: (item) => ipcRenderer.invoke('overlay:open', item),
  onPerf: (callback) => subscribe('perf:update', callback),
});

contextBridge.exposeInMainWorld('jarvisNotchAPI', {
  getConversation: (limit) => ipcRenderer.invoke('notch:jarvis-history', limit),
  setExpanded: (expanded, height) => ipcRenderer.invoke('notch:set-expanded', expanded, height),
  hide: () => ipcRenderer.invoke('notch:hide'),
  openMain: () => ipcRenderer.invoke('notch:main'),
  restore: () => ipcRenderer.invoke('notch:restore'),
  open: () => ipcRenderer.invoke('notch:open'),
  openExternal: (url) => ipcRenderer.invoke('notch:external', url),
  // El notch llama a estos cuatro y el puente no los tenia. Con '?.' no
  // reventaba al cargar, pero al pulsar un acceso rapido '.then' sobre un
  // undefined si que peta, y por eso el notch se quedaba medio muerto.
  openApp: (id) => ipcRenderer.invoke('notch:open-app', id),
  onEvent: (callback) => subscribe('brain:event', callback),
  onHoverOpen: (callback) => subscribe('notch:hover-open', callback),
  onHoverOut: (callback) => subscribe('notch:hover-out', callback),
  // Atajos del island, registrados en el proceso principal (Win+Shift+...).
  onIslandToggle: (callback) => subscribe('island:toggle', callback),
  onIslandTab: (callback) => subscribe('island:tab', callback),
  onIslandSend: (callback) => subscribe('island:send', callback),
  onIslandMute: (callback) => subscribe('island:mute', callback),
  onRestore: (callback) => subscribe('notch:restore', callback),
  onReset: (callback) => subscribe('notch:reset', callback),
  onHide: (callback) => subscribe('notch:hidden', callback),
  onAutoCollapse: (callback) => subscribe('notch:auto-collapse', callback),
onAgentState: (callback) => subscribe('notch:agent-state', callback),
    // Puente de Coucou: el island real usa Bridge (core/bridge.ts), que aqui
    // habla con el proceso principal en vez de con Tauri.
    setIslandRect: (x, y, w, h) => ipcRenderer.invoke('notch:island-rect', x, y, w, h),
    setCollapsed: (collapsed) => ipcRenderer.invoke('notch:collapsed', collapsed),
    focusWindow: (focused) => ipcRenderer.invoke('notch:focus-window', focused),
    openPath: (p) => ipcRenderer.invoke('notch:open-path', p),
    savePng: (data, name) => ipcRenderer.invoke('notch:save-png', data, name),
    quit: () => ipcRenderer.invoke('notch:quit'),
  });