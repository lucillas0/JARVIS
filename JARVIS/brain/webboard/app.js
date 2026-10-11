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
  // App abierta DENTRO de la pizarra (solo webapps: son contenido web)
  // ============================================================= //
  const workspace = $('workspace');
  const wsHost = $('wsHost');
  const wsTitle = $('wsTitle');
  const wsKind = $('wsKind');
  const wsDetach = $('wsDetach');
  const wsClose = $('wsClose');
  let wsView = null;   // <webview> vivo
  let wsUrl = '';
  const WS_UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
    + '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36';

  function clearWorkspace() {
    if (wsView) {
      try { wsView.stop(); } catch (e) { /* noop */ }
      try { wsView.remove(); } catch (e) { /* noop */ }
      wsView = null;
    }
    wsUrl = '';
    workspace.hidden = true;
  }

  function openInside(title, url) {
    clearWorkspace();
    wsTitle.textContent = title;
    wsKind.textContent = 'WEB';
    workspace.hidden = false;

    const wait = document.createElement('div');
    wait.className = 'ws-wait';
    wait.textContent = 'Cargando ' + title + '…';
    wsHost.appendChild(wait);

    const wv = document.createElement('webview');
    wv.setAttribute('src', url);
    wv.setAttribute('allowpopups', 'false');
    wv.setAttribute('partition', 'persist:jarvis');
    // Sin esto, webs como Notion o Gmail rechazan el navegador incrustado
    // ("your browser is not compatible") por la identificación de Electron.
    wv.setAttribute('useragent', WS_UA);
    wv.addEventListener('dom-ready', () => {
      const w = wsHost.querySelector('.ws-wait');
      if (w) w.remove();
    });
    wv.addEventListener('did-fail-load', () => {
      const w = wsHost.querySelector('.ws-wait');
      if (w) w.textContent = 'No he podido cargar esa web, señor.';
    });
    // Los enlaces que abren pestaña nueva van al navegador de verdad.
    wv.addEventListener('new-window', (e) => {
      const u = (e.url || (e.detail && e.detail.url) || '').toString();
      if (/^https?:\/\//i.test(u) && window.overlayAPI.external) window.overlayAPI.external(u);
    });
    wsHost.appendChild(wv);
    wsView = wv;
    wsUrl = url;
  }

  // Pulsar una app: si es webapp se abre dentro; si es nativa, se abre normal.
  async function launchApp(app) {
    let info = null;
    try { info = await window.overlayAPI.webapp(app.name); } catch (e) { info = null; }
    if (info && info.url) openInside(info.label || app.name, info.url);
    else openItem(app);
  }

  // Atajo del tile: abrir en el navegador de verdad, sin pasar por la pizarra.
  function openOutside(app) {
    let p = Promise.resolve(null);
    try { p = window.overlayAPI.webapp(app.name); } catch (e) { /* noop */ }
    p.then((info) => {
      if (info && info.url && window.overlayAPI.openWeb) window.overlayAPI.openWeb(info.url);
      else openItem(app);
    });
  }

  wsDetach.addEventListener('click', () => {
    if (wsUrl && window.overlayAPI.openWeb) window.overlayAPI.openWeb(wsUrl);
    clearWorkspace();
  });
  wsClose.addEventListener('click', () => clearWorkspace());
  if (window.overlayAPI && window.overlayAPI.onWorkspace) {
    window.overlayAPI.onWorkspace((s) => {
      if (s && s.kind === 'close') clearWorkspace();
    });
  }

  // ============================================================= //
  // Arranque de la pizarra: los paneles entran escalonados y el orbe
  // se dibuja al final. Se anima con la Web Animations API en vez de con
  // clases CSS porque la ventana del overlay se reutiliza: reponer una
  // clase no siempre reinicia su animación, y esto sí la reinicia siempre.
  // ============================================================= //
  // Secuencia de arranque en tres actos, en este orden exacto:
  //  1. Los laterales desde su lado: apps (izquierda), chat + monitor (derecha).
  //  2. La barra de arriba (EN LÍNEA) cayendo desde arriba.
  //  3. JARVIS: el orbe aparece y se dibuja capa a capa, igual que en la
  //     ventana principal (es el mismo orbe, mismo render).
  const BOOT_EASE = 'cubic-bezier(0.16, 0.84, 0.28, 1)';
  const BOOT_STEPS = [
    // Sin blur en los paneles grandes: animar blur + backdrop-blur juntos
    // tira fotogramas y el panel salta de vacío a completo de golpe. Con solo
    // opacidad y desplazamiento entra suave en cualquier GPU.
    { sel: '.side-widget', delay: 40, dur: 620,
      from: { opacity: 0, transform: 'translateX(-34px) scale(0.985)' },
      to: { opacity: 1, transform: 'none' } },
    { sel: '#closeBtn', delay: 90, dur: 440,
      from: { opacity: 0, transform: 'scale(0.55)' },
      to: { opacity: 1, transform: 'none' } },
    { sel: '#mainBtn', delay: 150, dur: 440,
      from: { opacity: 0, transform: 'scale(0.55)' },
      to: { opacity: 1, transform: 'none' } },
    { sel: '#gesturesBtn', delay: 210, dur: 440,
      from: { opacity: 0, transform: 'scale(0.55)' },
      to: { opacity: 1, transform: 'none' } },
    { sel: '.right-column', delay: 150, dur: 620,
      from: { opacity: 0, transform: 'translateX(34px) scale(0.985)' },
      to: { opacity: 1, transform: 'none' } },
    { sel: '.think-tree', delay: 560, dur: 560,
      from: { opacity: 0, transform: 'translateY(-14px)', filter: 'blur(2px)' },
      to: { opacity: 1, transform: 'none', filter: 'none' } },
    { sel: '.workspace', delay: 420, dur: 620,
      from: { opacity: 0, transform: 'translateY(14px) scale(0.99)' },
      to: { opacity: 1, transform: 'none' } },
    { sel: '.drop-hint', delay: 700, dur: 800,
      from: { opacity: 0 }, to: { opacity: 1 } },
    { sel: '.orb-stage', delay: 950, dur: 900,
      from: { opacity: 0, transform: 'scale(0.55)', filter: 'blur(6px)' },
      to: { opacity: 1, transform: 'none', filter: 'none' } },
    { sel: '#imgLayer', delay: 650, dur: 600,
      from: { opacity: 0 }, to: { opacity: 1 } },
  ];

  // El orbe puede aún no existir (los módulos cargan en paralelo): se espera
  // unos cuantos frames antes de resignarse. Velocidad pausada y elegante:
  // la construcción completa dura unos 4 segundos.
  function playOrbIntro() {
    if (window.__jarvisOrb) { window.__jarvisOrb.playIntro({ speed: 0.7 }); return; }
    let tries = 0;
    const wait = () => {
      if (window.__jarvisOrb) window.__jarvisOrb.playIntro({ speed: 0.7 });
      else if (++tries < 90) requestAnimationFrame(wait);
    };
    requestAnimationFrame(wait);
  }

  // Los ajustes de /config mandan sobre el ajuste de Windows: el señor ha
  // pedido estas animaciones expresamente, así que si las tiene activas se
  // reproducen enteras. Solo se saltan si las apagó en JARVIS (anims.enabled).
  let animCache = null;
  function animPrefs() {
    if (animCache) return { enabled: animCache.enabled };
    animCache = { enabled: true };
    fetch('http://127.0.0.1:8000/api/config')
      .then((r) => r.json())
      .then((j) => {
        const a = (j.config && j.config.anims) || {};
        if (a && a.enabled === false) animCache.enabled = false;
      })
      .catch(() => { /* sin config: se anima igualmente */ });
    return { enabled: animCache.enabled };
  }

  let lastBootAt = 0;
  let bootToken = 0;
  function bootSequence() {
    lastBootAt = Date.now();
    runBootSequence(++bootToken);
  }

  function runBootSequence(my) {
    const p = animPrefs();
    if (!p.enabled) {
      document.body.classList.remove('preboot');
      if (window.__jarvisOrb) window.__jarvisOrb.skipIntro();
      return;
    }
    armBootSafety(my);
    // Blindaje: se oculta todo por estilo directo ANTES de quitar el vacío.
    // Aunque preboot hubiera fallado, el primer fotograma tras esta tarea ya
    // sale oculto, y las animaciones WAAPI toman el relevo desde ahí.
    const hiddenEls = [];
    for (const step of BOOT_STEPS) {
      for (const el of document.querySelectorAll(step.sel)) {
        el.style.opacity = '0';
        hiddenEls.push(el);
      }
    }
    // Las tarjetas se ocultan AQUÍ MISMO, en la misma tarea que el panel. Antes
    // solo se ocultaba el panel: al desvanecerse dejaba ver la rejilla COMPLETA
    // de golpe (las tarjetas ya venían pintadas desde la carga) y la cascada
    // empezaba 200 ms después. Ese era el frame que el señor veía.
    for (const el of document.querySelectorAll('#sideList .app-tile, #sideList .file-tile')) {
      el.style.opacity = '0';
      hiddenEls.push(el);
    }
    // El orbe se apaga AQUÍ MISMO, en la misma tarea y antes de quitar el
    // vacío: como la ventana se reutiliza, el orbe podía estar ya construido
    // de la vez anterior y se veía completo unos milisegundos hasta que la
    // reconstrucción lo volvía a esconder. Ese era el instante que se veía.
    if (window.__jarvisOrb) window.__jarvisOrb.parkIntro();
    else {
      let tries = 0;
      const wait = () => {
        if (my !== bootToken) return;
        if (window.__jarvisOrb) window.__jarvisOrb.parkIntro();
        else if (++tries < 30) requestAnimationFrame(wait);
      };
      requestAnimationFrame(wait);
    }
    for (const step of BOOT_STEPS) {
      for (const el of document.querySelectorAll(step.sel)) {
        if (!el.animate) { try { el.style.opacity = ''; } catch (e) { /* noop */ } continue; }
        const a = el.animate([step.from, step.to], {
          duration: step.dur, delay: step.delay, easing: BOOT_EASE, fill: 'both',
        });
        // Al terminar, primero se limpia el estilo directo y LUEGO se suelta
        // la animación: al revés habría un parpadeo a invisible.
        const done = () => {
          try { el.style.opacity = ''; } catch (e) { /* noop */ }
          try { a.cancel(); } catch (e) { /* noop */ }
        };
        trackBootAnim(a, done);
      }
    }
    const frame = $('bootFrame');
    if (frame && frame.animate) {
      trackBootAnim(frame.animate(
        [{ boxShadow: 'inset 0 0 90px 10px rgba(127,184,216,0.32)' },
         { boxShadow: 'inset 0 0 0 0 rgba(127,184,216,0)' }],
        { duration: 850, easing: 'ease-out', fill: 'both' },
      ));
    }
    // Quitar el estado vacío y arrancar las animaciones en la MISMA tarea: si
    // se hiciera en pasos separados, el navegador podría pintar un fotograma
    // con la pizarra ya montada antes de empezar a animarse. Los estilos
    // directos se limpian al terminar cada animación para no pelearse después.
    document.body.classList.remove('preboot');
    const clearHidden = () => { for (const el of hiddenEls) { try { el.style.opacity = ''; } catch (e) { /* noop */ } } };
    setTimeout(() => { if (my === bootToken) clearHidden(); }, 1900);
    // Cada arranque invalida los temporizadores del anterior: si se abre y se
    // cierra rápido, un setTimeout viejo podía disparar su animación encima de
    // la pizarra ya montada y se veía repetirse. El orbe es el tercer acto,
    // así que se construye cuando su marco ya ha entrado. La cascada arranca
    // MIENTRAS el panel aún está entrando para que las tarjetas nunca se vean
    // completas y quietas antes de animarse.
    setTimeout(() => { if (my === bootToken) playOrbIntro(); }, 980);
    // La cascada arranca en la MISMA tarea que el panel, no 200 ms después: así
    // cada tarjeta entra mientras el panel se abre y nunca se ve la rejilla
    // entera antes de tiempo. `fill:'both'` las mantiene invisibles durante su
    // retardo, de modo que el primer fotograma solo enseña el panel y la
    // primera tarjeta.
    staggerSideTiles();
  }

  // Las tarjetas de apps/archivos entran en cascada junto con el panel: es lo
  // que más se nota y más sensación premium da. Sin tope de tarjetas, pero con
  // el retardo acotado para que la cola no eternice la entrada.
  function staggerSideTiles() {
    const tiles = document.querySelectorAll('#sideList .app-tile, #sideList .file-tile');
    let i = 0;
    for (const el of tiles) {
      if (!el.animate) continue;
      const a = el.animate(
        [{ opacity: 0, transform: 'translateY(12px) scale(0.96)' },
         { opacity: 1, transform: 'none' }],
        // La primera tarjeta entra junto al panel (40 ms) y el resto se reparten
        // la entrada: nunca puede verse la rejilla entera de una vez.
        { duration: 320, delay: 40 + Math.min(i * 26, 520), easing: BOOT_EASE, fill: 'both' },
      );
      trackBootAnim(a);
      i++;
    }
  }

  // Todas las animaciones del arranque se guardan para poder cancelarlas al
  // ocultar: si no, al reabrir seguirían a medias y se mezclarían con el nuevo
  // arranque, y se vería la pizarra a medio montar.
  const bootAnims = new Set();
  function trackBootAnim(a, done) {
    bootAnims.add(a);
    const drop = () => { try { if (done) done(); } catch (e) { /* noop */ } bootAnims.delete(a); };
    if (a.finished) a.finished.then(drop).catch(drop);
    else drop();
  }
  function cancelBootAnims() {
    for (const a of bootAnims) {
      try { a.cancel(); } catch (e) { /* noop */ }
    }
    bootAnims.clear();
  }

  // La ventana del overlay se reutiliza: al ocultarse vuelve a quedar vacía,
  // para que la próxima apertura no enseñe nada hasta que empiece la entrada.
  // Además se hace por visibilitychange y no solo por IPC: así da igual por
  // qué camino se oculte la ventana, la página se aparca a sí misma sin
  // depender de que llegue el mensaje.
  function parkOverlay() {
    bootToken++;
    cancelBootAnims();
    document.body.classList.add('preboot');
    if (window.__jarvisOrb) window.__jarvisOrb.parkIntro();
  }
  if (window.overlayAPI && window.overlayAPI.onHide) {
    window.overlayAPI.onHide(() => {
      parkOverlay();
      // Se avisa DESPUÉS de pintar el vacío (dos fotogramas): así Windows
      // guarda como última imagen la pizarra vacía y no la montada. Si al
      // reabrir enseña un fotograma viejo, enseña el vacío.
      requestAnimationFrame(() => requestAnimationFrame(() => {
        try { if (window.overlayAPI.hiddenDone) window.overlayAPI.hiddenDone(); } catch (e) { /* noop */ }
      }));
    });
  }
  // Al mostrar la ventana, el compositor de Windows puede enseñar todavía la
  // última imagen que tenía en memoria. Aquí, ya con la ventana visible, se le
  // pide un fotograma real y solo cuando el navegador confirma que lo ha
  // pintado se permite que empiece el arranque. Así lo primero que se ve en
  // pantalla es la pizarra vacía de `preboot`, nunca la montada.
  if (window.overlayAPI && window.overlayAPI.onPaintProbe) {
    window.overlayAPI.onPaintProbe(() => {
      requestAnimationFrame(() => requestAnimationFrame(() => {
        try { if (window.overlayAPI.painted) window.overlayAPI.painted(); } catch (e) { /* noop */ }
      }));
    });
  }

  // Antes de mostrarse, el proceso principal pide dejar la pizarra vacía y
  // espera confirmación (tras pintar). Así es imposible enseñarla montada,
  // pase lo que pase con el estado anterior.
  if (window.overlayAPI && window.overlayAPI.onPrepare) {
    window.overlayAPI.onPrepare(() => {
      parkOverlay();
      // OJO: aquí la ventana sigue OCULTA y con la ventana oculta el navegador
      // congela requestAnimationFrame, así que nunca llegaría la confirmación
      // (la esperando el proceso principal antes de mostrar). Por eso el vacío
      // se confirma con macrotareas: `preboot` oculta por CSS y se aplica al
      // primer reflow, sin depender de que se pinte un fotograma.
      setTimeout(() => setTimeout(() => {
        try { if (window.overlayAPI.prepared) window.overlayAPI.prepared(); } catch (e) { /* noop */ }
      }));
    });
  }
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') parkOverlay();
  });
  // La red de seguridad se arma SOLO cuando llega el arranque. Antes vivía
  // al cargar la página y quitaba el estado vacío a los 6 s, muchísimo antes
  // de que el señor pulsara Win+J: la pizarra se quedaba ya montada y luego
  // la animación se repetía encima. Ese era el parpadeo que se veía.
  function armBootSafety(my) {
    setTimeout(() => {
      if (my !== bootToken) return;
      if (document.body.classList.contains('preboot')) {
        document.body.classList.remove('preboot');
        if (window.__jarvisOrb && !window.__jarvisOrb.isIntroStarted()) {
          window.__jarvisOrb.skipIntro();
        }
      }
    }, 3000);
  }

  // El overlay se reutiliza entre Win+J y la ventana se muestra DESPUÉS de
  // que la página cargue. Por eso el arranque no se dispara al cargar, sino
  // cuando el proceso principal dice "ya puedes mostrarte" (overlay:boot):
  // así la animación se ve entera desde el primer fotograma visible.
  if (window.overlayAPI && window.overlayAPI.onBoot) {
    window.overlayAPI.onBoot(() => bootSequence());
  }
  // Si la orden de arranque se perdiera (IPC roto), la pizarra quedaría vacía
  // para siempre. Este vigilante solo actúa si la ventana está VISIBLE y sigue
  // vacía varios segundos después: mientras está oculta no toca nada.
  setInterval(() => {
    if (document.visibilityState !== 'visible') return;
    if (!document.body.classList.contains('preboot')) return;
    if (Date.now() - lastBootAt < 5000) return;
    bootSequence();
  }, 2000);

  // ============================================================= //
  // Widget apps / archivos
  // ============================================================= //
  const sideList = $('sideList');
  const tabApps = $('tabApps');
  const tabFiles = $('tabFiles');
  const fileNav = $('fileNav');
  const navUp = $('navUp');
  const navPath = $('navPath');
  const appSearch = $('appSearch');
  let currentDir = null;
  let listingParent = null;
  let allApps = [];
  let filesModel = [];
  let sideMode = 'apps';

  tabApps.addEventListener('click', () => {
    tabApps.classList.add('active');
    tabFiles.classList.remove('active');
    fileNav.hidden = true;
    sideMode = 'apps';
    if (appSearch) {
      appSearch.hidden = false;
      appSearch.placeholder = allApps.length
        ? 'Buscar entre ' + allApps.length + ' aplicaciones'
        : 'Buscar aplicaciones';
    }
    loadApps();
  });
  tabFiles.addEventListener('click', () => {
    tabFiles.classList.add('active');
    tabApps.classList.remove('active');
    fileNav.hidden = false;
    sideMode = 'files';
    if (appSearch) {
      appSearch.hidden = false;
      appSearch.placeholder = 'Buscar en esta carpeta';
      appSearch.value = '';
    }
    loadFiles();
  });
  if (appSearch) {
    appSearch.addEventListener('input', () => {
      if (sideMode === 'files') renderFiles();
      else renderApps();
    });
    appSearch.addEventListener('keydown', (e) => {
      if (e.key === 'Escape') { appSearch.value = ''; renderApps(); renderFiles(); appSearch.blur(); }
    });
  }
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

  // Iconos de Windows: extraídos del ejecutable real (base64 inline).
  // Si una app no trae icono (UWP, sin recurso), se dibuja un monograma
  // con la inicial y un color estable derivado del nombre.
  const MONO_HUES = [197, 210, 174, 158, 266, 22, 340, 40];

  function hueForExt(ext) {
    const e = (ext || '').toLowerCase();
    if (/\.(png|jpe?g|gif|webp|bmp|ico|tiff?|svg|heic|avif)$/.test(e)) return 197;
    if (/\.pdf$/.test(e)) return 8;
    if (/\.(docx?|odt|rtf|txt|md|pages)$/.test(e)) return 212;
    if (/\.(xlsx?|csv|ods|numbers)$/.test(e)) return 152;
    if (/\.(pptx?|key|odp)$/.test(e)) return 32;
    if (/\.(mp3|wav|flac|ogg|m4a|aac)$/.test(e)) return 280;
    if (/\.(mp4|mkv|avi|mov|webm|wmv)$/.test(e)) return 330;
    if (/\.(zip|rar|7z|tar|gz|bz2|xz)$/.test(e)) return 38;
    if (/\.(py|js|ts|tsx|jsx|json|html|css|xml|yml|yaml|sh|ps1|sql|rs|go|java|c|cpp|h)$/.test(e)) return 166;
    if (/\.(exe|msi|bat|cmd|lnk)$/.test(e)) return 205;
    return 205;
  }

  function svgEl(name, attrs) {
    const el = document.createElementNS('http://www.w3.org/2000/svg', name);
    for (const k in attrs) el.setAttribute(k, attrs[k]);
    return el;
  }

  // Documento con pliegue y renglones: el color lo marca el tipo de archivo.
  function fileGlyph(ext) {
    const h = hueForExt(ext);
    const svg = svgEl('svg', { viewBox: '0 0 32 32', 'class': 'app-file', 'aria-hidden': 'true' });
    svg.appendChild(svgEl('path', {
      d: 'M7.5 3.5h11L24 9v19a1.5 1.5 0 0 1-1.5 1.5h-15A1.5 1.5 0 0 1 6 28V5a1.5 1.5 0 0 1 1.5-1.5z',
      fill: 'hsla(' + h + ',44%,56%,.16)',
      stroke: 'hsla(' + h + ',58%,70%,.8)',
      'stroke-width': '1.3',
      'stroke-linejoin': 'round',
    }));
    svg.appendChild(svgEl('path', {
      d: 'M18.5 3.5V9H24',
      fill: 'hsla(' + h + ',58%,70%,.28)',
      stroke: 'hsla(' + h + ',58%,70%,.8)',
      'stroke-width': '1.3',
      'stroke-linejoin': 'round',
    }));
    for (let i = 0; i < 3; i++) {
      svg.appendChild(svgEl('line', {
        x1: '10', y1: String(14 + i * 4.2), x2: i === 2 ? '18' : '22', y2: String(14 + i * 4.2),
        stroke: 'hsla(' + h + ',50%,74%,.6)', 'stroke-width': '1.4', 'stroke-linecap': 'round',
      }));
    }
    return svg;
  }

  // Unidad: disco con led. La letra va en el nombre de la tarjeta, no aquí.
  function driveGlyph() {
    const svg = svgEl('svg', { viewBox: '0 0 32 32', 'class': 'app-file', 'aria-hidden': 'true' });
    svg.appendChild(svgEl('rect', {
      x: '3', y: '9', width: '26', height: '13', rx: '2.5',
      fill: 'rgba(127,184,216,.18)', stroke: 'rgba(150,203,228,.8)', 'stroke-width': '1.3',
    }));
    svg.appendChild(svgEl('circle', { cx: '8.5', cy: '15.5', r: '1.7', fill: 'rgba(190,230,248,.95)' }));
    svg.appendChild(svgEl('line', {
      x1: '14', y1: '15.5', x2: '25', y2: '15.5',
      stroke: 'rgba(150,203,228,.55)', 'stroke-width': '1.6', 'stroke-linecap': 'round',
    }));
    svg.appendChild(svgEl('path', {
      d: 'M6 26.5h20', stroke: 'rgba(150,203,228,.4)',
      'stroke-width': '1.3', 'stroke-linecap': 'round',
    }));
    return svg;
  }

  // Miniatura real si el navegador puede leer el archivo; si no, glifo.
  function fileVisual(f) {
    const wrap = document.createElement('span');
    wrap.className = 'app-ico';
    // Las unidades llegan como type:'folder' pero con kind:'drive': mirar antes.
    if (f.kind === 'drive' || /^[A-Z]:\\?$/.test(f.name || '')) {
      wrap.appendChild(driveGlyph());
      return wrap;
    }
    if (f.type === 'folder') {
      wrap.appendChild(folderGlyph());
      return wrap;
    }
    if (f.type === 'image' && f.path) {
      const img = document.createElement('img');
      img.className = 'thumb';
      img.alt = '';
      img.draggable = false;
      img.src = 'file:///' + encodeURI(String(f.path).replace(/\\/g, '/'));
      img.addEventListener('error', () => {
        img.remove();
        wrap.appendChild(fileGlyph(f.ext));
      }, { once: true });
      wrap.appendChild(img);
      return wrap;
    }
    wrap.appendChild(fileGlyph(f.ext));
    return wrap;
  }

  function monoFor(name) {
    const clean = (name || '?').replace(/[^\p{L}\p{N}]/gu, '');
    const letter = (clean.charAt(0) || '?').toUpperCase();
    let h = 0;
    for (let i = 0; i < clean.length; i++) h = (h * 31 + clean.charCodeAt(i)) >>> 0;
    const hue = MONO_HUES[h % MONO_HUES.length];
    const el = document.createElement('span');
    el.className = 'app-mono';
    el.textContent = letter;
    el.style.setProperty('--mono-h', hue);
    return el;
  }

  function folderGlyph() {
    const NS = 'http://www.w3.org/2000/svg';
    const svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 32 32');
    svg.setAttribute('class', 'app-folder');
    svg.setAttribute('aria-hidden', 'true');
    const back = document.createElementNS(NS, 'path');
    back.setAttribute('d', 'M3 8.5A2.5 2.5 0 0 1 5.5 6h6.2l2.4 2.8h12.4A2.5 2.5 0 0 1 29 11.3v2H3z');
    back.setAttribute('fill', 'rgba(127,184,216,.42)');
    const front = document.createElementNS(NS, 'path');
    front.setAttribute('d', 'M3 12h26v13.5A2.5 2.5 0 0 1 26.5 28h-21A2.5 2.5 0 0 1 3 25.5z');
    front.setAttribute('fill', 'url(#jarvisFolder)');
    const defs = document.createElementNS(NS, 'defs');
    const grad = document.createElementNS(NS, 'linearGradient');
    grad.setAttribute('id', 'jarvisFolder');
    grad.setAttribute('x1', '0');
    grad.setAttribute('y1', '0');
    grad.setAttribute('x2', '0');
    grad.setAttribute('y2', '1');
    const s1 = document.createElementNS(NS, 'stop');
    s1.setAttribute('offset', '0');
    s1.setAttribute('stop-color', 'rgba(150,203,228,.95)');
    const s2 = document.createElementNS(NS, 'stop');
    s2.setAttribute('offset', '1');
    s2.setAttribute('stop-color', 'rgba(72,124,154,.95)');
    grad.appendChild(s1);
    grad.appendChild(s2);
    defs.appendChild(grad);
    svg.appendChild(defs);
    svg.appendChild(back);
    svg.appendChild(front);
    return svg;
  }

  function appTile(a) {
    const tile = document.createElement('button');
    tile.className = 'app-tile';
    tile.type = 'button';
    tile.title = a.name;
    tile.setAttribute('aria-label', a.name);

    const ic = document.createElement('span');
    ic.className = 'app-ico';
    if (a.type === 'folder') {
      ic.appendChild(folderGlyph());
    } else if (a.icon) {      const img = document.createElement('img');
      img.src = a.icon;
      img.alt = '';
      img.draggable = false;
      img.addEventListener('error', () => {
        img.remove();
        ic.appendChild(monoFor(a.name));
      }, { once: true });
      ic.appendChild(img);
    } else {
      ic.appendChild(monoFor(a.name));
    }

    const nm = document.createElement('span');
    nm.className = 'app-name';
    nm.textContent = a.name;

    tile.appendChild(ic);
    tile.appendChild(nm);

    // Botón secundario: abrir en su propia ventana sin usar la pizarra.
    const ext = document.createElement('span');
    ext.className = 'tile-ext';
    ext.textContent = '⛶';
    ext.title = 'Abrir en su propia ventana';
    ext.setAttribute('aria-label', 'Abrir ' + a.name + ' en su propia ventana');
    ext.addEventListener('click', (ev) => {
      ev.stopPropagation();
      openOutside(a);
    });
    tile.appendChild(ext);

    tile.addEventListener('click', () => launchApp(a));
    return tile;
  }

  function renderApps() {
    sideList.innerHTML = '';
    sideList.className = 'side-list apps-grid';
    const q = (appSearch ? appSearch.value : '').trim().toLowerCase();
    const hits = q
      ? allApps.filter((a) => a.name.toLowerCase().includes(q))
      : allApps;

    if (!hits.length) {
      const empty = document.createElement('div');
      empty.className = 'app-empty';
      empty.textContent = q
        ? 'Sin coincidencias, señor.'
        : 'No he encontrado aplicaciones, señor.';
      sideList.appendChild(empty);
      return;
    }

    const frag = document.createDocumentFragment();
    for (const a of hits) frag.appendChild(appTile(a));
    sideList.appendChild(frag);
    // Si las tarjetas llegan tarde (la lista tarda en cargar), la cascada del
    // arranque ya pasó y aparecerían de golpe. Por eso se repite aquí siempre
    // que estemos dentro de un arranque: así nunca se ven completas y quietas.
    if (Date.now() - lastBootAt < 2500) staggerSideTiles();
  }

  async function loadApps() {
    if (!allApps.length) {
      let apps = [];
      try { apps = await window.overlayAPI.getApps(); } catch { /* */ }
      allApps = apps || [];
      if (appSearch) {
        appSearch.placeholder = allApps.length
          ? 'Buscar entre ' + allApps.length + ' aplicaciones'
          : 'Buscar';
      }
    }
    renderApps();
  }

  function filesSep(label) {
    const s = document.createElement('div');
    s.className = 'files-sep';
    s.textContent = label;
    sideList.appendChild(s);
  }

  function fileTile(f, onPick) {
    const tile = document.createElement('button');
    tile.type = 'button';
    tile.className = 'app-tile file-tile'
      + (f.type === 'folder' ? ' is-folder' : '')
      + (f.kind === 'drive' ? ' is-drive' : '');
    tile.title = f.path || f.name;
    tile.setAttribute('aria-label', f.name);
    tile.appendChild(fileVisual(f));
    const nm = document.createElement('span');
    nm.className = 'app-name';
    nm.textContent = f.name;
    tile.appendChild(nm);
    tile.addEventListener('click', onPick);
    return tile;
  }

  // El buscador también filtra la carpeta visible (sin recorrer el disco).
  function renderFiles() {
    sideList.innerHTML = '';
    sideList.className = 'side-list files-grid';
    const q = (appSearch ? appSearch.value : '').trim().toLowerCase();
    const model = filesModel || [];
    const items = model.filter((b) => b.kind === 'item');
    const hits = q
      ? items.filter((b) => (b.f.name || '').toLowerCase().includes(q))
      : items;

    if (q) {
      if (!hits.length) {
        const empty = document.createElement('div');
        empty.className = 'app-empty';
        empty.textContent = 'Ningún archivo coincide, señor.';
        sideList.appendChild(empty);
        return;
      }
      for (const b of hits) sideList.appendChild(fileTile(b.f, b.onPick));
      return;
    }

    if (!model.length) {
      const empty = document.createElement('div');
      empty.className = 'app-empty';
      empty.textContent = 'Esta carpeta está vacía, señor.';
      sideList.appendChild(empty);
      return;
    }
    for (const b of model) {
      if (b.kind === 'sep') filesSep(b.label);
      else sideList.appendChild(fileTile(b.f, b.onPick));
    }
    if (Date.now() - lastBootAt < 2500) staggerSideTiles();
  }

  async function loadFiles() {
    filesModel = [];
    renderFiles();
    navPath.textContent = '…';
    let listing = null;
    try {
      listing = await window.overlayAPI.listDir(currentDir);
    } catch { /* */ }
    if (!listing) {
      filesModel = [];
      navPath.textContent = 'No se pudo leer la carpeta.';
      renderFiles();
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
    const model = [];
    const go = (f) => () => { currentDir = f.path; loadFiles(); };
    const pick = (f) => () => {
      createCard({ path: f.path, name: f.name, size: f.size });
      dropHint.style.display = 'none';
    };

    if (currentDir) {
      for (const f of folders) model.push({ kind: 'item', f, onPick: go(f) });
      if (folders.length && entries.length) model.push({ kind: 'sep', label: 'ARCHIVOS' });
      for (const f of entries) model.push({ kind: 'item', f, onPick: pick(f) });
    } else {
      for (const f of homes) model.push({ kind: 'item', f, onPick: go(f) });
      if (drives.length) {
        model.push({ kind: 'sep', label: 'UNIDADES' });
        for (const f of drives) model.push({ kind: 'item', f, onPick: go(f) });
      }
      if (homes.length && entries.length) model.push({ kind: 'sep', label: 'RECIENTES' });
      for (const f of entries) model.push({ kind: 'item', f, onPick: pick(f) });
    }
    filesModel = model;
    renderFiles();
  }
  loadApps();

  // Abrir app/archivo: cierra el overlay para que se vea delante
  async function openItem(item) {
    if (!window.overlayAPI || typeof window.overlayAPI.open !== 'function') {
      try { addMsg('sys', 'Aquí no puedo abrir programas, señor.'); } catch { /* noop */ }
      return;
    }
    try {
      // Antes no se miraba la respuesta: si el cerebro decía que no, se
      // quedaba tan.callado y parecía que el botón no funcionaba.
      const r = await window.overlayAPI.open(item);
      if (r && r.ok === false) {
        addMsg('sys', 'No he podido abrir «' + ((item && item.name) || 'eso') + '», señor.');
      }
    } catch (err) {
      try { addMsg('sys', 'No pude abrirlo, señor.'); } catch { /* noop */ }
    }
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

  // ---- Gestor de programas instalados y terminal ----
  // La rejilla de APPS muestra una muestra de 60. Esto es TODO lo que hay en
  // el equipo, con buscador, y también la terminal. En modo LITE es la única
  // forma de abrir Docker Desktop, un navegador o una consola, porque no hay
  // ventana de escritorio.
  const appsMgr = $('appsMgr');
  const appsMgrList = $('appsMgrList');
  const appsMgrSearch = $('appsMgrSearch');
  const appsMgrCount = $('appsMgrCount');
  const appsMgrBtn = $('appsMgrBtn');
  const termBtn = $('termBtn');
  let mgrCargado = false;
  let mgrTimer = null;

  function pintarMgr(apps) {
    if (!appsMgrList) return;
    appsMgrList.textContent = '';
    if (!apps || !apps.length) {
      const v = document.createElement('div');
      v.className = 'mgr-vacio';
      v.textContent = 'Ningun programa con ese nombre.';
      appsMgrList.appendChild(v);
      if (appsMgrCount) appsMgrCount.textContent = '0';
      return;
    }
    if (appsMgrCount) appsMgrCount.textContent = apps.length + ' programas';
    for (const a of apps) {
      const b = document.createElement('button');
      b.className = 'mgr-item';
      b.title = a.path || a.name;
      const ic = document.createElement('span');
      ic.className = 'mgr-ico';
      ic.textContent = '▪';
      const tx = document.createElement('span');
      tx.textContent = a.name;
      b.appendChild(ic);
      b.appendChild(tx);
      b.addEventListener('click', async () => {
        if (!window.overlayAPI || !window.overlayAPI.open) return;
        await window.overlayAPI.open(a);
      });
      appsMgrList.appendChild(b);
    }
  }

  async function cargarMgr(q) {
    if (!window.overlayAPI || !window.overlayAPI.listInstalled) {
      pintarMgr([]);
      return;
    }
    try {
      const apps = await window.overlayAPI.listInstalled(q || '');
      pintarMgr(apps || []);
    } catch (e) {
      pintarMgr([]);
    }
  }

  function abrirMgr() {
    if (!appsMgr) return;
    appsMgr.hidden = false;
    if (!mgrCargado) { mgrCargado = true; cargarMgr(''); }
    else cargarMgr(appsMgrSearch ? appsMgrSearch.value : '');
    if (appsMgrSearch) setTimeout(() => appsMgrSearch.focus(), 40);
  }

  function cerrarMgr() {
    if (appsMgr) appsMgr.hidden = true;
  }

  if (appsMgrBtn) appsMgrBtn.addEventListener('click', () => {
    if (appsMgr && !appsMgr.hidden) cerrarMgr(); else abrirMgr();
  });
  const mgrX = $('appsMgrClose');
  if (mgrX) mgrX.addEventListener('click', cerrarMgr);
  if (appsMgrSearch) {
    appsMgrSearch.addEventListener('input', () => {
      if (mgrTimer) clearTimeout(mgrTimer);
      mgrTimer = setTimeout(() => cargarMgr(appsMgrSearch.value), 180);
    });
  }
  // El gestor de programas y la terminal son solo para MODO LITE (servidor).
  // En un escritorio no hacen falta: ahí ya se abre cualquier cosa como se
  // quiera, y meter un panel de 949 programas en la pizarra solo la ensucia.
  async function aplicarModo() {
    let m = 'full';
    try {
      if (window.overlayAPI && window.overlayAPI.modo) m = (await window.overlayAPI.modo()) || 'full';
    } catch (e) { /* si no se sabe, se asume escritorio */ }
    const lite = m === 'lite';
    if (appsMgrBtn) appsMgrBtn.hidden = !lite;
    if (termBtn) termBtn.hidden = !lite;
    if (appsMgr) appsMgr.dataset.modo = m;
    const nota = $('appsMgrNota');
    if (nota) {
      nota.hidden = !lite;
      if (lite) {
        nota.textContent = 'Se abren en el servidor donde corre JARVIS, '
          + 'no en este navegador.';
      }
    }
    if (!lite) cerrarMgr();
  }
  setTimeout(aplicarModo, 60);

  if (termBtn) termBtn.addEventListener('click', async () => {
    if (!window.overlayAPI || !window.overlayAPI.terminal) return;
    await window.overlayAPI.terminal('');
  });

  // ---- Árbol de actividad: qué está haciendo JARVIS ----
  const ttRoot = $('thinkTree');
  const ttTitle = $('ttTitle');
  const ttList = $('ttList');
  let ttSteps = [];
  let ttCurrent = 'EN REPOSO';
  let ttLastBeat = Date.now();
  function ttSet(title, cls) {
    if (!ttRoot || !ttTitle) return;
    ttTitle.textContent = title;
    ttCurrent = title;
    ttLastBeat = Date.now();
    ttRoot.classList.remove('think', 'speak', 'busy');
    if (cls) ttRoot.classList.add(cls);
  }
  function ttRender() {
    if (!ttList) return;
    ttList.textContent = '';
    for (const s of ttSteps) {
      const li = document.createElement('li');
      li.textContent = s.text;
      if (s.state) li.className = s.state;
      ttList.appendChild(li);
    }
  }
  function ttAdd(text, state) {
    ttLastBeat = Date.now();
    const prev = ttSteps[ttSteps.length - 1];
    if (prev && prev.text === text) { ttRender(); return; }
    ttSteps.push({ text: String(text || '').slice(0, 70), state: state || 'now' });
    if (ttSteps.length > 5) ttSteps.shift();
    ttRender();
  }
  let ttHealBusy = false;
  function ttClear(keepIdle) {
    ttSteps = [];
    if (ttList) ttList.textContent = '';
    if (keepIdle !== false) ttSet('EN REPOSO', null);
  }

  // El estado mentía: se quedaba en "RESPONDIENDO" para siempre cuando faltaba
  // el evento de fin (una respuesta cortada, o una herramienta que avisa a
  // medias). Aquí se vigila: si pasan unos segundos sin noticias Y el rótulo es
  // de los que duran poco, se vuelve solo a "EN REPOSO". Los rótulos de trabajo
  // real (ACTUANDO, INVESTIGANDO, REPARANDO) no se tocan: esos sí pueden tardar.
  const TT_TRANSIENT = ['RESPONDIENDO', 'PENSANDO', 'ESCUCHANDO'];
  setInterval(() => {
    if (ttHealBusy) return;
    if (!TT_TRANSIENT.includes(ttCurrent)) return;
    if (Date.now() - ttLastBeat > 6000) ttClear();
  }, 700);

  // ---- Tarjeta de autocorrección ----
  const healCard = $('healCard');
  const healDot = $('healDot');
  const healText = $('healText');
  const healBtns = $('healBtns');
  const healAllowBtn = $('healAllowBtn');
  const healDenyBtn = $('healDenyBtn');
  let healPendingId = null;
  const HEAL_STAGE_TXT = {
    found: 'Error detectado',
    fixing: 'Reparando',
    fixed: 'Error solucionado',
    needs_user: 'Necesita su ayuda',
    escalated: 'Sigo intentándolo',
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
    try {
      if (window.JarvisGestures && window.JarvisGestures.acquireCameraStream) {
        // Se le pasa el vídeo para que pueda comprobar que llegan imágenes de
        // verdad y no un stream corrupto que 'abre' pero está negro.
        cameraStream = await window.JarvisGestures.acquireCameraStream(cameraModeVideo);
      } else {
        cameraStream = await navigator.mediaDevices.getUserMedia({ video: true, audio: false });
      }
      cameraModeVideo.srcObject = cameraStream;
      await cameraModeVideo.play();
      cameraMode.hidden = false;
      document.body.classList.add('camera-active');
      return true;
    } catch (err) {
      const detail = err && err.message ? err.message : err;
      addMsg('sys', 'No pude abrir la cámara: ' + detail);
      cameraMode.hidden = true;
      sendWs({ type: 'error_report', source: 'camera',
               error: String('getUserMedia falló: ' + detail).slice(0, 300),
               context: 'overlay cameraStart (permiso ausente o cámara ocupada)' });
      return false;
    }
  }

  function cameraStop() {
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
      if (ok) {
        for (let i = 0; i < 20 && !cameraModeVideo.videoWidth; i++) {
          await new Promise((r) => setTimeout(r, 250));
        }
      }
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
  // y se reporta al cerebro para auto-reparación. Se deduplica: una web dentro
  // de la pizarra puede repetir el mismo fallo y no debe llenar el bus.
  const _seenErrors = new Map();
  function reportUiError(kind, text) {
    const key = kind + '|' + String(text).slice(0, 80);
    const n = (_seenErrors.get(key) || 0) + 1;
    _seenErrors.set(key, n);
    if (n === 1) {
      try { addMsg('sys', text); } catch (e) { /* noop */ }
    } else if (n === 2) {
      try { addMsg('sys', text + ' (repite)'); } catch (e) { /* noop */ }
    }
    if (n > 2) return;
    try {
      sendWs({ type: 'error_report', source: 'overlay',
               error: String(text).slice(0, 300), context: 'win+j overlay' });
    } catch (e) { /* noop */ }
  }

  window.addEventListener('error', (e) => {
    try {
      reportUiError('error', 'Error interno: ' + (e && e.message ? e.message : e));
    } catch { /* noop */ }
  });
  window.addEventListener('unhandledrejection', (e) => {
    try {
      const r = (e && e.reason && (e.reason.message || e.reason)) || e;
      reportUiError('promesa', 'Fallo asíncrono: ' + r);
    } catch { /* noop */ }
  });

  function setWsDot(on) {
    try {
      const dot = $('wsDot');
      if (dot) {
        dot.classList.toggle('on', !!on);
        dot.title = on ? 'Conectado al cerebro' : 'Sin conexión';
      }
      // El estado del enlace también va en la barra de arriba, que es lo único
      // que se ve cuando hay una web abierta dentro de la pizarra.
      const link = $('ttLink');
      if (link) {
        link.classList.toggle('on', !!on);
        link.classList.toggle('off', !on);
        link.textContent = on ? 'EN LÍNEA' : 'RECONECTANDO';
        link.title = on ? 'Enlace con el cerebro correcto' : 'Sin conexión con el cerebro';
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
        ttSet('RESPONDIENDO', 'speak');
        ttAdd('respondiendo: ' + String(d.text).slice(0, 60), 'now');
        setTimeout(ttClear, 6000);
      } else if (d.type === 'conversation_state') {
        if (d.state === 'listening') ttSet('ESCUCHANDO', 'think');
        else if (d.state === 'thinking') { ttClear(false); ttSet('PENSANDO', 'think'); ttAdd('procesando su petición', 'now'); }
        else if (d.state === 'speaking') ttSet('RESPONDIENDO', 'speak');
        else if (!ttHealBusy) ttClear();
      } else if (d.type === 'task_step') {
        const slab = String(d.label || '').slice(0, 70);
        if (d.first || !d.done) { ttClear(false); ttSet('INVESTIGANDO', 'busy'); }
        if (slab) ttAdd(slab, d.done ? 'done' : 'now');
        if (d.done) setTimeout(() => { if (!ttHealBusy) ttClear(); }, 12000);
      } else if (d.type === 'user_heard') {
        const ut = String(d.text || '').slice(0, 60);
        if (ut) { ttClear(false); ttSet('PENSANDO', 'think'); ttAdd('«' + ut + '»', 'now'); }
      } else if (d.type === 'action_status') {
        const done = d.state === 'done' || d.state === 'failed' || d.state === 'cancelled';
        const lbl = d.label || d.tool || '';
        if (!done && lbl) { ttClear(false); ttSet('ACTUANDO', 'busy'); ttAdd(lbl, 'now'); }
        else if (done) { ttAdd(lbl || 'acción', d.state === 'done' ? 'done' : 'fail'); setTimeout(ttClear, 5000); }
      } else if (d.type === 'selfheal_update') {
        healUpdate(d);
        ttHealBusy = (d.stage === 'fixing' || d.stage === 'found');
        const head = d.stage === 'fixed' ? 'ERROR SOLUCIONADO'
          : d.stage === 'needs_user' ? 'NECESITA SU AYUDA'
          : d.stage === 'fixing' ? 'REPARANDO' : 'ERROR DETECTADO';
        ttClear(false);
        ttSet(head, d.stage === 'fixed' ? null : 'busy');
        if (d.source) ttAdd('origen: ' + d.source, 'now');
        if (d.error) ttAdd(d.error, d.stage === 'fixed' ? 'done' : 'now');
        if (d.note) ttAdd(d.note, d.stage === 'fixed' ? 'done' : 'now');
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
      } else if (d.type === 'open_webapp' && d.url) {
        // JARVIS pide abrir una webapp dentro de la pizarra.
        openInside(d.label || d.url, d.url);
        if (window.overlayAPI.show) window.overlayAPI.show();
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
    handCursor.style.left = handStagePos.x + 'px';
    handCursor.style.top = handStagePos.y + 'px';
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
      const d = Math.hypot(cx - pos.x, cy - pos.y);
      if (d < bestDist) {
        bestDist = d;
        best = c.el;
      }
    }
    return best;
  }

  function processHandDrag(delta) {
    const wa = window.screen;
    const sensX = wa.availWidth / 6;
    const sensY = wa.availHeight / 6;
    handStagePos.x += delta.x * sensX;
    handStagePos.y += delta.y * sensY;
    handStagePos.x = Math.max(0, Math.min(wa.availWidth, handStagePos.x));
    handStagePos.y = Math.max(0, Math.min(wa.availHeight, handStagePos.y));
    moveHandPointer();

    if (!grabbedCard) {
      grabbedCard = handPickCard(handStagePos);
      if (grabbedCard) {
        grabbedCard.style.zIndex = String(++zTop);
        const r = grabbedCard.getBoundingClientRect();
        grabbedCard.style.left = (handStagePos.x - r.width / 2) + 'px';
        grabbedCard.style.top = (handStagePos.y - r.height / 2) + 'px';
      }
      return;
    }
    const r = grabbedCard.getBoundingClientRect();
    grabbedCard.style.left = (handStagePos.x - r.width / 2) + 'px';
    grabbedCard.style.top = (handStagePos.y - r.height / 2) + 'px';
    fitInScreen(grabbedCard);
  }

  // ---- Modo tableta: el recuadro de la cámara se comporta como una
  // superficie táctil. Mover los dedos mueve el puntero por toda la pizarra
  // sin pellizcar nada, como se mueve el dedo en un trackpad.
  const ZONA_TABLETA = 0.92;   // margen: no usar los bordes de la imagen
  let punteroVivo = false;

  // Aviso corto al lado del puntero, para no tener que mirar el chat.
  function flashHandHint(texto) {
    let hint = document.getElementById('handHint');
    if (!hint) {
      hint = document.createElement('div');
      hint.id = 'handHint';
      hint.className = 'hand-hint';
      document.body.appendChild(hint);
    }
    hint.textContent = texto;
    hint.classList.add('show');
    clearTimeout(hint._t);
    hint._t = setTimeout(() => hint.classList.remove('show'), 2200);
  }

  function punteroDesdeDedo(p) {
    if (!p) { punteroVivo = false; return; }
    const wa = window.screen;
    const cx = (p.x - (1 - ZONA_TABLETA) / 2) / ZONA_TABLETA;
    const cy = (p.y - (1 - ZONA_TABLETA) / 2) / ZONA_TABLETA;
    handStagePos.x = Math.max(0, Math.min(wa.availWidth, cx * wa.availWidth));
    handStagePos.y = Math.max(0, Math.min(wa.availHeight, cy * wa.availHeight));
    punteroVivo = true;
    if (!handPointerActive) showHandPointer();
    moveHandPointer();
  }

  // Clic: doble pellizco con el dedo. Se lo pone el tracker.
  function clickEnPunto(x, y) {
    const el = document.elementFromPoint(x, y);
    if (!el) return false;
    const pulsable = el.closest('button, .card, [data-clickable], a, input, .webapp-card');
    if (!pulsable) return false;
    // Un clic de verdad: mismo efecto que si lo pulsara el ratón.
    pulsable.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, clientX: x, clientY: y }));
    pulsable.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, clientX: x, clientY: y }));
    pulsable.click();
    return true;
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
    const on = gesturesBtn.getAttribute('aria-pressed') === 'true';
    if (on) {
      stopGestures();
      return;
    }
    try {
      gesturesBtn.textContent = '⏳';
      gesturesBtn.setAttribute('aria-pressed', 'true');
      document.body.classList.add('gestures-on');
      await window.JarvisGestures.loadMediaPipe();
      const Tracker = window.JarvisGestures.HandTracker;
      tracker = new Tracker(camVideo, camOverlay, {
        // Modo tableta: la yema del índice mueve el puntero por la pizarra.
        onPointer: (p, nManos) => {
          punteroDesdeDedo(p);
          handCursor.classList.toggle('tracking', !!p);
          handCursor.dataset.hands = String(nManos || 0);
        },
        // Doble pellizco rápido = clic donde esté el puntero.
        onClick: (info) => {
          if (info && typeof info.x === 'number') {
            const wa = window.screen;
            const cx = (info.x - (1 - ZONA_TABLETA) / 2) / ZONA_TABLETA * wa.availWidth;
            const cy = (info.y - (1 - ZONA_TABLETA) / 2) / ZONA_TABLETA * wa.availHeight;
            handStagePos.x = Math.max(0, Math.min(wa.availWidth, cx));
            handStagePos.y = Math.max(0, Math.min(wa.availHeight, cy));
            moveHandPointer();
          }
          const ok = clickEnPunto(handStagePos.x, handStagePos.y);
          handCursor.classList.add('clicking');
          setTimeout(() => handCursor.classList.remove('clicking'), 220);
          if (!ok) flashHandHint('Aquí no hay nada que pulsar, señor.');
        },
        onRotate: (dx, dy) => {
          processHandDrag({ x: dx / 5, y: dy / 5 });
        },
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
        onStatus: (s) => {
          handCursor.classList.toggle('pinching', s.mode !== 'idle');
          if (s.mode !== 'spin' && s.mode !== 'zoom') releaseHand();
        },
      });
      handStagePos = { x: window.screen.availWidth / 2, y: window.screen.availHeight * 0.35 };
      showHandPointer();
      await tracker.start();
      gesturesBtn.textContent = '👐';
      gesturesBtn.title = 'Gestos activos (toca para desactivar)';
    } catch (err) {
      console.error('Gestos:', err);
      gesturesBtn.textContent = '👐';
      gesturesBtn.setAttribute('aria-pressed', 'false');
      document.body.classList.remove('gestures-on');
      hideHandPointer();
      const detail = String((err && err.message ? err.message : err)).slice(0, 300);
      addMsg('sys', 'No he podido activar la cámara, señor: ' + detail);
      sendWs({ type: 'error_report', source: 'camera-tracking',
               error: detail,
               context: 'overlay toggleGestures (MediaPipe/getUserMedia)' });
      stopGestures();
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
  // La pizarra tiene que dejar ver el escritorio. El tema traía un color
  // totalmente opaco (#0b1020) y, al aplicarse con !important, tapaba el
  // escritorio. Aquí se conserva SU color pero con transparencia: mismo tono,
  // fondo traslúcido. Para cambiarle la intensidad, toca BOARD_ALPHA.
  const BOARD_ALPHA = 0.52;
  function colorTranslucido(color, alpha) {
    const c = String(color == null ? '' : color).trim();
    let m = /^#([0-9a-f]{3,8})$/i.exec(c);
    if (m) {
      let h = m[1];
      if (h.length === 3 || h.length === 4) {
        h = h.split('').map(x => x + x).join('');
      }
      if (h.length >= 6) {
        return 'rgba(' + parseInt(h.slice(0, 2), 16) + ','
          + parseInt(h.slice(2, 4), 16) + ','
          + parseInt(h.slice(4, 6), 16) + ',' + alpha + ')';
      }
    }
    m = /^rgba?\(([^)]+)\)$/i.exec(c);
    if (m) {
      const p = m[1].split(',').map(s => s.trim());
      if (p.length >= 3) return 'rgba(' + p[0] + ',' + p[1] + ',' + p[2] + ',' + alpha + ')';
    }
    return 'rgba(11, 16, 32, ' + alpha + ')';
  }
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
        'body{background:' + colorTranslucido(T.bg, BOARD_ALPHA) + '!important;color:' + T.ink + '!important;}' +
        '@keyframes jarvisFadeIn{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:none}}' +
        '@keyframes jarvisSlideIn{from{opacity:0;transform:translateX(-16px)}to{opacity:1;transform:none}}' +
        '@keyframes jarvisPulse{0%,100%{filter:brightness(1)}50%{filter:brightness(' + inten + ')}}' +
        '@keyframes jarvisSpin{to{transform:rotate(360deg)}}' +
        '.msg.user{background:' + M.user_bg + '!important;border-radius:' + M.radius + 'px!important;font-size:' + M.font + 'px!important;}' +
        '.msg.jarvis,.msg.assistant{background:' + M.jarvis_bg + '!important;color:' + T.ink + '!important;border-radius:' + M.radius + 'px!important;font-size:' + M.font + 'px!important;}' +
        '.msg.sys{color:' + M.sys_color + '!important;}' +
        '.app-search:focus{border-color:' + T.accent + '!important;box-shadow:0 0 0 3px ' + T.accent + '26,0 0 18px ' + T.accent + '33!important;}' +
        '.app-tile:hover{border-color:' + T.accent + '!important;box-shadow:0 8px 20px rgba(0,0,0,.42),0 0 22px ' + T.accent + '33,inset 0 1px 0 rgba(255,255,255,.08)!important;}' +
        '.app-tile:hover .app-name,.side-tab.active{color:' + T.ink + '!important;}' +
        '.app-tile:focus-visible,.side-tab:focus-visible{outline-color:' + T.accent + '!important;}';
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
})();
/* Parallax sutil del orbe con el ratón. La voz NO se genera aquí:
   solo el brain habla (Piper) para que suene igual en ventana, orbe,
   Discord y WhatsApp. Una sola voz, la de JARVIS. */
(function () {
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
        });
      });
    });
  }).observe(document.body, { childList: true, subtree: true });
})();
