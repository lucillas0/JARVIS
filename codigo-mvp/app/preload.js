const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('jarvisAPI', {
  getHistory: (limit) => ipcRenderer.invoke('history:get', limit),
  addMessage: (role, content) => ipcRenderer.invoke('history:add', { role, content }),
});
