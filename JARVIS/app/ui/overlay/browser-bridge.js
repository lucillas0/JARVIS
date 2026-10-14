/* Puente de la pizarra cuando se sirve en el navegador (modo LITE, servidor
 * o Docker). En el escritorio lo pone el preload de Electron como
 * `window.overlayAPI`; aquí se implementa la misma interfaz contra HTTP y el
 * WebSocket del brain, de forma que app.js no se entera de nada.
 *
 * Limitación honesta: las webapps dentro de la pizarra usan <webview>, que es
 * de Electron. En un navegador eso no existe y además casi todas las webs
 * bloquean que se las incrustre (cabecera X-Frame-Options), así que aquí se
 * abren en una pestaña nueva. Todo lo demás —programas, ficheros, chat, orbe,
 * animaciones, rendimiento y peticiones al brain— funciona igual.
 */
(function () {
  if (window.overlayAPI) return; // estamos en Electron: no tocar nada

  const memory = {};

  // El token lo inyecta el brain al servir la pagina (ver _inyecta_token).
  // Sin el, el WebSocket se rechaza y las rutas de claves devuelven 401.
  const T = window.JARVIS_TOKEN || '';
  const QS = window.JARVIS_TOKEN_URL || '';

  function conToken(url) {
    if (!T || !url || url.startsWith('http') || url.indexOf('token=') >= 0) return url;
    return url + (url.indexOf('?') >= 0 ? '&' : '?') + 'token=' + encodeURIComponent(T);
  }

  async function get(url, body) {
    const cabeceras = body ? { 'Content-Type': 'application/json' } : undefined;
    if (T) cabeceras = Object.assign({}, cabeceras || {}, { 'X-Jarvis-Token': T });
    const r = await fetch(conToken(url), body
      ? { method: 'POST', headers: cabeceras, body: JSON.stringify(body) }
      : { headers: cabeceras });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }

  // Mapa de webapps, el mismo que usa la pizarra de escritorio. Se pide una
  // vez y se cachea: son 50 entradas fijas y no cambian entre recargas.
  let _webapps = null;
  async function cargarWebapps() {
    if (_webapps) return _webapps;
    try {
      const d = await get('/api/board/webapps');
      _webapps = d.webapps || {};
    } catch (e) {
      _webapps = {};
    }
    return _webapps;
  }
  function buscarWebapp(nombre) {
    const clave = String(nombre || '').toLowerCase().replace(/[^a-z0-9]/g, '');
    const v = _webapps ? _webapps[clave] : null;
    if (!v) return null;
    // El mapa del brain guarda las webapps como tupla (etiqueta, url). Si se
    // toma tal cual, .url sale undefined y la pestaña se abre en blanco.
    if (Array.isArray(v)) return { label: v[0], url: v[1] };
    if (typeof v === 'string') return { label: nombre, url: v };
    return (v.url ? v : null);
  }
  cargarWebapps();

  window.overlayAPI = {
    // Datos de la pizarra: el brain devuelve la misma forma que el IPC.
    getApps: () => get('/api/board/apps').then((d) => d.apps || []).catch(() => []),
    getRecent: () => get('/api/board/recent').then((d) => d.files || []).catch(() => []),
    listDir: (dir) => get('/api/board/dir' +
      (dir ? '?path=' + encodeURIComponent(dir) : '')).catch(() => null),
    open: (item) => get('/api/board/open', item || {}).catch(() => ({ ok: false })),

    // Gestor de instaladas y terminal. En LITE no hay ventana de escritorio,
    // así que esto es lo que permite abrir CUALQUIER programa del equipo
    // (Docker Desktop, un navegador, la terminal) desde la pizarra del
    // navegador.
    // ¿Estamos en un servidor (LITE) o en el escritorio (FULL)? Lo decide el
    // brain. En FULL no se muestra el gestor de programas: en un escritorio
    // ya se puede abrir cualquier cosa como se quiera, así que el panel sobra.
    modo: () => get('/api/mode').then((d) => d.modo || 'full').catch(() => 'full'),

    listInstalled: (q) => get('/api/board/instaladas' +
      (q ? '?q=' + encodeURIComponent(q) : '')).then((d) => d.apps || []).catch(() => []),
    terminal: (shell) => get('/api/board/terminal', { shell: shell || '' })
      .catch(() => ({ ok: false })),

    // Abrir cosas. Las webapps se van a una pestaña: en el navegador no hay
    // <webview> y los sitios de aquí no dejan incrustrarse.
    //
    // OJO con webapp(name): app.js espera que devuelva {label, url} si es una
    // webapp, y si no devuelve nada sigue por openItem() para lanzar el
    // programa. Antes devolvía el valor de window.open(), que tiene 'url' y
    // por tanto la pizarra creía que podía mostrarla dentro... y la
    // aplicación real no se abría jamás. Aquí se abre en pestaña y se
    // devuelve null para que openItem haga su trabajo.
    webapp: (name) => {
      const w = buscarWebapp(name);
      if (w) window.open(w.url, '_blank', 'noopener');
      return null;
    },
    openWeb: (url) => { window.open(url, '_blank', 'noopener'); return null; },
    external: (url) => window.open(url, '_blank', 'noopener'),
    show: () => document.body.classList.remove('preboot'),
    close: () => {
      if (history.length > 1) history.back();
      else document.body.classList.add('preboot');
    },
    main: () => { window.location.href = '/'; },

    // Ciclo de vida de la ventana. En el navegador la página ya está
    // visible, así que se contesta al instante: si no, la pizarra esperaría
    // un arranque que no llega y se vería en blanco.
    onBoot: (cb) => { memory.boot = cb; setTimeout(cb, 40); },
    onHide: (cb) => { memory.hide = cb; },
    onPrepare: (cb) => setTimeout(cb, 0),
    onPaintProbe: (cb) => requestAnimationFrame(() => requestAnimationFrame(cb)),
    onWorkspace: (cb) => { memory.workspace = cb; },
    onPerf: (cb) => { memory.perf = cb; },
    prepared: () => { },
    painted: () => { },
    hiddenDone: () => { },
  };

  // Se reenvían al puente los eventos del WebSocket que la pizarra consume.
  function conectar() {
    let ws;
    try {
      ws = new WebSocket('ws://' + location.host + '/ws' + (QS || (T ? '?token=' + encodeURIComponent(T) : '')));
    } catch (e) {
      setTimeout(conectar, 3000);
      return;
    }
    ws.addEventListener('message', (e) => {
      let d;
      try { d = JSON.parse(e.data); } catch { return; }
      if (d.type === 'workspace:state' && memory.workspace) memory.workspace(d);
      else if (d.type === 'perf:update' && memory.perf) memory.perf(d);
      // Si el brain manda abrir una webapp ("abre youtube"), aquí no hay
      // <webview>: se abre en una pestaña, que es lo mismo que hacemos al
      // pulsar su tarjeta.
      else if (d.type === 'open_webapp' && d.url) window.open(d.url, '_blank', 'noopener');
    });
    ws.addEventListener('close', () => setTimeout(conectar, 3000));
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', conectar);
  } else {
    conectar();
  }
})();
