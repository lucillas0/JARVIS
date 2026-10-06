ew-Object -ComObject WScript.Shell;',
    '$roots = @(' + roots.map((r) => "'" + r.replace(/'/g, "''") + "'").join(',') + ');',
    'foreach ($root in $roots) {',
    '  Get-ChildItem -LiteralPath $root -Filter *.lnk -Recurse -Depth 2 -File -ErrorAction SilentlyContinue | ForEach-Object {',
    "    if ($_.Name -like 'uninstall*') { return; }",
    '    try { $t = $sh.CreateShortcut($_.FullName).TargetPath } catch { $t = "" }',
    "    if ($t -match 'search|web%') { return; }",
    '    Write-Output ($_.BaseName + "`t" + $_.FullName + "`t" + $t)',
    '  }',
    '}',
  ].join('\n');
  const out = execFileSync('powershell', [
    '-NoProfile', '-NonInteractive', '-Command', script,
  ], { timeout: 20000, encoding: 'utf8', windowsHide: true, maxBuffer: 4 * 1024 * 1024 });
  const seen = new Set();
  const apps = [];
  for (const line of String(out || '').split(/\r?\n/)) {
    if (!line.includes('\t')) continue;
    const parts = line.split('\t');
    const name = (parts[0] || '').trim();
    const lnk = (parts[1] || '').trim();
    const target = (parts[2] || '').trim();
    if (!name || !lnk) continue;
    const key = name.toLowerCase();
    if (seen.has(key)) continue;
    seen.add(key);
    apps.push({ name, path: lnk, target: target || lnk, type: 'app' });
  }
  return apps;
}

// Fallback sin spawns: recorre los .lnk con fs y usa la ruta del .lnk como
// destino (shell.openPath resuelve .lnk de forma nativa). Rápido siempre.
function listAppsFallback() {
  const startPaths = [
    process.env.APPDATA && path.join(process.env.APPDATA, 'Microsoft', 'Windows', 'Start Menu', 'Programs'),
    process.env['ProgramData'] && path.join(process.env['ProgramData'], 'Microsoft', 'Windows', 'Start Menu', 'Programs'),
  ].filter(Boolean);
  const out = [];
  const seen = new Set();
  const walk = (dir, depth) => {
    if (!dir || depth > 2) return;
    let names;
    try {
      if (!fs.existsSync(dir)) return;
      names = fs.readdirSync(dir, { withFileTypes: true });
    } catch { return; }
    for (const d of names) {
      const n = d.name.toLowerCase();
      if (n.startsWith('uninstall') || !n.endsWith('.lnk')) {
        if (d.isDirectory()) walk(path.join(dir, d.name), depth + 1);
        continue;
      }
      if (seen.has(n)) continue;
      seen.add(n);
      const ln = path.join(dir, d.name);
      out.push({ name: d.name.replace(/\.lnk$/i, ''), path: ln, target: ln, type: 'app' });
    }
  };
  for (const sp of startPaths) walk(sp, 0);
  return out;
}

function listApps() {
  if (appsCache && Date.now() - appsCacheAt < 5 * 60 * 1000) return appsCache;
  let apps = null;
  try {
    apps = listAppsPs();
  } catch {
    apps = null;
  }
  if (!apps) apps = listAppsFallback();
  const home = os.homedir();
  for (const p of [path.join(home, 'Desktop'), path.join(home, 'Downloads'), path.join(home, 'Pictures')]) {
    if (fs.existsSync(p)) apps.push({ name: path.basename(p), path: p, type: 'folder', kind: 'quick' });
  }
  appsCache = apps.slice(0, 60);
  appsCacheAt = Date.now();
  return appsCache;
}

function recentFiles() {
  const out = [];
  const dirs = [
    path.join(os.homedir(), 'Documents'),
    path.join(os.homedir(), 'Pictures'),
    path.join(os.homedir(), 'Desktop'),
    path.join(os.homedir(), 'Downloads'),
  ];
  const skipExts = new Set(['.lnk', '.exe', '.dll', '.sys', '.tmp', '.ini', '.reg']);
  for (const d of dirs) {
    if (!fs.existsSync(d)) continue;
    let names;
    try {
      names = fs.readdirSync(d);
    } catch { continue; }
    for (const n of names) {
      const f = path.join(d, n);
      const ext = path.extname(f).toLowerCase();
      if (skipExts.has(ext)) continue;
      let st;
      try {
        st = fs.statSync(f);
      } catch { continue; }
      if (!st.isFile()) continue;
      if (st.size > 100 * 1024 * 1024) continue; // saltar >100 MB
      const isImage = /\.(png|jpe?g|gif|webp|bmp|ico|tiff?)$/i.test(f);
      out.push({
        name: path.basename(f),
        path: f,
        ext,
        type: isImage ? 'image' : 'file',
        _mtime: st.mtimeMs,
      });
    }
  }
  // Ordenar por mtime cacheado: antes el comparador llamaba a statSync
  // (decenas de miles de stats en carpetas grandes → segundos colgado).
  out.sort((a, b) => b._mtime - a._mtime);
  return out.slice(0, 80).map(({ _mtime, ...rest }) => rest);
}

const HOME_DIRS = [
  path.join(os.homedir(), 'Documents'),
  path.join(os.homedir(), 'Pictures'),
  path.join(os.homedir(), 'Desktop'),
  path.join(os.homedir(), 'Downloads'),
];

function fixedDrives() {
  // Unidades listas (C:, D:…): readdir barato por letra.
  const out = [];
  for (let code = 67; code <= 90; code++) {
    const root = String.fromCharCode(code) + ':\\';
    try {
      fs.accessSync(root, fs.constants.R_OK);
      out.push({ name: root, path: root, type: 'folder', kind: 'drive' });
    } catch { /* no existe / sin acceso */ }
  }
  return out;
}

function tryStat(p) {
  try { return fs.statSync(p); } catch { return null; }
}

function homeDirs() {
  return HOME_DIRS
    .filter((d) => fs.existsSync(d))
    .map((d) => ({ name: path.basename(d), path: d, type: 'folder', kind: 'home' }));
}

// Navegación de carpetas: si dir es null devuelve el "inicio" (recientes +
// carpetas rápidas + unidades). Si no, lista el contenido de la carpeta.
function listDir(dir) {
  if (!dir) {
    return {
      path: null,
      name: 'Recientes',
      parent: null,
      homes: homeDirs(),
      drives: fixedDrives(),
      folders: [],
      entries: recentFiles(),
    };
  }
  let resolved;
  try { resolved = path.resolve(dir); } catch { return listDir(null); }
  const stat = tryStat(resolved);
  if (!stat || !stat.isDirectory()) return listDir(null);

  const skipExts = new Set(['.lnk', '.dll', '.sys', '.tmp', '.ini', '.reg']);
  const folders = [];
  const files = [];
  let names;
  try {
    names = fs.readdirSync(resolved, { withFileTypes: true });
  } catch {
    return listDir(null);
  }
  for (const d of names) {
    if (d.name.startsWith('.')) continue;
    // Capar listados gigantescos (System32, etc.): primeras 1500 entradas.
    if (folders.length + files.length >= 1500) break;
    const full = path.join(resolved, d.name);
    const s = tryStat(full);
    if (!s) continue;
    if (d.isDirectory() || s.isDirectory()) {
      folders.push({ name: d.name, path: full, type: 'folder' });
      continue;
    }
    const ext = path.extname(d.name).toLowerCase();
    if (skipExts.has(ext)) continue;
    const isImage = /\.(png|jpe?g|gif|webp|bmp|ico|tiff?)$/i.test(d.name);
    files.push({
      name: d.name,
      path: full,
      ext,
      type: isImage ? 'image' : 'file',
      size: s.size,
      big: s.size > 100 * 1024 * 1024,
    });
  }
  const cmp = (a, b) => a.name.localeCompare(b.name, undefined, { numeric: true, sensitivity: 'base' });
  folders.sort(cmp);
  files.sort(cmp);
  const parent = path.dirname(resolved);
  return {
    path: resolved,
    name: path.basename(resolved) || resolved,
    parent: parent === resolved ? null : parent,
    homes: homeDirs(),
    folders,
    entries: files,
  };
}

function cpuPercent() {
  const cpus = os.cpus();
  const now = { idle: 0, total: 0 };
  for (const c of cpus) {
    for (const type of Object.keys(c.times)) now.total += c.times[type];
    now.idle += c.times.idle;
  }
  let pct = 0;
  if (lastCpuInfo) {
    const dTotal = now.total - lastCpuInfo.total;
    const dIdle = now.idle - lastCpuInfo.idle;
    pct = dTotal > 0 ? Math.max(0, Math.min(100, Math.round(((dTotal - dIdle) / dTotal) * 100))) : 0;
  }
  lastCpuInfo = now;
  return pct;
}

function perfSnapshot() {
  const total = os.totalmem();
  const free = os.freemem();
  const used = total - free;
  return {
    cpu: cpuPercent(),
    mem: Math.round((used / total) * 100),
    memUsedGb: used / 1024 ** 3,
    memTotalGb: total / 1024 ** 3,
  };
}

module.exports = { listApps, recentFiles, listDir, homeDirs, perfSnapshot, cpuPercent };