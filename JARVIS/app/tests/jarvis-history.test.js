const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const test = require('node:test');
const Database = require('better-sqlite3');
const { readJarvisConversation } = require('../jarvis-history');

function withHistory(run) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'jarvis-history-'));
  const databasePath = path.join(dir, 'jarvis.db');
  const db = new Database(databasePath);
  try {
    db.exec(`CREATE TABLE messages (
      id INTEGER PRIMARY KEY AUTOINCREMENT,
      role TEXT NOT NULL,
      content TEXT NOT NULL,
      created_at TEXT DEFAULT CURRENT_TIMESTAMP
    )`);
    run(db, databasePath);
  } finally {
    db.close();
    fs.rmSync(dir, { recursive: true, force: true });
  }
}

test('reads recent JARVIS user/assistant turns in chronological order, ignoring system rows', () => {
  withHistory((db, file) => {
    db.prepare('INSERT INTO messages (role, content, created_at) VALUES (?, ?, ?)').run('user', 'pregunta antigua', '2026-10-08 10:00:00');
    db.prepare('INSERT INTO messages (role, content, created_at) VALUES (?, ?, ?)').run('assistant', 'respuesta antigua', '2026-10-08 10:00:01');
    db.prepare('INSERT INTO messages (role, content, created_at) VALUES (?, ?, ?)').run('system', 'interno', '2026-10-08 10:00:02');
    db.prepare('INSERT INTO messages (role, content, created_at) VALUES (?, ?, ?)').run('user', 'pregunta nueva', '2026-10-08 10:00:03');
    db.prepare('INSERT INTO messages (role, content, created_at) VALUES (?, ?, ?)').run('assistant', 'respuesta nueva', '2026-10-08 10:00:04');

    assert.deepEqual(readJarvisConversation(file, 3).map(({ role, content }) => [role, content]), [
      ['assistant', 'respuesta antigua'],
      ['user', 'pregunta nueva'],
      ['assistant', 'respuesta nueva'],
    ]);
    assert.equal(db.prepare('SELECT COUNT(*) AS n FROM messages').get().n, 5);
  });
});

test('missing database returns an empty history without creating files', () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'jarvis-history-missing-'));
  try {
    const file = path.join(dir, 'missing.db');
    assert.deepEqual(readJarvisConversation(file), []);
    assert.equal(fs.existsSync(file), false);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test('history limit is clamped and does not mutate persistent conversation data', () => {
  withHistory((db, file) => {
    for (let i = 0; i < 5; i++) {
      db.prepare('INSERT INTO messages (role, content) VALUES (?, ?)').run(i % 2 ? 'assistant' : 'user', `message ${i}`);
    }
    assert.equal(readJarvisConversation(file, 0).length, 1);
    assert.equal(readJarvisConversation(file, 9999).length, 5);
    assert.equal(db.prepare('SELECT COUNT(*) AS n FROM messages').get().n, 5);
  });
});
