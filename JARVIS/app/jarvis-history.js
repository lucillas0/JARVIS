const fs = require('fs');
const Database = require('better-sqlite3');

const DEFAULT_LIMIT = 100;
const MAX_LIMIT = 300;

function readJarvisConversation(databasePath, requestedLimit = DEFAULT_LIMIT) {
  if (!databasePath || !fs.existsSync(databasePath)) return [];

  const numericLimit = Number(requestedLimit);
  const limit = Number.isFinite(numericLimit)
    ? Math.max(1, Math.min(MAX_LIMIT, Math.trunc(numericLimit)))
    : DEFAULT_LIMIT;
  const db = new Database(databasePath, { readonly: true, fileMustExist: true });
  try {
    db.pragma('busy_timeout = 2000');
    return db.prepare(
      "SELECT id, role, content, created_at FROM messages " +
      "WHERE role IN ('user', 'assistant') ORDER BY id DESC LIMIT ?",
    ).all(limit).reverse();
  } finally {
    db.close();
  }
}

module.exports = { readJarvisConversation };
