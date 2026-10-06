const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const Database = require('better-sqlite3');

let db;

function initDb() {
  db = new Database(path.join(app.getPath('userData'), 'jarvis.db'));
  db.exec(`
    CREATE TABLE IF NOT EXISTS messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      role TEXT NOT NULL,
      content TEXT NOT NULL,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    );
  `);
}

function createWindow() {
  const win = new BrowserWindow({
    width: 900,
    height: 700,
    backgroundColor: '#0b0d10',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
    },
  });
  win.loadFile('index.html');
}

app.whenReady().then(() => {
  initDb();
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit();
});

ipcMain.handle('history:get', (_event, limit = 20) => {
  return db
    .prepare('SELECT role, content, created_at FROM messages ORDER BY id DESC LIMIT ?')
    .all(limit)
    .reverse();
});

ipcMain.handle('history:add', (_event, { role, content }) => {
  db.prepare('INSERT INTO messages (role, content) VALUES (?, ?)').run(role, content);
  return true;
});
