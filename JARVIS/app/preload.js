) => ipcRenderer.invoke('overlay:apps'),
  getRecent: () => ipcRenderer.invoke('overlay:recent'),
  listDir: (dir) => ipcRenderer.invoke('overlay:dir', dir || null),
  open: (item) => ipcRenderer.invoke('overlay:open', item),
  onPerf: (cb) => ipcRenderer.on('perf:update', (_e, data) => cb(data)),
});