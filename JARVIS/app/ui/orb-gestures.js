/* Gestos de mano DENTRO de la ventana del orbe.
 *
 * El orbe es pequeño y está casi siempre en medio de la pantalla, así que la
 * caja de la cámara se pone como una banda discreta en una esquina y se puede
 * plegar. La mano funciona como una tableta: mueve el puntero sobre el orbe y
 * un doble pellizco rápido es un clic (que devuelve a la ventana principal).
 */
(function () {
  const capa = document.getElementById('handLayer');
  const btn = document.getElementById('gesturesBtn');
  const video = document.getElementById('camVideo');
  const overlay = document.getElementById('camOverlay');
  const estado = document.getElementById('cameraStatus');
  const cursor = document.getElementById('handCursor');
  if (!capa || !btn || !video || !overlay || !window.JarvisGestures) return;

  let tracker = null;

  const ETIQUETAS = {
    idle: 'SIN MANO', point: 'PUNTERO', spin: 'PELLIZCO', zoom: 'ZOOM',
  };

  function pista(texto) {
    estado.textContent = texto || '';
    estado.className = 'camera-status' + (texto === 'PUNTERO' ? ' ok' : '');
  }

  function parar() {
    if (tracker) { try { tracker.stop(); } catch { /* noop */ } }
    tracker = null;
    if (cursor) cursor.hidden = true;
    btn.setAttribute('aria-pressed', 'false');
    btn.textContent = '👐 GESTOS';
    capa.classList.remove('on');
    pista('Gestos apagados');
  }

  function moverCursor(p) {
    if (!cursor) return;
    if (!p) { cursor.hidden = true; return; }
    cursor.hidden = false;
    // Mapeo de tableta: el recuadro de la cámara cubre toda la ventana.
    cursor.style.left = (p.x * window.innerWidth) + 'px';
    cursor.style.top = (p.y * window.innerHeight) + 'px';
    cursor.classList.toggle('pinching', !!p.pinching);
  }

  function clic() {
    if (cursor) {
      cursor.classList.add('clicking');
      setTimeout(() => cursor.classList.remove('clicking'), 220);
    }
    if (window.jarvisAPI && window.jarvisAPI.restoreMain) {
      window.jarvisAPI.restoreMain();
    }
    const c = document.getElementById('orbContainer');
    if (c && typeof c.click === 'function') c.click();
  }

  async function arrancar() {
    if (tracker) { parar(); return; }
    btn.textContent = '…';
    btn.setAttribute('aria-pressed', 'true');
    try {
      await window.JarvisGestures.loadMediaPipe();
      const Tracker = window.JarvisGestures.HandTracker;
      tracker = new Tracker(video, overlay, {
        onPointer: (p) => moverCursor(p),
        onClick: () => clic(),
        // En el orbe no hay tarjetas que arrastrar: el pellizco gira el orbe.
        onRotate: (dx, dy) => { /* el orbe no gira con la mano: se deja */ void dx; void dy; },
        onZoom: () => { /* el zoom del orbe no aporta aquí */ },
        onStatus: (s) => pista(ETIQUETAS[s.mode] || s.mode),
      });
      await tracker.start();
      capa.classList.add('on');
      btn.textContent = '👐 ON';
      pista('Buscando su mano…');
    } catch (e) {
      tracker = null;
      btn.textContent = '👐 GESTOS';
      btn.setAttribute('aria-pressed', 'false');
      capa.classList.remove('on');
      pista('Cámara no disponible: ' + String((e && e.message) || e).slice(0, 80));
    }
  }

  btn.addEventListener('click', arrancar);
  window.addEventListener('beforeunload', parar);
  document.addEventListener('visibilitychange', () => {
    if (document.hidden) parar();
  });

  pista('Gestos apagados');
})();
