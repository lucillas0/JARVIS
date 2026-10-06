// Banco de pruebas: reproduce EXACTAMENTE lo que hace el renderer de JARVIS
// al abrir la cámara (origen file://, <img> fuera de pantalla alimentando un
// canvas con captureStream(0)+requestFrame) y dice qué falla y por qué.
// No toca la app real: es un proceso Electron aparte que se cierra solo.
const { app, BrowserWindow } = require('electron');
const path = require('path');

const resultados = [];
const decir = (m) => { resultados.push(String(m)); console.log('[PROBE] ' + m); };

app.commandLine.appendSwitch('disable-gpu-shader-disk-cache');

app.whenReady().then(async () => {
  const win = new BrowserWindow({
    width: 480,
    height: 360,
    show: true,
    webPreferences: {
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  win.webContents.on('console-message', (_e, a, b, c) => {
    // Electron 31: (event, level, message, line, sourceId)
    const nuevo = typeof a === 'object' && a && 'message' in a; // Electron >= 37
    const msg = nuevo ? a.message : b;
    const line = nuevo ? a.lineNumber : c;
    decir('renderer> ' + msg + (typeof line === 'number' ? ' (línea ' + line + ')' : ''));
  });

  win.webContents.on('render-process-gone', (_e, det) =>
    decir('!!! el renderer murió: ' + JSON.stringify(det)));

  await win.loadFile(path.join(__dirname, 'index.html'));

  // Margen generoso: la cámara tarda en despertar y el stream MJPEG también.
  setTimeout(() => {
    decir('--- fin del sondeo ---');
    console.log('\n===== RESULTADO =====\n' + resultados.join('\n') + '\n=====================\n');
    app.exit(0);
  }, 40000);

  win.webContents.send('arranca');
});
