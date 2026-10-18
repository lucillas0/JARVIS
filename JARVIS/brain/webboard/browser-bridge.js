
// --- Enlace con el cerebro -------------------------------------------------
// El token lo inyecta el servidor al servir la pagina (ver _inyecta_token en
// brain/main.py): window.JARVIS_TOKEN_URL = "?token=...". Sin el no hay WS.
// Y la URL sale de location, no de 127.0.0.1: en un NAS se entra por la IP y
// el 127.0.0.1 del navegador es el del PC del usuario, no el del servidor.
function _jarvisWsUrl() {
  const esquema = location.protocol === 'https:' ? 'wss://' : 'ws://';
  const token = (typeof window !== 'undefined' && window.JARVIS_TOKEN_URL) || '';
  return esquema + location.host + '/ws' + token;
}

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

  // El token lo inyecta el servidor al servir la pagina (ver _inyecta_token en
// brain/main.py). /api/board/* abre ficheros y una terminal, asi que sin token
// no se sirven: es lo que evita que cualquiera de la LAN abra una shell en el
// equipo del servidor. Mismo criterio que el puente de Electron
// (app/ui/overlay/browser-bridge.js): cabecera y, por si acaso, tambien en la
// query.
function conToken(url) {
    const t = (typeof window !== 'undefined' && window.JARVIS_TOKEN) || '';
    if (!t || !url || url.indexOf('token=') >= 0) return url;
    return url + (url.indexOf('?') >= 0 ? '&' : '?') + 'token=' + encodeURIComponent(t);
  }

  async function get(url, body) {
    const cabeceras = {};
    if (body) cabeceras['Content-Type'] = 'application/json';
    const t = (typeof window !== 'undefined' && window.JARVIS_TOKEN) || '';
    if (t) cabeceras['X-Jarvis-Token'] = t;
    const destino = conToken(url);
    const r = await fetch(destino, body
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

  // Abre un fichero en el navegador. inline lo pone el servidor segun la
  // extension: imagenes, PDF, texto, audio y video se ven; el resto se baja.
function verEnElNavegador(ruta) {
  const url = window.overlayAPI.urlFichero(ruta);
  window.open(url, '_blank', 'noopener');
}

window.overlayAPI = {
    getApps: () => get('/api/board/apps').then((d) => d.apps || []).catch(() => []),
    getRecent: () => get('/api/board/recent').then((d) => d.files || []).catch(() => []),
    listDir: (dir) => get('/api/board/dir' +
      (dir ? '?path=' + encodeURIComponent(dir) : '')).catch(() => null),
    // Abrir un fichero. En un NAS no hay escritorio, así que no hay nada que
    // lanzar: el brain responde sin_escritorio y entonces lo que se hace es
    // mandarlo al navegador con /api/board/fichero. En Windows el brain sí
    // que abre con os.startfile y esto ni se usa.
    open: (item) => get('/api/board/open', item || {})
      .then((r) => {
        if (r && r.sin_escritorio && r.descargar) {
          verEnElNavegador(r.descargar);
          return { ok: true, abierto_en: 'navegador' };
        }
        return r || { ok: false };
      })
      .catch(() => ({ ok: false })),

    // URL con el token puesto, para abrirla en una pestaña. El token va en la
    // query porque el navegador no manda cabeceras en un <a> ni en un
    // window.open, y sin el el endpoint devuelve 401.
    //
    // OJO con el separador: window.JARVIS_TOKEN_URL ya empieza por "?token=",
    // y pegarlo tal cual detrás de "?path=..." deja dos interrogantes. El token
    // se acababa dentro del valor del path y la descarga devolvia 401 siempre.
    urlFichero: (path) => {
      const token = (window.JARVIS_TOKEN_URL || '').replace(/^\?/, '&');
      return '/api/board/fichero?path=' + encodeURIComponent(path) + token;
    },

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
      ws = new WebSocket(_jarvisWsUrl());
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
