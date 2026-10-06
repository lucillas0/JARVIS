/* Overlay JARVIS: espacio de trabajo (archivos/imágenes movibles + gestos) + chat.
   Ya NO hay pizarra de dibujo: se sustituye por tarjetas de imagen/documento. */
(() => {
  'use strict';

  const $ = (id) => document.getElementById(id);

  // ============================================================= //
  // Lector de documentos / imágenes (dentro del overlay)
  // ============================================================= //
  const reader = $('reader');
  const readerTitle = $('readerTitle');
  const readerText = $('readerText');
  const readerImg = $('readerImg');
  const readerAskInput = $('readerAskInput');
  let readerZoom = 15;       // tamaño de letra en px para documentos
  let readerImgScale = 1;    // zoom de la imagen
  let _readerPath = null;

  // Resaltado de código de una sola pasada (escáner por línea): nunca anida
  // <span> dentro de <span> (el anterior aplicaba regex en cadena y rompía
  // el HTML: "class" es keyword y matcheaba dentro de class="hl-str").
  const HL_KEYWORDS = new Set(
    ('function const let var return if else for while do switch case break continue ' +
     'class extends new this super import export from default async await try catch ' +
     'throw finally yield typeof instanceof in of void delete true false null undefined ' +
     'None True False def self elif except raise lambda with as pass and or not is print ' +
     'fn pub use mod struct impl trait enum match move mut ref loop type where Self println ' +
     'func go chan select defer make len cap append error bool int string float64 ' +
     'public private protected static final abstract interface package range ' +
     'include require define chmod fork exec spawn pipe').split(' ')
  );

  function escHtml(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function highlightCode(code) {
    const lines = String(code == null ? '' : code).split('\n');
    let inBlock = false;
    const out = [];
    for (let li = 0; li < lines.length; li++) {
      const line = lines[li];
      let h = '';
      let i = 0;
      // Si venimos dentro de un /* ... */ multilínea, seguir en comentario.
      if (inBlock) {
        const end = line.indexOf('*/');
        if (end === -1) {
          h += '<span class="hl-cmt">' + escHtml(line) + '</span>';
          out.push('<span class="hl-ln">' + String(li + 1).padStart(3, ' ') + '</span> ' + h);
          continue;
        }
        h += '<span class="hl-cmt">' + escHtml(line.slice(0, end + 2)) + '</span>';
        i = end + 2;
        inBlock = false;
      }
      while (i < line.length) {
        const ch = line[i];
        // Comentario de línea: //... o #... (fuera de strings: los strings se
        // consumen como token completo al encontrar la comilla, así que aquí
        // nunca estamos dentro de uno).
        if (ch === '/' && line[i + 1] === '/') {
          h += '<span class="hl-cmt">' + escHtml(line.slice(i)) + '</span>';
          break;
        }
        if (ch === '/' && line[i + 1] === '*') {
          const end = line.indexOf('*/', i + 2);
          if (end === -1) {
            h += '<span class="hl-cmt">' + escHtml(line.slice(i)) + '</span>';
            inBlock = true;
            break;
          }
          h += '<span class="hl-cmt">' + escHtml(line.slice(i, end + 2)) + '</span>';
          i = end + 2;
          continue;
        }
        if (ch === '#') {
          h += '<span class="hl-cmt">' + escHtml(line.slice(i)) + '</span>';
          break;
        }
        // Strings con escape.
        if (ch === '"' || ch === "'" || ch === '`') {
          let j = i + 1;
          while (j < line.length) {
            if (line[j] === '\\') { j += 2; continue; }
            if (line[j] === ch) { j++; break; }
            j++;
          }
          h += '<span class="hl-str">' + escHtml(line.slice(i, j)) + '</span>';
          i = j;
          continue;
        }
        // Números.
        if (ch >= '0' && ch <= '9') {
          let j = i + 1;
          while (j < line.length && /[0-9a-zA-Z_.]/.test(line[j])) j++;
          h += '<span class="hl-num">' + escHtml(line.slice(i, j)) + '</span>';
          i = j;
          continue;
        }
        // Identificadores: solo envolver si es keyword.
        if (/[A-Za-z_$]/.test(ch)) {
          let j = i + 1;
          while (j < line.length && /[A-Za-z0-9_$]/.test(line[j])) j++;
          const word = line.slice(i, j);
          h += HL_KEYWORDS.has(word)
            ? '<span class="hl-kw">' + escHtml(word) + '</span>'
            : escHtml(word);
          i = j;
          continue;
        }
        h += escHtml(ch);
        i++;
      }
      const n = String(li + 1).padStart(3, ' ');
      out.push('<span class="hl-ln">' + n + '</span> ' + h);
    }
    return out.join('\n');
  }

  function applyReaderText(text) {
    readerText.style.fontSize = readerZoom + 'px';
    readerText.textContent = text;
  }

  // Código ya resaltado por highlightCode(): es HTML generado localmente
  // (escapado token a token), se inyecta sin sanitizar para no deshacerlo.
  function applyReaderCode(html) {
    readerText.innerHTML = html;
    readerText.style.fontSize = readerZoom + 'px';
  }

  function applyReaderHtml(html) {
    readerText.innerHTML = sanitizeHtml(html);
    readerText.style.fontSize = readerZoom + 'px';
  }

  function requestReaderPreview(path) {
    if (!sendWs({ type: 'file_preview', path })) {
      applyReaderText('Cerebro desconectado, señor: no puedo leer el documento ahora. Use ABRIR para verlo en su aplicación.');
    }
  }

  function openReader(path, name) {
    if (!path) return;
    _readerPath = path;
    readerTitle.textContent = name || path.split(/[\\/]/).pop() || path;
    readerZoom = 15;
    readerImgScale = 1;
    reader.classList.remove('mode-text', 'mode-image', 'mode-html', 'mode-code');
    if (IMG_RE.test(path)) {
      reader.classList.add('mode-image');
      readerImg.style.transform = 'scale(1)';
      readerImg.src = fileUrl(path);
      return (reader.hidden = false);
    }
    reader.classList.add('mode-text');
    readerImg.removeAttribute('src');
    const isCode = CODE_RE.test(extName(path));
    const card = [...document.querySelectorAll('.fcard')].find((c) => c._path === path);
    if (isCode) {
      reader.classList.remove('mode-text');
      reader.classList.add('mode-code');
      if (card && card._docText) {
        applyReaderCode(highlightCode(card._docText));
      } else {
        applyReaderCode(highlightCode('// Leyendo código…'));
        requestReaderPreview(path);
      }
    } else if (card && card._docBox && card._docBox._renderedHtml) {
      applyReaderHtml(card._docBox._renderedHtml);
      reader.classList.remove('mode-text');
      reader.classList.add('mode-html');
    } else if (card && card._docText) {
      applyReaderText(card._docText);
    } else {
      applyReaderText('Leyendo documento…');
      requestReaderPreview(path);
    }
    reader.hidden = false;
  }

  function closeReader() {
    reader.hidden = true;
    _readerPath = null;
  }

  function askJarvisAboutDoc() {
    const q = readerAskInput.value.trim();
    if (!q || !_readerPath) return;
    let docText = readerText.textContent || '';
    if (docText.startsWith('Leyendo documento') || docText.startsWith('Cerebro desconectado')) docText = '';
    const body = docText.trim()
      ? '[DOCUMENTO: ' + (readerTitle.textContent || _readerPath) + ']\n' + docText.slice(0, 15000) + '\n\nPREGUNTA: ' + q
      : q;
    addMsg('user', q);
    readerAskInput.value = '';
    closeReader();
    sendMessage(body, 'text');
  }

  $('readerClose').addEventListener('click', closeReader);
  $('readerZoomIn').addEventListener('click', () => {
    if (reader.classList.contains('mode-image')) {
      readerImgScale = Math.min(6, readerImgScale * 1.2);
      readerImg.style.transform = 'scale(' + readerImgScale.toFixed(3) + ')';
    } else {
      readerZoom = Math.min(40, readerZoom + 2);
      readerText.style.fontSize = readerZoom + 'px';
    }
  });
  $('readerZoomOut').addEventListener('click', () => {
    if (reader.classList.contains('mode-image')) {
      readerImgScale = Math.max(0.25, readerImgScale / 1.2);
      readerImg.style.transform = 'scale(' + readerImgScale.toFixed(3) + ')';
    } else {
      readerZoom = Math.max(10, readerZoom - 2);
      readerText.style.fontSize = readerZoom + 'px';
    }
  });
  $('readerAskBtn').addEventListener('click', askJarvisAboutDoc);
  readerAskInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') askJarvisAboutDoc();
  });

  // ============================================================= //
  // Tarjetas de archivos (imágenes y documentos)
  // ============================================================= //
  const imgLayer = $('imgLayer');
  let cards = [];
  let zTop = 10;

  // El arrastre nativo de las tarjetas (sobre todo de las imágenes, draggable por
  // defecto) hacía que el drop global duplicara la tarjeta y robara los pointer
  // events (imposible mover/escalar). Lo vetamos por completo aquí.
  document.addEventListener('dragstart', (e) => {
    if (e.target && e.target.closest && e.target.closest('.fcard')) e.preventDefault();
  });

  const IMG_RE = /\.(png|jpe?g|gif|webp|bmp|ico|tiff?)$/i;
  const DOC_RE = /\.(pdf|docx?|odt|txt|rtf|pptx|xlsx)$/i;
  const CODE_RE = /\.(py|js|ts|jsx|tsx|html|css|json|xml|yaml|yml|md|sh|bat|ps1|java|c|cpp|h|rs|go|rb|php|sql|r|swift|kt|vue|svelte|toml|ini|env|dockerfile|makefile)$/i;

  /** Whitelist de tags/attrs permitidos para el HTML sanitizado del preview */
  const _SAFE_TAGS = new Set(['p','div','span','img','br','strong','em','b','i','ul','ol','li','table','tr','td','th','thead','tbody','h1','h2','h3','h4','blockquote','pre','code']);
  function sanitizeHtml(html) {
    // Quita script/style y tags no permitidos, deja data: URIs en img src
    let s = html.replace(/<script[\s\S]*?<\/script>/gi, '')
                .replace(/<style[\s\S]*?<\/style>/gi, '');
    // Quitar tags no permitidos (pero preservar contenido de texto)
    s = s.replace(/<(\/?)([a-zA-Z][a-zA-Z0-9]*)(\s[^>]*)?>/g, (full, slash, tag, attrs) => {
      const t = tag.toLowerCase();
      if (!_SAFE_TAGS.has(t)) return '';
      if (slash) return '</' + t + '>';
      let safeAttrs = (attrs || '')
        .replace(/\s+on\w+\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, '')
        .replace(/\s+style\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, '')
        .replace(/\s+srcset\s*=\s*("[^"]*"|'[^']*'|[^\s>]+)/gi, '')
        .replace(/\s+(href|src)\s*=\s*("|')\s*javascript:[^"']*\2/gi, '');
      // Solo esquemas seguros en src (el preview usa file:// y data:image).
      safeAttrs = safeAttrs.replace(/\s+src\s*=\s*("|')([^"']*)\1/gi, (m, q, v) => {
        const val = v.trim();
        if (/^(https?:|file:|blob:|data:image\/)/i.test(val)) return ' src=' + q + val + q;
        return '';
      });
      if (t === 'br' || t === 'img') return '<' + t + safeAttrs + '>';
      return '<' + t + safeAttrs + '>';
    });
    return s;
  }

  function fileUrl(p) {
    // file:/// con cada segmento codificado: encodeURI solo no basta porque
    // deja '#' y '?' como fragmento/query y la imagen/documento no carga.
    const parts = String(p).replace(/\\/g, '/').split('/');
    const enc = parts.map((seg, i) => {
      if (!seg) return '';
      if (/^[A-Za-z]:$/.test(seg)) return seg; // unidad (C:) sin codificar ':'
      return encodeURIComponent(seg);
    });
    let rel = enc.join('/').replace(/^\/+/, '');
    try { return 'file:///' + rel; } catch { return 'file:///' + rel; }
  }

  function fmtBytes(n) {
    if (!n && n !== 0) return '';
    if (n < 1024) return n + ' B';
    if (n < 1024 * 1024) return (n / 1024).toFixed(1) + ' KB';
    if (n < 1024 * 1024 * 1024) return (n / (1024 * 1024)).toFixed(1) + ' MB';
    return (n / (1024 * 1024 * 1024)).toFixed(2) + ' GB';
  }

  function extName(p) {
    return (p.match(/\.([^.]+)$/) || [])[1] ? '.' + p.match(/\.([^.]+)$/)[1].toLowerCase() : '';
  }

  function createCard({ path, name, size }) {
    const card = document.createElement('div');
    card.className = 'fcard';
    const ext = extName(path);

    if (IMG_RE.test(path)) {
      const img = document.createElement('img');
      img.className = 'preview';
      img.draggable = false;
      img.src = fileUrl(path);
      img.addEventListener('load', () => card.classList.add('has-img'));
      img.addEventListener('error', () => {
        const ph = document.createElement('div');
        ph.className = 'placeholder image';
        ph.textContent = '🖼';
        card.insertBefore(ph, img);
        img.remove();
      });
      card.appendChild(img);
      card._tapView = () => openReader(path, name);
    } else if (CODE_RE.test(ext)) {
      const box = document.createElement('div');
      box.className = 'doc-content code-preview';
      box.innerHTML = '<div class="doc-loading">Leyendo código…</div>';
      card.appendChild(box);
      card._docBox = box;
      card._tapView = () => openReader(path, name);
      requestDocPreview(path, card);
    } else if (DOC_RE.test(ext)) {
      const box = document.createElement('div');
      box.className = 'doc-content';
      box.innerHTML = '<div class="doc-loading">Leyendo ' + ext + '…</div>';
      card.appendChild(box);
      card._docBox = box;
      card._tapView = () => openReader(path, name);
      requestDocPreview(path, card);
    } else {
      const ph = document.createElement('div');
      ph.className = 'placeholder document';
      ph.textContent = FILE_ICON(ext);
      card.appendChild(ph);
    }

    const foot = document.createElement('div');
    foot.className = 'card-foot';
    if (!IMG_RE.test(path)) {
      if (CODE_RE.test(ext)) {
        const reviewBtn = document.createElement('button');
        reviewBtn.className = 'open-btn';
        reviewBtn.textContent = 'REVISAR';
        reviewBtn.title = 'Revisar código en el visor';
        reviewBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          openReader(path, name);
        });
        foot.appendChild(reviewBtn);
      }
      const openBtn = document.createElement('button');
      openBtn.className = 'open-btn';
      openBtn.textContent = 'ABRIR';
      openBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        openItem({ path });
      });
      foot.appendChild(openBtn);
    }
    card.appendChild(foot);

    const nm = document.createElement('div');
    nm.className = 'fname';
    nm.textContent = name;
    nm.title = name;
    card.appendChild(nm);
    const sz = document.createElement('div');
    sz.className = 'fsize';
    sz.textContent = fmtBytes(size);
    card.appendChild(sz);

    const xBtn = document.createElement('button');
    xBtn.className = 'img-close';
    xBtn.textContent = '×';
    xBtn.addEventListener('click', (e) => {
      e.stopPropagation();
      card.remove();
      cards = cards.filter((c) => c.el !== card);
    });
    card.appendChild(xBtn);

    makeDraggable(card, imgLayer);
    makeResizable(card);
    card._path = path;
    placeCard(card);
    card.addEventListener('dblclick', () => {
      const ext = extName(path);
      if (IMG_RE.test(path) || DOC_RE.test(ext)) openReader(path, name);
      else openItem({ path });
    });
    return card;
  }

  function FILE_ICON(ext) {
    const map = { '.mp3': '🎵', '.wav': '🎵', '.mp4': '🎬', '.mkv': '🎬', '.zip': '🗜', '.rar': '🗜', '.exe': '⚙', '.html': '🌐', '.css': '🎨', '.js': '⚡', '.ts': '🔷', '.py': '🐍', '.jsx': '⚛', '.tsx': '⚛', '.json': '📋', '.xml': '📋', '.md': '📝', '.yaml': '📋', '.yml': '📋', '.sh': '🖥', '.bat': '🖥', '.ps1': '🖥', '.java': '☕', '.c': '🔧', '.cpp': '🔧', '.h': '🔧', '.rs': '🦀', '.go': '🔷', '.rb': '💎', '.php': '🐘', '.sql': '🗃', '.vue': '💚', '.svelte': '🧡' };
    return map[ext] || '📄';
  }

  function placeCard(card) {
    const wa = window.screen;
    const count = cards.length;
    const px = 300 + (count % 4) * 30;
    const py = 90 + (count % 4) * 35;
    card.style.left = Math.min(px, wa.availWidth - 300) + 'px';
    card.style.top = Math.min(py, wa.availHeight - 200) + 'px';
    card.style.zIndex = String(++zTop);
    imgLayer.appendChild(card);
    cards.push({ el: card });
  }

  // ---- Arrastrar con el ratón/táctil: mover tarjeta a cualquier sitio ----
  function makeDraggable(card, layer) {
    let dragging = false;
    let offX = 0;
    let offY = 0;
    let pressX = 0;
    let pressY = 0;
    let moved = 0;

    card.addEventListener('pointerdown', (e) => {
      if (e.target.closest('button')) return;
      dragging = true;
      moved = 0;
      pressX = e.clientX;
      pressY = e.clientY;
      const pt = e.currentTarget;
      if (pt.setPointerCapture) pt.setPointerCapture(e.pointerId);
      card.classList.add('drag-src');
      card.style.zIndex = String(++zTop);
      const r = card.getBoundingClientRect();
      offX = e.clientX - r.left;
      offY = e.clientY - r.top;
    });
    card.addEventListener('pointermove', (e) => {
      if (!dragging) return;
      moved = Math.max(moved, Math.hypot(e.clientX - pressX, e.clientY - pressY));
      if (moved < 4) return;
      e.preventDefault();
      card.style.left = (e.clientX - offX) + 'px';
      card.style.top = (e.clientY - offY) + 'px';
      fitInScreen(card);
    });
    card.addEventListener('pointerup', (e) => {
      const wasDrag = dragging && moved >= 4;
      dragging = false;
      card.classList.remove('drag-src');
      // Si se soltó sobre un botón (X, ABRIR, REVISAR…), que solo actúe el
      // botón: no abrir el lector.
      const onButton = !!(e.target && e.target.closest && e.target.closest('button'));
      if (!wasDrag && !onButton && card._tapView) card._tapView();
    });
    card.addEventListener('pointercancel', () => {
      dragging = false;
      card.classList.remove('drag-src');
    });
    card.addEventListener('pointerleave', () => {
      if (!dragging) return;
      dragging = false;
      card.classList.remove('drag-src');
    });
  }

  function setScale(card, s) {
    card.style.transform = 'scale(' + s.toFixed(3) + ')';
  }

  function getScale(card) {
    const m = card.style.transform.match(/scale\(([\d.]+)\)/);
    return m ? parseFloat(m[1]) : 1;
  }

  function fitInScreen(card) {
    const r = card.getBoundingClientRect();
    const wa = window.screen;
    if (r.right > wa.availWidth) card.style.left = (parseFloat(card.style.left) - (r.right - wa.availWidth)) + 'px';
    if (r.left < 0) card.style.left = (parseFloat(card.style.left) - r.left) + 'px';
    if (r.bottom > wa.availHeight) card.style.top = (parseFloat(card.style.top) - (r.bottom - wa.availHeight)) + 'px';
    if (r.top < 0) card.style.top = (parseFloat(card.style.top) - r.top) + 'px';
  }

  // ---- Redimensionar el cuerpo de la tarjeta con la manija inferior-derecha ----
  function makeResizable(card) {
    const handle = document.createElement('div');
    handle.className = 'resize-handle';
    handle.title = 'Arrastra para redimensionar';
    card.appendChild(handle);
    let resizing = false;
    let startX = 0, startY = 0, startW = 0, startH = 0;
    handle.addEventListener('pointerdown', (e) => {
      e.preventDefault();
      e.stopPropagation();
      resizing = true;
      startX = e.clientX;
      startY = e.clientY;
      const r = card.getBoundingClientRect();
      startW = r.width;
      startH = r.height;
      if (handle.setPointerCapture) handle.setPointerCapture(e.pointerId);
    });
    handle.addEventListener('pointermove', (e) => {
      if (!resizing) return;
      const s = getScale(card) || 1;
      const visW = Math.max(180, Math.min(window.screen.availWidth - 40, startW + (e.clientX - startX)));
      const visH = Math.max(120, Math.min(window.screen.availHeight - 40, startH + (e.clientY - startY)));
      card.style.width = (visW / s) + 'px';
      card.style.height = (visH / s) + 'px';
      card.classList.add('resized');
      fitInScreen(card);
    });
    handle.addEventListener('pointerup', (e) => { resizing = false; e.stopPropagation(); });
    handle.addEventListener('pointercancel', (e) => { resizing = false; e.stopPropagation(); });
    // Un nodo auxiliar para que .fcard no fuerce width/height con contenido
    card._resizeHandle = handle;
  }

  // ---- Preview de documentos vía brain (WS) ----
  function requestDocPreview(path, card) {
    if (!sendWs({ type: 'file_preview', path })) {
      if (card._docBox) card._docBox.innerHTML = '<div class="doc-error">Cerebro desconectado, señor. Pulse ABRIR para ver el archivo.</div>';
      return;
    }
    card._previewFor = path;
  }

  // ============================================================= //
  // Drop de archivos (cualquier tipo, no solo imágenes)
  // ============================================================= //
  const dropArea = $('dropArea');
  const dropHint = $('dropHint');

  document.addEventListener('dragover', (e) => { e.preventDefault(); dropArea.classList.add('drag-over'); });
  document.addEventListener('dragleave', (e) => {
    if (!e.relatedTarget) dropArea.classList.remove('drag-over');
  });
  document.addEventListener('drop', (e) => {
    e.preventDefault();
    dropArea.classList.remove('drag-over');
    const files = Array.from(e.dataTransfer?.files || []);
    for (const f of files) {
      const p = f.path || f.name;
      createCard({ path: p, name: f.name || p, size: f.size });
    }
    dropHint.style.display = 'none';
  });

  // ============================================================= //
  // Widget rendimiento
  // ============================================================= //
  const perfCpu = $('perfCpu');
  const perfMem = $('perfMem');
  const perfCpuTxt = $('perfCpuTxt');
  const perfMemTxt = $('perfMemTxt');

  if (window.overlayAPI?.onPerf) {
    window.overlayAPI.onPerf((d) => {
      perfCpu.style.width = d.cpu + '%';
      perfCpu.classList.toggle('high', d.cpu > 80);
      perfCpuTxt.textContent = d.cpu + '%';
      perfMem.style.width = d.mem + '%';
      perfMem.classList.toggle('high', d.mem > 80);
      perfMemTxt.textContent = d.mem + '% (' + d.memUsedGb.toFixed(1) + '/' + d.memTotalGb.toFixed(1) + ' GB)';
    });
  }

  // ============================================================= //
  // Widget apps / archivos
  // ============================================================= //
  const sideList = $('sideList');
  const tabApps = $('tabApps');
  const tabFiles = $('tabFiles');
  const fileNav = $('fileNav');
  const navUp = $('navUp');
  const navPath = $('navPath');
  let currentDir = null;
  let listingParent = null;

  tabApps.addEventListener('click', () => {
    tabApps.classList.add('active');
    tabFiles.classList.remove('active');
    fileNav.hidden = true;
    loadApps();
  });
  tabFiles.addEventListener('click', () => {
    tabFiles.classList.add('active');
    tabApps.classList.remove('active');
    fileNav.hidden = false;
    loadFiles();
  });
  navUp.addEventListener('click', () => {
    currentDir = listingParent || null;
    loadFiles();
  });
  // Doble clic en la ruta: ir a cualquier carpeta escribiéndola.
  navPath.title = 'Doble clic para ir a una ruta';
  navPath.style.cursor = 'text';
  navPath.addEventListener('dblclick', () => {
    const dest = prompt('Carpeta:', currentDir || '');
    if (dest && dest.trim()) {
      currentDir = dest.trim();
      loadFiles();
    }
  });

  function itemIcon(item) {
    if (item.type === 'image') return '🖼';
    if (item.type === 'folder') return '📁';
    const e = (item.ext || '').toLowerCase();
    return e === '.pdf' ? '📄' : (e === '.docx' || e === '.doc' ? '📝' : (e === '.mp3' || e === '.wav' ? '🎵' : '📄'));
  }

  async function loadApps() {
    sideList.innerHTML = '';
    let apps = [];
    try { apps = await window.overlayAPI.getApps(); } catch { /* */ }
    for (const a of apps) {
      const item = document.createElement('div');
      item.className = 'side-item';
      item.innerHTML = '<span class="ico">⬚</span><span class="nm"></span>';
      item.querySelector('.nm').textContent = a.name;
      item.title = a.name;
      item.addEventListener('click', () => openItem(a));
      sideList.appendChild(item);
    }
  }

  function addSideSep(label) {
    const s = document.createElement('div');
    s.className = 'side-sep';
    s.textContent = label;
    sideList.appendChild(s);
  }

  function addSideItem(f, onPick) {
    const item = document.createElement('div');
    item.className = 'side-item' + (f.type === 'folder' ? ' folder' : '');
    item.innerHTML = '<span class="ico">' + (f.type === 'folder' ? '📁' : itemIcon(f)) + '</span><span class="nm"></span>';
    item.querySelector('.nm').textContent = f.name;
    item.title = f.path || f.name;
    item.addEventListener('click', onPick);
    sideList.appendChild(item);
  }

  async function loadFiles() {
    sideList.innerHTML = '';
    navPath.textContent = '…';
    let listing = null;
    try {
      listing = await window.overlayAPI.listDir(currentDir);
    } catch { /* */ }
    if (!listing) {
      navPath.textContent = 'No se pudo leer la carpeta.';
      return;
    }
    listingParent = listing.parent || null;
    navPath.textContent = currentDir ? listing.name : 'Recientes · carpetas';
    navPath.title = currentDir || 'Inicio';
    navUp.disabled = !currentDir;

    const entries = listing.entries || [];
    const folders = listing.folders || [];
    const homes = listing.homes || [];
    const drives = listing.drives || [];

    let sepAdded = false;
    if (currentDir) {
      for (const f of folders) {
        addSideItem(f, () => {
          currentDir = f.path;
          loadFiles();
        });
      }
      if (folders.length && entries.length) addSideSep('ARCHIVOS');
      sepAdded = true;
    } else {
      for (const f of homes) {
        addSideItem(f, () => {
          currentDir = f.path;
          loadFiles();
        });
      }
      if (drives.length) {
        addSideSep('UNIDADES');
        sepAdded = true;
        for (const f of drives) {
          addSideItem(f, () => {
            currentDir = f.path;
            loadFiles();
          });
        }
      }
      if (homes.length && entries.length) {
        addSideSep('RECIENTES');
        sepAdded = true;
      }
    }
    for (const f of entries) {
      // Todo archivo va a la pizarra como tarjeta (la tarjeta decide:
      // imagen, preview, código o icono + ABRIR).
      addSideItem(f, () => {
        createCard({ path: f.path, name: f.name, size: f.size });
        dropHint.style.display = 'none';
      });
    }
    if (!sepAdded && homes.length && folders.length) addSideSep('CARPETAS');
  }
  loadApps();

  // Abrir app/archivo: cierra el overlay para que se vea delante
  function openItem(item) {
    if (!window.overlayAPI || typeof window.overlayAPI.open !== 'function') {
      try { addMsg('sys', 'Fuera de Electron no puedo abrir archivos, señor.'); } catch { /* noop */ }
      return;
    }
    try { window.overlayAPI.open(item); }
    catch (err) { try { addMsg('sys', 'No pude abrirlo, señor.'); } catch { /* noop */ } }
  }

  // ============================================================= //
  // Chat con JARVIS (voz + texto)
  // ============================================================= //
  const chatLog = $('chatLog');
  const chatInput = $('chatInput');
  const sendBtn = $('sendBtn');
  const micBtn = $('micBtn');

  let ws = null;
  let reconnectTimer = 0;
  let listening = false;

  // ---- Modo cámara (pantalla completa) ----
  const cameraMode = $('cameraMode');
  const cameraModeVideo = $('cameraModeVideo');
  const cameraModeTitle = $('cameraModeTitle');
  const cameraModeStatus = $('cameraModeStatus');
  const cameraCloseBtn = $('cameraCloseBtn');
  const cameraAnalyzeBtn = $('cameraAnalyzeBtn');
  let cameraStream = null;
  let cameraStartPromise = null;

  // ---- Tarjeta de autocorrección ----
  const healCard = $('healCard');
  const healDot = $('healDot');
  const healText = $('healText');
  const healBtns = $('healBtns');
  const healAllowBtn = $('healAllowBtn');
  const healDenyBtn = $('healDenyBtn');
  let healPendingId = null;
  const HEAL_STAGE_TXT = {
    found: '🔍 Encontrado, voy a arreglarlo',
    fixing: '🔧 Arreglando…',
    fixed: '✅ Solucionado',
    needs_user: '🙋 Necesito tu ayuda',
    escalated: '⏳ Sigo intentándolo…',
  };
  function healUpdate(d) {
    if (!healCard) return;
    healCard.hidden = false;
    healCard.style.opacity = '';
    if (healBtns) healBtns.hidden = true;
    healPendingId = null;
    healCard.classList.toggle('ok', d.stage === 'fixed');
    if (healDot) healDot.textContent = '●';
    const head = HEAL_STAGE_TXT[d.stage] || d.stage;
    const err = (d.error || '').slice(0, 140);
    const note = (d.note || '').slice(0, 200);
    if (healText) healText.textContent = head + '\n' + err + (note ? '\n' + note : '');
    if (d.stage === 'fixed') {
      setTimeout(() => { if (healCard) healCard.hidden = true; }, 30000);
    }
  }
  function healAsk(d) {
    if (!healCard) return;
    healCard.hidden = false;
    healCard.style.opacity = '';
    healCard.classList.remove('ok');
    healPendingId = d.id || null;
    if (healText) {
      healText.textContent = '¿Permite el arreglo, señor?\n' +
        (d.error || '').slice(0, 140) + '\nPlan: ' + (d.plan || 'repararlo');
    }
    if (healBtns) healBtns.hidden = false;
  }
  function healAnswer(approved) {
    if (healBtns) healBtns.hidden = true;
    sendWs({ type: 'heal_approval', id: healPendingId || '', approved: !!approved });
    healPendingId = null;
    // Fundido rápido al responder; si hay progreso lo vuelve a mostrar.
    if (healCard) {
      healCard.style.transition = 'opacity 0.35s ease';
      healCard.style.opacity = '0';
      setTimeout(() => {
        if (healCard) { healCard.hidden = true; healCard.style.opacity = ''; }
      }, 380);
    }
  }
  if (healAllowBtn) healAllowBtn.addEventListener('click', () => healAnswer(true));
  if (healDenyBtn) healDenyBtn.addEventListener('click', () => healAnswer(false));

  function setCameraBusy(on) {
    cameraModeStatus.hidden = !on;
    if (on) cameraModeStatus.textContent = 'Analizando…';
  }

  function setCameraStatus(text) {
    cameraModeStatus.textContent = text;
    cameraModeStatus.hidden = !text;
  }

  function cameraCaptureFrame() {
    if (!cameraModeVideo.videoWidth) return null;
    const c = document.createElement('canvas');
    c.width = cameraModeVideo.videoWidth;
    c.height = cameraModeVideo.videoHeight;
    const ctx = c.getContext('2d');
    ctx.drawImage(cameraModeVideo, 0, 0);
    return c.toDataURL('image/jpeg', 0.7).split(',')[1];
  }

  async function cameraStart() {
    if (cameraStartPromise) return cameraStartPromise;
    if (cameraStream && cameraStream.getVideoTracks().some((t) => t.readyState === 'live')) {
      cameraMode.hidden = false;
      document.body.classList.add('camera-active');
      return true;
    }
    cameraStartPromise = (async () => {
      try {
        // Evita que la cámara de análisis compita con el tracker de gestos
        // por el mismo dispositivo en Windows.
        if (tracker) stopGestures();
        if (window.JarvisGestures && window.JarvisGestures.acquireCameraStream) {
          cameraStream = await window.JarvisGestures.acquireCameraStream();
        } else {
          cameraStream = await navigator.mediaDevices.getUserMedia({
            video: { width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30, max: 30 } },
            audio: false,
          });
        }
        cameraModeVideo.srcObject = cameraStream;
        await cameraModeVideo.play();
        if (cameraModeVideo.readyState < 2) {
          await new Promise((resolve, reject) => {
            const cleanup = () => {
              clearTimeout(timer);
              cameraModeVideo.removeEventListener('loadeddata', onData);
              cameraModeVideo.removeEventListener('error', onError);
            };
            const onData = () => { cleanup(); resolve(); };
            const onError = () => { cleanup(); reject(new Error('No se pudo iniciar el vídeo.')); };
            const timer = setTimeout(() => { cleanup(); reject(new Error('La cámara no entrega fotogramas.')); }, 8000);
            cameraModeVideo.addEventListener('loadeddata', onData, { once: true });
            cameraModeVideo.addEventListener('error', onError, { once: true });
            if (cameraModeVideo.readyState >= 2) onData();
          });
        }
        cameraMode.hidden = false;
        document.body.classList.add('camera-active');
        return true;
      } catch (err) {
        const detail = err && err.message ? err.message : err;
        if (cameraStream) cameraStream.getTracks().forEach((t) => t.stop());
        cameraStream = null;
        cameraModeVideo.srcObject = null;
        cameraMode.hidden = true;
        document.body.classList.remove('camera-active');
        addMsg('sys', 'No pude abrir la cámara: ' + detail);
        sendWs({ type: 'error_report', source: 'camera',
                 error: String('getUserMedia falló: ' + detail).slice(0, 300),
                 context: 'overlay cameraStart (permiso ausente, dispositivo ocupado o sin fotogramas)' });
        return false;
      } finally {
        cameraStartPromise = null;
      }
    })();
    return cameraStartPromise;
  }

  function cameraStop() {
    cameraStartPromise = null;
    if (cameraStream) {
      cameraStream.getTracks().forEach((t) => t.stop());
      cameraStream = null;
    }
    cameraModeVideo.srcObject = null;
    cameraMode.hidden = true;
    cameraModeStatus.hidden = true;
    document.body.classList.remove('camera-active');
  }

  async function cameraAnalyze(prompt) {
    // Si la cámara no está abierta, abrirla primero y esperar al vídeo.
    if (!cameraStream || cameraMode.hidden || !cameraModeVideo.videoWidth) {
      setCameraStatus('Abriendo cámara…');
      const ok = await cameraStart();
      if (!ok) return;
    }
    for (let i = 0; i < 20 && !cameraModeVideo.videoWidth; i++) {
      await new Promise((r) => setTimeout(r, 250));
    }
    const b64 = cameraCaptureFrame();
    if (!b64) {
      setCameraStatus('Cámara sin imagen. ¿Está conectada?');
      sendWs({ type: 'error_report', source: 'camera',
               error: 'cameraCaptureFrame vacío (sin vídeo de webcam)',
               context: 'overlay cameraAnalyze tras auto-arranque' });
      return;
    }
    setCameraBusy(true);
    sendWs({ type: 'camera_frame', image: b64, message: prompt || 'Describe qué ves en esta cámara web.' });
  }

  cameraCloseBtn.addEventListener('click', cameraStop);
  cameraAnalyzeBtn.addEventListener('click', () => cameraAnalyze('Describe qué hay delante de la cámara web.'));

  function addMsg(role, text) {
    const m = document.createElement('div');
    m.className = 'msg ' + role;
    m.textContent = text;
    chatLog.appendChild(m);
    chatLog.scrollTop = chatLog.scrollHeight;
  }

  // Trampa global: cualquier error JS se vuelve visible en el chat
  // y se reporta al cerebro para auto-reparación.
  window.addEventListener('error', (e) => {
    try {
      const msg = 'Error interno: ' + (e && e.message ? e.message : e);
      addMsg('sys', msg);
      sendWs({ type: 'error_report', source: 'overlay',
               error: String((e && e.message) || e).slice(0, 300),
               context: 'win+j overlay' });
    } catch { /* noop */ }
  });
  window.addEventListener('unhandledrejection', (e) => {
    try {
      const msg = 'Fallo asíncrono: ' + ((e && e.reason && (e.reason.message || e.reason)) || e);
      addMsg('sys', String(msg).slice(0, 200));
      sendWs({ type: 'error_report', source: 'overlay',
               error: String(msg).slice(0, 300),
               context: 'win+j overlay (promesa)' });
    } catch { /* noop */ }
  });

  function setWsDot(on) {
    try {
      const dot = $('wsDot');
      if (dot) {
        dot.classList.toggle('on', !!on);
        dot.title = on ? 'Conectado al cerebro' : 'Sin conexión';
      }
    } catch { /* noop */ }
  }

  function connect() {
    clearTimeout(reconnectTimer);
    try {
      ws = new WebSocket('ws://127.0.0.1:8000/ws');
    } catch {
      reconnectTimer = setTimeout(connect, 3000);
      return;
    }
    ws.onmessage = (e) => {
      let d;
      try { d = JSON.parse(e.data); } catch { return; }
      if (d.type === 'assistant_reply' && d.text) {
        if (!cameraMode.hidden) setCameraStatus(d.text);
        addMsg('jarvis', d.text);
      } else if (d.type === 'selfheal_update') {
        healUpdate(d);
      } else if (d.type === 'heal_approval_request') {
        healAsk(d);
      } else if (d.type === 'camera_mode') {
        if (d.state === 'on') cameraStart();
        else cameraStop();
      } else if (d.type === 'camera_analyze_request') {
        cameraAnalyze(d.message || 'Describe qué ves en esta cámara web.');
      } else if (d.type === 'file_preview_result') {
        handlePreviewResult(d);
      } else if (d.type === 'voice_result') {
        if (d.text) {
          addMsg('user', '(voz) ' + d.text);
          sendMessage(d.text, 'voice');
        } else if (d.error === 'no_heard') {
          addMsg('sys', 'No le he oído, señor. Pulse 🎙️ o escriba.');
        }
      } else if (d.type === 'conversation_state' && d.state === 'listening') {
        addMsg('sys', 'Escuchando…');
      } else if (d.type === 'conversation_state' && d.state === 'thinking') {
        addMsg('sys', 'Pensando…');
      } else if (d.type === 'hello') {
        if (!ws._greeted) { addMsg('sys', d.message); ws._greeted = true; }
      }
    };
    ws.onclose = () => {
      setWsDot(false);
      reconnectTimer = setTimeout(connect, 3000);
    };
    ws.onopen = () => {
      setWsDot(true);
    };
  }
  connect();

  function sendWs(msg) {
    if (ws && ws.readyState === WebSocket.OPEN) {
      ws.send(JSON.stringify(msg));
      return true;
    }
    return false;
  }

  function handlePreviewResult(d) {
    const htmlContent = d.ok ? (d.html || null) : null;
    const textDisplay = d.ok
      ? d.text + (d.truncated ? '\n\n… (texto truncado, se muestra el principio)' : '')
      : (d.reason || 'No pude leer el documento.');
    const isCode = CODE_RE.test(extName(d.path));
    if (_readerPath === d.path && !reader.hidden) {
      if (isCode) {
        applyReaderCode(highlightCode(textDisplay));
        reader.classList.remove('mode-text', 'mode-image', 'mode-html');
        reader.classList.add('mode-code');
      } else if (htmlContent) {
        applyReaderHtml(htmlContent);
        reader.classList.remove('mode-text', 'mode-image', 'mode-code');
        reader.classList.add('mode-html');
      } else {
        applyReaderText(textDisplay);
        reader.classList.remove('mode-image', 'mode-html', 'mode-code');
        reader.classList.add('mode-text');
      }
    }
    for (const c of [...document.querySelectorAll('.fcard')]) {
      if (c._previewFor !== d.path) continue;
      c._docText = textDisplay;
      const box = c._docBox;
      if (!box) continue;
      if (htmlContent) {
        box.innerHTML = sanitizeHtml(htmlContent);
        box._renderedHtml = htmlContent; // para el lector sin re-solicitar
      } else if (box.classList.contains('code-preview')) {
        box.innerHTML = highlightCode(textDisplay);
        box._renderedHtml = null;
      } else {
        box.innerHTML = '';
        box.append(document.createTextNode(textDisplay));
        box._renderedHtml = null;
      }
      c.classList.toggle('has-text', !!d.ok);
      c.classList.toggle('no-text', !d.ok);
    }
  }

  function sendMessage(text, mode) {
    if (!sendWs({ type: 'user_message', message: text, input_mode: mode || 'text', speak: true })) {
      addMsg('sys', 'Cerebro desconectado. Reintentando…');
      connect();
    }
  }

  $('sendBtn').addEventListener('click', () => {
    const text = chatInput.value.trim();
    if (!text) return;
    addMsg('user', text);
    chatInput.value = '';
    sendMessage(text, 'text');
  });

  chatInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') sendBtn.click();
  });

  micBtn.addEventListener('click', () => {
    if (listening) {
      listening = false;
      micBtn.classList.remove('listening');
      micBtn.title = 'Hablar con JARVIS';
      return;
    }
    if (!ws || ws.readyState !== WebSocket.OPEN) { addMsg('sys', 'Cerebro desconectado, señor.'); return; }
    listening = true;
    micBtn.classList.add('listening');
    micBtn.title = 'Detener escucha';
    ws.send(JSON.stringify({ type: 'voice_activate' }));
    setTimeout(() => {
      if (listening) {
        listening = false;
        micBtn.classList.remove('listening');
        micBtn.title = 'Hablar con JARVIS';
      }
    }, 12000);
  });

  // (Botón de micro del orbe eliminado: se habla por el mic del chat.)

  // ============================================================= //
  // Gestos de mano: agarrar/mover/escalar tarjetas
  // ============================================================= //
  const camVideo = $('camVideo');
  const camOverlay = $('camOverlay');
  const gesturesBtn = $('gesturesBtn');
  const handCursor = $('handCursor');
  let tracker = null;
  let startingGestures = false;
  let grabbedCard = null;
  let gestureScale = 1;

  const GRAB_RADIUS = 220; // px alrededor del cursor para "agarrar" una tarjeta

  let handStagePos = { x: 0, y: 0 };
  let handPointerActive = false;

  function showHandPointer() {
    handPointerActive = true;
    handCursor.hidden = false;
    moveHandPointer();
  }

  function hideHandPointer() {
    handPointerActive = false;
    handCursor.hidden = true;
  }

  function moveHandPointer() {
    if (!handPointerActive) return;
    handCursor.style.left = (handStagePos.x * window.innerWidth) + 'px';
    handCursor.style.top = (handStagePos.y * window.innerHeight) + 'px';
  }

  function handPickCard(pos) {
    // Tarjeta más cercana al punto de la mano dentro del radio
    let best = null;
    let bestDist = GRAB_RADIUS;
    for (const c of cards) {
      if (!c.el.isConnected) continue;
      const r = c.el.getBoundingClientRect();
      const cx = r.left + r.width / 2;
      const cy = r.top + r.height / 2;
      const px = pos.x * window.innerWidth;
      const py = pos.y * window.innerHeight;
      const d = Math.hypot(cx - px, cy - py);
      if (d < bestDist) {
        bestDist = d;
        best = c.el;
      }
    }
    return best;
  }

  function releaseHand() {
    grabbedCard = null;
  }

  function stopGestures() {
    if (tracker) { try { tracker.stop(); } catch { /* */ } }
    tracker = null;
    grabbedCard = null;
    gesturesBtn.textContent = '👐';
    gesturesBtn.setAttribute('aria-pressed', 'false');
    document.body.classList.remove('gestures-on');
    hideHandPointer();
    gesturesBtn.title = 'Activar gestos de mano';
  }

  async function toggleGestures() {
    if (startingGestures) return;
    const on = gesturesBtn.getAttribute('aria-pressed') === 'true';
    if (on) {
      stopGestures();
      return;
    }
    try {
      startingGestures = true;
      gesturesBtn.disabled = true;
      gesturesBtn.textContent = '⏳';
      gesturesBtn.setAttribute('aria-pressed', 'true');
      document.body.classList.add('gestures-on');
      // HandTracker abre primero la webcam; el fallo de la CDN no debe impedir
      // que la cámara funcione en modo análisis ni ocultar el error real.
      const Tracker = window.JarvisGestures.HandTracker;
      tracker = new Tracker(camVideo, camOverlay, {
        onRotate: () => {},
        onZoom: (f) => {
          // f viene del tracker como prevDist/dist (convención de cámara:
          // f<1 = acercar). Para tamaños (fuente/escala) hay que invertir:
          // abrir las manos (f<1) debe AGRANDAR, no encoger.
          const inv = (f && isFinite(f) && f > 0) ? (1 / f) : 1;
          if (!reader.hidden) {
            if (reader.classList.contains('mode-image')) {
              readerImgScale = Math.max(0.25, Math.min(6, readerImgScale * inv));
              readerImg.style.transform = 'scale(' + readerImgScale.toFixed(3) + ')';
            } else {
              readerZoom = Math.max(10, Math.min(40, readerZoom * inv));
              readerText.style.fontSize = readerZoom + 'px';
            }
            return;
          }
          if (grabbedCard) {
            setScale(grabbedCard, getScale(grabbedCard) * inv);
          } else if (cards.length) {
            const c = cards[cards.length - 1].el;
            if (c && c.isConnected) setScale(c, getScale(c) * inv);
          }
        },
        onPointer: (x, y, mode) => {
          handStagePos = { x, y };
          moveHandPointer();
          if (mode === 'spin' && grabbedCard) {
            const r = grabbedCard.getBoundingClientRect();
            grabbedCard.style.left = (x * window.innerWidth - r.width / 2) + 'px';
            grabbedCard.style.top = (y * window.innerHeight - r.height / 2) + 'px';
            fitInScreen(grabbedCard);
          }
        },
        onStatus: (s) => {
          handCursor.classList.toggle('pinching', s.mode !== 'idle');
          if (s.mode === 'spin' && !grabbedCard) {
            grabbedCard = handPickCard(handStagePos);
            if (grabbedCard) grabbedCard.style.zIndex = String(++zTop);
          }
          if (s.mode !== 'spin') releaseHand();
        },
      });
      handStagePos = { x: 0.5, y: 0.35 };
      showHandPointer();
      await tracker.start();
      gesturesBtn.textContent = '👐';
      gesturesBtn.title = 'Gestos activos (toca para desactivar)';
    } catch (err) {
      console.error('Gestos:', err);
      const cameraReady = !!(tracker && camVideo.videoWidth > 0 && tracker.stream &&
        tracker.stream.getVideoTracks().some((track) => track.readyState === 'live'));
      const detail = String((err && err.message ? err.message : err)).slice(0, 300);
      if (cameraReady) {
        // Mantener la webcam visible si el dispositivo abrió y lo que falló
        // fue la descarga/modelo de MediaPipe.
        gesturesBtn.textContent = '👐';
        gesturesBtn.setAttribute('aria-pressed', 'true');
        gesturesBtn.title = 'Cámara activa; gestos no disponibles: ' + detail;
        document.body.classList.add('gestures-on');
        showHandPointer();
        handCursor.title = 'Cámara activa; falló el seguimiento de manos';
      } else {
        gesturesBtn.textContent = '👐';
        gesturesBtn.setAttribute('aria-pressed', 'false');
        document.body.classList.remove('gestures-on');
        hideHandPointer();
        stopGestures();
      }
      addMsg('sys', (cameraReady ? 'La cámara sí abrió, pero falló el seguimiento de manos: '
        : 'No he podido activar la cámara, señor: ') + detail);
      sendWs({ type: 'error_report', source: 'camera-tracking',
               error: detail,
               context: 'overlay toggleGestures (MediaPipe/getUserMedia)' });
    } finally {
      startingGestures = false;
      gesturesBtn.disabled = false;
    }
  }

  gesturesBtn.addEventListener('click', toggleGestures);

  // ============================================================= //
  // Cerrar / abrir ventana principal / colapsar chat
  // ============================================================= //
  $('closeBtn').addEventListener('click', () => {
    if (window.overlayAPI && typeof window.overlayAPI.close === 'function') window.overlayAPI.close();
  });
  $('mainBtn').addEventListener('click', () => {
    if (window.overlayAPI && typeof window.overlayAPI.main === 'function') window.overlayAPI.main();
  });

  const chatPanel = $('chatPanel');
  const chatCollapseBtn = $('chatCollapseBtn');
  chatCollapseBtn.addEventListener('click', () => {
    const collapsed = chatPanel.classList.toggle('collapsed');
    chatCollapseBtn.textContent = collapsed ? '✛' : '—';
    chatCollapseBtn.title = collapsed ? 'Expandir chat' : 'Colapsar chat (solo micrófono)';
    if (!collapsed) chatInput.focus();
  });

  // (Panel siempre visible: sin atenuado por inactividad.)

  window.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') {
      if (!reader.hidden) {
        closeReader();
      } else if (chatPanel.classList.contains('collapsed')) {
        chatCollapseBtn.click();
      } else if (window.overlayAPI && typeof window.overlayAPI.close === 'function') {
        window.overlayAPI.close();
      }
    }
  });
})();
/* JARVIS theme hook: aplica /api/config (colores, mensajes, orbe, animaciones). */
(function () {
  async function jarvisTheme() {
    try {
      const j = await (await fetch('http://127.0.0.1:8000/api/config')).json();
      const c = j.config; if (!c) return;
      const T = c.theme || {}, M = c.messages || {}, O = c.orb || {}, A = c.anims || {};
      let st = document.getElementById('jarvis-theme');
      if (!st) { st = document.createElement('style'); st.id = 'jarvis-theme'; document.head.appendChild(st); }
      const inten = 1 + ((+A.intensity || 70) / 200);
      st.textContent =
        ':root{--jv-accent:' + T.accent + ';--jv-bg:' + T.bg + ';--jv-ink:' + T.ink + ';}' +
        'body{background:' + T.bg + '!important;color:' + T.ink + '!important;}' +
        '@keyframes jarvisFadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}' +
        '@keyframes jarvisSlideIn{from{opacity:0;transform:translateX(-16px)}to{opacity:1;transform:none}}' +
        '@keyframes jarvisPulse{0%,100%{filter:brightness(1)}50%{filter:brightness(' + inten + ')}}' +
        '@keyframes jarvisSpin{to{transform:rotate(360deg)}}' +
        '.msg.user{background:' + M.user_bg + '!important;border-radius:' + M.radius + 'px!important;font-size:' + M.font + 'px!important;}' +
        '.msg.jarvis,.msg.assistant{background:' + M.jarvis_bg + '!important;color:' + T.ink + '!important;border-radius:' + M.radius + 'px!important;font-size:' + M.font + 'px!important;}' +
        '.msg.sys{color:' + M.sys_color + '!important;}';
      const stage = document.getElementById('orbStage');
      if (stage) stage.style.display = O.enabled ? '' : 'none';
      const sp = +(O.speed || 1);
      const anim = (!A.enabled || !O.enabled) ? 'none'
        : (A.orb_anim === 'spin' ? 'jarvisSpin ' + (4 / sp) + 's linear infinite'
        : (A.orb_anim === 'glow' ? 'jarvisPulse 2.2s ease-in-out infinite'
        : 'jarvisPulse ' + (2 / sp) + 's ease-in-out infinite'));
      document.querySelectorAll('#orbStage,#orbStage canvas').forEach(function (o) {
        o.style.display = O.enabled ? '' : 'none';
        o.style.animation = anim;
      });
      const msgAnim = !A.enabled || A.msg_anim === 'none' ? '' :
        (A.msg_anim === 'slide' ? 'jarvisSlideIn .45s ease both' : 'jarvisFadeIn .45s ease both');
      document.querySelectorAll('.msg').forEach(function (m) {
        if (msgAnim && !m.dataset.jv) { m.dataset.jv = '1'; m.style.animation = msgAnim; }
      });
    } catch (e) { /* sin cerebro: tema por defecto */ }
  }
  jarvisTheme();
  setInterval(jarvisTheme, 30000);
  // La voz del overlay (voice_hook) consulta este flag.
  try {
    fetch('http://127.0.0.1:8000/api/config').then(r => r.json()).then(j => {
      const v = (((j || {}).config || {}).voice) || {};
      window.__jvSpeak = v.read_aloud !== false;
    }).catch(() => {});
  } catch (e) {}
})();
/* JARVIS voice hook: lee en alto respuestas nuevas + parallax del orbe. */
(function () {
  window.__jvSpeak = true;
  // Parallax sutil del orbe con el ratón
  let px = 0, py = 0, tx = 0, ty = 0;
  document.addEventListener('mousemove', function (e) {
    tx = (e.clientX / window.innerWidth - 0.5) * 14;
    ty = (e.clientY / window.innerHeight - 0.5) * 14;
  });
  setInterval(function () {
    px += (tx - px) * 0.08;
    py += (ty - py) * 0.08;
    const st = document.getElementById('orbStage');
    if (st && !st.dataset.jvlock) {
      st.style.translate = px.toFixed(1) + 'px ' + py.toFixed(1) + 'px';
    }
  }, 50);
  function speak(t) {
    try {
      if (!window.__jvSpeak || !window.speechSynthesis) return;
      speechSynthesis.cancel();
      const u = new SpeechSynthesisUtterance((t || '').slice(0, 600));
      u.lang = 'es-ES'; u.rate = 1.05;
      speechSynthesis.speak(u);
    } catch (e) { /* sin voz: silencio elegante */ }
  }
  const seen = (typeof WeakSet !== 'undefined') ? new WeakSet() : null;
  const seenArr = [];
  function fresh(el) {
    if (seen) { if (seen.has(el)) return false; seen.add(el); return true; }
    if (seenArr.indexOf(el) >= 0) return false;
    seenArr.push(el);
    return true;
  }
  new MutationObserver(function (muts) {
    muts.forEach(function (m) {
      m.addedNodes.forEach(function (n) {
        if (!n || n.nodeType !== 1) return;
        let els = [];
        try {
          if (n.matches && n.matches('.msg.jarvis')) els = [n];
          else if (n.querySelectorAll) els = Array.from(n.querySelectorAll('.msg.jarvis'));
        } catch (e) { return; }
        els.forEach(function (el) {
          if (!fresh(el)) return;
          try { el.style.animation = 'jarvisFadeIn .45s ease both'; } catch (e) {}
          speak(el.textContent || '');
        });
      });
    });
  }).observe(document.body, { childList: true, subtree: true });
})();
