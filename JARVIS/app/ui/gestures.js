/* Gestos de mano con MediaPipe — port de lib/handTracker.ts (ultron-by-sagar-builds). */
(function () {
  // El paquete tasks-vision NO publica bundle IIFE: su entrada es ESM
  // (vision_bundle.min.mjs). Se carga con import() dinámico, con fallback
  // a unpkg si jsdelivr falla.
  const MP_ESM = [
    'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.35/vision_bundle.min.mjs',
    'https://unpkg.com/@mediapipe/tasks-vision@0.10.35/vision_bundle.min.mjs',
  ];
  const WASM_CDN =
    'https://cdn.jsdelivr.net/npm/@mediapipe/tasks-vision@0.10.35/wasm';
  const MODEL_URL =
    'https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task';

  const WRIST = 0;
  const THUMB_TIP = 4;
  const INDEX_TIP = 8;
  const MIDDLE_MCP = 9;

  // Esqueleto de la mano (los 21 puntos que devuelve MediaPipe), para dibujarla
  // entera y que se vea a dónde apunta el dedo.
  const HAND_BONES = [
    [0, 1], [1, 2], [2, 3], [3, 4],
    [0, 5], [5, 6], [6, 7], [7, 8],
    [5, 9], [9, 10], [10, 11], [11, 12],
    [9, 13], [13, 14], [14, 15], [15, 16],
    [13, 17], [17, 18], [18, 19], [19, 20],
    [0, 17],
  ];

  const PINCH_ON = 0.32;
  const PINCH_OFF = 0.45;
  const ROTATE_SPEED = 5.0;
  const SMOOTHING = 0.4;

  // Modo «tableta»: la caja de la cámara se comporta como una superficie
  // táctil. La yema del índice es el puntero y se mueve por toda la pantalla
  // sin necesidad de pellizcar.
  const PUNTERO_SUAVE = 0.32;
  // Un pellizco rapidito cuenta como toque, y dos seguidos cuentan como clic
  // (como doble toque en una tableta).
  const TOQUE_MS = 280;
  const DOBLE_MS = 520;

  function dist2d(a, b) {
    return Math.hypot(a.x - b.x, a.y - b.y);
  }

  function resolveVisionGlobal(name) {
    // 1) namespace del import() dinámico (vía ESM, lo normal ahora).
    try {
      if (_mpNs && _mpNs[name]) return _mpNs[name];
    } catch { /* noop */ }
    // 2) El bundle IIFE exponía los símbolos como globales sueltos, pero según
    // la versión pueden venir bajo window.vision o window.tasksVision.
    if (window[name]) return window[name];
    for (const ns of ['vision', 'tasksVision', 'mediapipe']) {
      try {
        if (window[ns] && window[ns][name]) return window[ns][name];
      } catch { /* noop */ }
    }
    return null;
  }

  let _mpPromise = null;
  let _mpNs = null;

  const _wait = (ms) => new Promise((r) => setTimeout(r, ms));

  // ------------------------------------------------------------------ log
  // Todo lo que pasa al abrir la cámara queda en la consola (el proceso
  // principal lo vuelca a camara.log) y también en window.__camaraLog, que se
  // puede volcar desde devtools. Antes solo se veía un error final y no había
  // forma de saber en qué paso concreto se había quedado.
  window.__camaraLog = window.__camaraLog || [];
  function logCam(msg, extra) {
    const t = new Date().toLocaleTimeString('es-ES');
    const linea = t + ' ' + msg + (extra ? ' ' + extra : '');
    try { console.log('[camara] ' + linea); } catch { /* noop */ }
    try {
      window.__camaraLog.push(linea);
      if (window.__camaraLog.length > 200) window.__camaraLog.shift();
    } catch { /* noop */ }
  }
  function describirError(e) { return e && e.name ? e.name + ': ' + e.message : String(e); }

  // El Cerebro es quien tiene la cámara en este equipo (por DirectShow). Preguntarle
  // primero es barato y evita disparar la escalera del navegador a ciegas.
  async function estadoCerebro() {
    try {
      const r = await fetch('http://127.0.0.1:8000/api/camara/estado',
        { headers: await cabecerasCerebro() });
      const j = await r.json();
      return j && j.ok ? j.estado : null;
    } catch (e) {
      logCam('el Cerebro no responde en /api/camara/estado ->', describirError(e));
      return null;
    }
  }

  // El token lo da el preload (window.jarvisNet). El cerebro lo exige ya en
  // /api/camara/*: la camara del señor no se abre porque lo pida cualquiera de
  // la LAN. Si el token no esta (no es Electron), se sigue intentando: el
  // 401 se ve en el log y no se rompe la interfaz.
  async function cabecerasCerebro() {
    try {
      if (window.jarvisNet && window.jarvisNet.token) {
        const t = await window.jarvisNet.token();
        return t ? { 'X-Jarvis-Token': t } : {};
      }
    } catch (e) { /* sin token: que lo decida el servidor */ }
    return {};
  }

  // Fuente alternativa: las fotos las saca el CEREBRO con OpenCV (por
  // DirectShow). En equipos sin Media Foundation el navegador no abre la
  // cámara, pero por esta vía sí. Se intenta primero esta; si el cerebro no
  // contesta, se cae a la cámara del navegador como antes.
  const MJPEG_CEREBRO = 'http://127.0.0.1:8000/api/camara/mjpeg';

  // Este stream va en un <img>/fetch simple: ahi no caben cabeceras, asi que el
  // token va en la query, que el cerebro acepta justamente para las descargas y
  // para esto. Se resuelve al empezar, no en cada foto.
  let _tokenCerebro = '';
  async function urlMjpeg() {
    if (!_tokenCerebro) {
      try {
        if (window.jarvisNet && window.jarvisNet.token) {
          _tokenCerebro = (await window.jarvisNet.token()) || '';
        }
      } catch (e) { /* sin token: lo dira el servidor */ }
    }
    return _tokenCerebro ? MJPEG_CEREBRO + '?token=' + encodeURIComponent(_tokenCerebro)
      : MJPEG_CEREBRO;
  }

  // El bucle de sondeo se puede cancelar. Antes era un 'while (true)' sin
  // salida: seguía pidiendo fotos para siempre aunque el tracker se apagara, y
  // como esas fotos ABREN la cámara en el cerebro, la cámara se quedaba
  // cogida eternamente y el navegador ya no podía abrirla nunca. Se quedaba
  // en 'SIN CAMARA' para siempre.
  let sondeoCerebro = null;

  function stopBrainStream() {
    if (sondeoCerebro) {
      sondeoCerebro.cancel = true;
      if (typeof sondeoCerebro.cerrar === 'function') {
        try { sondeoCerebro.cerrar(); } catch { /* noop */ }
      }
      sondeoCerebro = null;
    }
  }

  async function acquireBrainStream() {
    // Consumimos el MJPEG como bytes, en vez de usar <img src=...>: Chromium
    // no garantiza un evento load por cada parte multipart, así que esa vía
    // puede dejar un fotograma congelado o hacer que el navegador reclame una
    // cámara que ya está abierta por DirectShow.
    const lienzo = document.createElement('canvas');
    lienzo.width = 640;
    lienzo.height = 480;
    const ctx = lienzo.getContext('2d');
    if (!ctx || typeof lienzo.captureStream !== 'function') {
      logCam('canvas/captureStream no disponible para recibir el MJPEG.');
      return null;
    }

    let stream = lienzo.captureStream(0);
    let pista = stream && stream.getVideoTracks()[0];
    if (!pista || typeof pista.requestFrame !== 'function') {
      try { if (stream) stream.getTracks().forEach((t) => t.stop()); } catch { /* noop */ }
      stream = lienzo.captureStream(15);
      pista = stream && stream.getVideoTracks()[0];
    }
    if (!stream || !pista) return null;

    const control = {
      cancel: false, listo: false, cuadros: 0, errores: 0,
      t0: performance.now(), lector: null, abort: new AbortController(),
    };
    let resolverListo;
    let rechazarListo;
    let listoResuelto = false;
    control.ready = new Promise((resolve, reject) => {
      resolverListo = resolve;
      rechazarListo = reject;
    });
    control.cerrar = () => {
      control.cancel = true;
      try { control.abort.abort(); } catch { /* noop */ }
      try { if (control.lector) control.lector.cancel(); } catch { /* noop */ }
      try { stream.getTracks().forEach((t) => t.stop()); } catch { /* noop */ }
      if (sondeoCerebro === control) sondeoCerebro = null;
    };
    sondeoCerebro = control;

    const CRLF = new Uint8Array([13, 10]);
    const HEAD_END = new Uint8Array([13, 10, 13, 10]);
    const BOUNDARY = new TextEncoder().encode('--marcojarvis');
    let buffer = new Uint8Array(0);
    const concatenar = (a, b) => {
      const c = new Uint8Array(a.length + b.length);
      c.set(a);
      c.set(b, a.length);
      return c;
    };
    const buscar = (haystack, needle) => {
      outer: for (let i = 0; i <= haystack.length - needle.length; i += 1) {
        for (let j = 0; j < needle.length; j += 1) {
          if (haystack[i + j] !== needle[j]) continue outer;
        }
        return i;
      }
      return -1;
    };

    const consumirMJPEG = async () => {
      const response = await fetch(await urlMjpeg(), {
        cache: 'no-store', credentials: 'omit', signal: control.abort.signal,
      });
      if (!response.ok || !response.body) {
        throw new Error('HTTP ' + response.status + ' sin flujo MJPEG');
      }
      control.lector = response.body.getReader();
      const leer = async () => {
        const { value, done } = await control.lector.read();
        if (done || !value) throw new Error('el Cerebro cerró el flujo MJPEG');
        buffer = concatenar(buffer, value);
      };
      const asegurar = async (cantidad) => {
        while (buffer.length < cantidad) await leer();
      };
      const hasta = async (delimitador, limite) => {
        while (true) {
          const pos = buscar(buffer, delimitador);
          if (pos >= 0) {
            const hallado = buffer.slice(0, pos);
            buffer = buffer.slice(pos + delimitador.length);
            return hallado;
          }
          if (buffer.length > limite) throw new Error('cabecera MJPEG demasiado larga');
          await leer();
        }
      };

      await hasta(BOUNDARY, 8192);
      while (!control.cancel) {
        await asegurar(2);
        if (buffer[0] === 45 && buffer[1] === 45) break; // -- fin multipart
        const cabecera = new TextDecoder().decode(await hasta(HEAD_END, 8192));
        const match = cabecera.match(/(?:^|\r\n)content-length\s*:\s*(\d+)/i);
        if (!match) throw new Error('el fotograma MJPEG no incluye Content-Length');
        const bytes = Number(match[1]);
        if (!Number.isFinite(bytes) || bytes < 4 || bytes > 5_000_000) {
          throw new Error('tamaño de fotograma MJPEG no válido: ' + match[1]);
        }
        await asegurar(bytes + 2);
        const jpeg = buffer.slice(0, bytes);
        buffer = buffer.slice(bytes + 2); // imagen y CRLF que la termina

        const bitmap = await createImageBitmap(new Blob([jpeg], { type: 'image/jpeg' }));
        try {
          if (control.cancel) break;
          ctx.drawImage(bitmap, 0, 0, lienzo.width, lienzo.height);
          control.cuadros += 1;
          control.listo = true;
          if (pista.requestFrame) pista.requestFrame();
          if (control.cuadros === 1) {
            logCam('primer fotograma MJPEG decodificado tras ' +
              Math.round(performance.now() - control.t0) + ' ms');
          }
          if (!listoResuelto) {
            listoResuelto = true;
            resolverListo(stream);
          }
        } finally {
          bitmap.close();
        }
        await hasta(BOUNDARY, 8192);
      }
      if (!listoResuelto && !control.cancel) {
        throw new Error('el flujo MJPEG terminó antes del primer fotograma');
      }
    };

    consumirMJPEG().catch((e) => {
      if (control.cancel) return;
      control.errores += 1;
      logCam('falló la recepción MJPEG:', describirError(e));
      if (!listoResuelto) {
        listoResuelto = true;
        rechazarListo(e);
      }
    });
    logCam('recibiendo MJPEG del Cerebro:', MJPEG_CEREBRO);

    const margenMs = 10000;
    let resultado = null;
    let reloj;
    try {
      const agotado = new Promise((resolve) => {
        reloj = setTimeout(() => resolve(null), margenMs);
      });
      resultado = await Promise.race([control.ready, agotado]);
    } catch (e) {
      logCam('no llegó el primer fotograma del Cerebro:', describirError(e));
    } finally {
      clearTimeout(reloj);
    }
    if (!resultado) {
      let estado = null;
      try {
        estado = await Promise.race([estadoCerebro(), _wait(2000).then(() => null)]);
      } catch { /* el registro final debe tener plazo */ }
      logCam('sin fotograma MJPEG en ' + Math.round((performance.now() - control.t0) / 1000) +
        ' s; estado del Cerebro: ' + JSON.stringify(estado));
      control.cerrar();
      return null;
    }
    logCam('vía MJPEG lista; ' + control.cuadros + ' fotograma(s) recibidos.');
    return stream;
  }

  // Promesa con reloj: si la cámara, el WASM o el modelo no responden en el
  // plazo, se corta con un error claro. Antes, si el CDN se quedaba colgado,
  // el botón se quedaba en 'INITIALIZING…' para siempre y no se podía ni
  // volver a intentarlo ni saber qué pasaba.
  function conPlazo(promesa, ms, etiqueta) {
    let reloj;
    const plazo = new Promise((_, reject) => {
      reloj = setTimeout(
        () => reject(new Error('[' + etiqueta + '] tardó más de ' + (ms / 1000) + ' s')),
        ms);
    });
    return Promise.race([promesa, plazo]).finally(() => clearTimeout(reloj));
  }

  // ¿De verdad llegan imágenes? Una cámara puede abrirse sin dar error y sin
  // entregar nada: la de este equipo abría el puerto y devolvía ruido/negro, y
  // el código lo daba por bueno porque 'no había excepción'. Por eso hay que
  // MIRAR el fotograma antes de aceptar la cámara.
  function pareceImagenUtil(video) {
    try {
      const c = document.createElement('canvas');
      c.width = 64; c.height = 48;
      const ctx = c.getContext('2d', { willReadFrequently: true });
      if (!ctx) return true;
      ctx.drawImage(video, 0, 0, c.width, c.height);
      const d = ctx.getImageData(0, 0, c.width, c.height).data;
      let suma = 0; let suma2 = 0; let n = 0;
      for (let i = 0; i < d.length; i += 4) {
        const g = (d[i] + d[i + 1] + d[i + 2]) / 3;
        suma += g; suma2 += g * g; n += 1;
      }
      const media = suma / n;
      const desv = Math.sqrt(Math.max(0, suma2 / n - media * media));
      // Desviación bajísima = imagen plana (negro o un color sólido).
      // Media casi cero = cámara tapada o apagada.
      return media > 4 && desv > 6;
    } catch { return true; } // si no se puede medir, no se bloquea el arranque
  }

  // Abre la cámara y espera a que traiga fotogramas de verdad. Si tras un
  // razonable número de intentos sigue sin imagen, se suelta el stream y se
  // prueba la siguiente forma de pedirla.
  async function abrirYComprobar(video, cons, etiqueta, alFallar) {
    let stream = null;
    try {
      stream = await conPlazo(navigator.mediaDevices.getUserMedia(cons), 8000, 'camara');
    } catch (e) {
      video.srcObject = null;
      if (typeof alFallar === 'function') alFallar(e);
      logCam('navegador: ' + etiqueta + ' no abrió ->', describirError(e));
      return null;
    }
    const anterior = video.srcObject;
    video.srcObject = stream;
    try { await video.play(); } catch { /* some browsers need muted */ }
    // Se le da margen: el primer fotograma suele tardar en arrancar.
    for (let i = 0; i < 14; i += 1) {
      await _wait(220);
      if (video.videoWidth > 0 && pareceImagenUtil(video)) {
        return stream;
      }
    }
    // Abrió pero no entrega imagen usable: fuera.
    const errorImagen = new Error('abrió pero no entrega imagen (' +
      video.videoWidth + 'x' + video.videoHeight + ')');
    if (typeof alFallar === 'function') alFallar(errorImagen);
    logCam('navegador: ' + etiqueta + ' abrió pero SIN imagen usable (' +
      video.videoWidth + 'x' + video.videoHeight + ').');
    try { stream.getTracks().forEach((t) => t.stop()); } catch { /* */ }
    video.srcObject = anterior;
    return null;
  }

  // Abre la cámara probando CADA dispositivo (hay equipos con la cámara
  // duplicada: una entrada OK y una fantasma; el dispositivo por defecto
  // puede ser el muerto -> "Could not start video source").
  async function acquireCameraStream(video) {
    // ORDEN: primero MJPEG del Cerebro (DirectShow); si falla, se prueba la
    // cámara nativa del navegador con restricciones progresivas.
    const delCerebro = await acquireBrainStream();
    if (delCerebro) return delCerebro;
    const estadoTrasCerebro = await estadoCerebro();

    let lastErr = null;
    let devs = [];
    try {
      devs = await navigator.mediaDevices.enumerateDevices();
    } catch { /* getUserMedia decidirá */ }
    const ids = devs.filter((d) => d.kind === 'videoinput')
      .map((c) => c.deviceId).filter(Boolean);

    // Lo PRIMERO es lo más simple y sin ataduras: 'video: true' y punto. Es
    // lo que hacen las apps que funcionan (FaceDesk, la de reconocimiento
    // facial). Pedir deviceId exacto y fps forzados hace que el navegador
    // negocie un formato que esta cámara no da bien, y el resultado es un
    // stream que abre pero no trae imagen. Empezar por lo simple evita todo eso.
    const registrarError = (e) => { lastErr = e; };
    const primero = await abrirYComprobar(video, { video: true, audio: false }, 'simple', registrarError);
    if (primero) return primero;

    // Si lo simple no ha servido, ahora sí se prueban variantes, primero sin
    // deviceId y luego dispositivo por dispositivo.
    const variantes = [
      { video: { width: { ideal: 640 }, height: { ideal: 480 } }, audio: false },
      { video: { frameRate: { ideal: 30 } }, audio: false },
      { video: { width: { ideal: 320 }, height: { ideal: 240 } }, audio: false },
    ];
    for (const cons of variantes) {
      const s = await abrirYComprobar(video, cons, 'variante', registrarError);
      if (s) return s;
    }
    ids.push(undefined); // dispositivo por defecto, ya explícito
    for (const id of ids) {
      const sel = id ? { deviceId: { exact: id } } : {};
      for (const cons of [
        { video: { ...sel }, audio: false },
        { video: (id ? sel : true), audio: false },
      ]) {
        const s = await abrirYComprobar(video, cons, 'deviceId', registrarError);
        if (s) return s;
      }
    }

    // Ni el Cerebro ni el navegador.:
    const msg = lastErr && lastErr.message ? lastErr.message : lastErr;
    const denied = lastErr && lastErr.name === 'NotAllowedError';
    const noDevice = /not found|Requested device/i.test(String(msg));
    let pista;
    if (denied) {
      pista = '— permite la cámara para JARVIS en Configuración > Privacidad > Cámara';
    } else if (noDevice) {
      pista = '— no aparece ninguna cámara en el sistema. Conéctala y reintenta';
    } else if (estadoTrasCerebro && !estadoTrasCerebro.ultimo_error) {
      // El Cerebro tiene la cámara sana y el navegador no: lo que se lleva el
      // dispositivo por delante es el servicio de cámara de Windows. Decirlo
      // claro evita que el señor piense que la cámara está rota.
      pista = '— el Cerebro SÍ tiene la cámara abierta por '
        + (estadoTrasCerebro.backend || 'DirectShow')
        + ', pero el navegador no consigue cogerla: en este equipo el servicio '
        + 'de cámara de Windows no la comparte. Cierra Zoom/Meet/Teams y reintenta.';
    } else {
      pista = '— la cámara se abre pero no entrega imagen (negro o corrupto). '
        + 'Cierra las apps que la usen y reintenta; si persiste, es la cámara '
        + 'o su cable, no JARVIS. Pregúntame por el estado y lo compruebo.';
    }
    throw new Error('[camara] sin acceso: ' + msg + pista);
  }

  function loadMediaPipe() {
    if (resolveVisionGlobal('HandLandmarker') && resolveVisionGlobal('FilesetResolver')) {
      return Promise.resolve();
    }
    if (_mpPromise) return _mpPromise;
    _mpPromise = (async () => {
      let lastErr = null;
      for (const url of MP_ESM) {
        try {
          const ns = await import(/* webpackIgnore: true */ url);
          if (ns && ns.HandLandmarker && ns.FilesetResolver) {
            _mpNs = ns;
            return;
          }
          lastErr = new Error('bundle sin HandLandmarker');
        } catch (e) {
          lastErr = e;
        }
      }
      _mpPromise = null;
      throw new Error('[mediapipe] CDN inaccesible: ' +
        (lastErr && lastErr.message ? lastErr.message : lastErr));
    })();
    return _mpPromise;
  }

  class HandTracker {
    constructor(video, overlay, callbacks) {
      this.video = video;
      this.overlay = overlay;
      this.callbacks = callbacks;
      this.landmarker = null;
      this.stream = null;
      this.rafId = 0;
      this.running = false;
      this.lastVideoTime = -1;
      this.handStates = new Map();
      this.prevMode = 'idle';
      this.prevSpinGrab = null;
      this.prevZoomDist = null;
      this.lastStatus = { hands: 0, mode: 'idle' };
    }

    // Espera a que el vídeo de verdad tenga dimensiones. Antes solo se esperaba
    // a 'play()', que en un vídeo escondido se queda colgado y hacía que el
    // arranque pareciera un fallo de cámara. Y si no llegan dimensiones, ahora
    // se avisa: antes se continuaba en silencio y el panel se quedaba en
    // 'Apagada' para siempre sin decir por qué.
    async _esperarVideoListo(ms = 20000) {
      try { await this.video.play(); } catch { /* puede pedir gesto: se ignora */ }
      for (let i = 0; i < ms / 100; i += 1) {
        if (this.video.videoWidth > 0 && this.video.videoHeight > 0) return true;
        await _wait(100);
      }
      return false;
    }

    async start() {
      this._stopStream();
      try {
        this.stream = await conPlazo(acquireCameraStream(this.video), 90000, 'camara');
      } catch (e) {
        stopBrainStream();
        throw e;
      }
      this.video.srcObject = this.stream;
      if (!(await this._esperarVideoListo())) {
        throw new Error('[camara] el vídeo llega sin fotogramas (imagen vacía)');
      }

      const FilesetResolver = resolveVisionGlobal('FilesetResolver');
      const HandLandmarker = resolveVisionGlobal('HandLandmarker');
      if (!FilesetResolver || !HandLandmarker) {
        throw new Error('[mediapipe] librería no cargada (CDN)');
      }

      let fileset;
      try {
        fileset = await conPlazo(
          FilesetResolver.forVisionTasks(WASM_CDN), 30000, 'wasm');
      } catch (e) {
        throw new Error('[modelo] WASM no descargado: ' + (e && e.message ? e.message : e));
      }
      const options = {
        baseOptions: { modelAssetPath: MODEL_URL, delegate: 'GPU' },
        runningMode: 'VIDEO',
        numHands: 2,
        // Antes en 0,6: con la mano lejos o con poca luz no la encontraba y
        // el señor veía 'no detecta los dedos'. Bajado a 0,5 para que entre
        // antes; el recorte de ruido lo hace luego el suavizado del puntero.
        minHandDetectionConfidence: 0.5,
        minHandPresenceConfidence: 0.5,
        minTrackingConfidence: 0.5,
      };
      try {
        this.landmarker = await conPlazo(
          HandLandmarker.createFromOptions(fileset, options), 30000, 'modelo');
      } catch {
        try {
          this.landmarker = await conPlazo(
            HandLandmarker.createFromOptions(fileset, {
              ...options,
              baseOptions: { ...options.baseOptions, delegate: 'CPU' },
            }), 30000, 'modelo');
        } catch (e2) {
          throw new Error('[modelo] ni GPU ni CPU: ' + (e2 && e2.message ? e2.message : e2));
        }
      }

      this.running = true;
      this.loop();
    }

    _stopStream() {
      // Corta también el sondeo de fotos: si sigue vivo, el cerebro no suelta la
      // cámara y la app se queda sin ella para siempre.
      stopBrainStream();
      if (this.stream) {
        try { this.stream.getTracks().forEach((t) => t.stop()); } catch { /* */ }
      }
      this.stream = null;
      try { if (this.video) this.video.srcObject = null; } catch { /* */ }
    }

    stop() {
      this.running = false;
      cancelAnimationFrame(this.rafId);
      if (this.landmarker) { try { this.landmarker.close(); } catch { /* */ } }
      this.landmarker = null;
      this._stopStream();
      this.handStates.clear();
      this.prevMode = 'idle';
      this.prevSpinGrab = null;
      this.prevZoomDist = null;
      const ctx = this.overlay.getContext('2d');
      if (ctx) ctx.clearRect(0, 0, this.overlay.width, this.overlay.height);
      this.emitStatus({ hands: 0, mode: 'idle' });
    }

    loop = () => {
      if (!this.running) return;
      this.rafId = requestAnimationFrame(this.loop);
      if (!this.landmarker || this.video.readyState < 2) return;
      if (this.video.currentTime === this.lastVideoTime) return;
      this.lastVideoTime = this.video.currentTime;
      const result = this.landmarker.detectForVideo(this.video, performance.now());
      const labels = result.handedness.map((h) => (h[0] && h[0].categoryName) || '?');
      this.processHands(result.landmarks, labels);
      this.drawOverlay(result.landmarks);
    };

    processHands(landmarks, labels) {
      const pinchedGrabs = [];
      const punteros = [];
      const vistos = new Set();
      const ahora = performance.now();

      landmarks.forEach((lm, i) => {
        const label = labels[i];
        vistos.add(label);

        const handScale = dist2d(lm[WRIST], lm[MIDDLE_MCP]);
        if (handScale < 1e-6) return;
        const pinchRatio = dist2d(lm[THUMB_TIP], lm[INDEX_TIP]) / handScale;

        // El puntero es la yema del índice (es lo natural para señalar).
        // La cámara va espejada, así que también se invierte la X.
        const crudo = { x: 1 - lm[INDEX_TIP].x, y: lm[INDEX_TIP].y };
        // El «agarre» sigue siendo el centro pulgar-índice, que es donde se
        // nota el pellizco.
        const agarre = {
          x: 1 - (lm[THUMB_TIP].x + lm[INDEX_TIP].x) / 2,
          y: (lm[THUMB_TIP].y + lm[INDEX_TIP].y) / 2,
        };

        let state = this.handStates.get(label);
        if (!state) {
          state = { pinching: false, pinchStart: 0, grab: agarre, point: crudo, toques: [] };
          this.handStates.set(label, state);
        }

        // --- PELLIZCO: entrar y salir ---
        const estaba = state.pinching;
        if (estaba && pinchRatio > PINCH_OFF) state.pinching = false;
        else if (!estaba && pinchRatio < PINCH_ON) state.pinching = true;

        if (state.pinching && !estaba) {
          state.pinchStart = ahora;                 // empieza el pellizco
        } else if (!state.pinching && estaba) {
          // Se acaba de soltar: si ha sido rápido, es un toque.
          if (ahora - (state.pinchStart || ahora) <= TOQUE_MS) {
            state.toques.push(ahora);
            if (state.toques.length > 3) state.toques.shift();
            const previa = state.toques[state.toques.length - 2];
            if (previa !== undefined && ahora - previa <= DOBLE_MS) {
              state.toques.length = 0;
              this.callbacks.onClick && this.callbacks.onClick({
                x: state.point.x, y: state.point.y,
                etiqueta: 'doble pellizco',
              });
            }
          }
          state.pinchStart = 0;
        }

        state.grab = {
          x: state.grab.x + (agarre.x - state.grab.x) * SMOOTHING,
          y: state.grab.y + (agarre.y - state.grab.y) * SMOOTHING,
        };
        state.point = {
          x: state.point.x + (crudo.x - state.point.x) * PUNTERO_SUAVE,
          y: state.point.y + (crudo.y - state.point.y) * PUNTERO_SUAVE,
        };
        state.hay = true;

        if (state.pinching) pinchedGrabs.push(state.grab);
        punteros.push({ label, x: state.point.x, y: state.point.y,
                        pinching: state.pinching, scale: handScale });
      });

      for (const [k, s] of this.handStates) {
        if (!vistos.has(k)) { this.handStates.delete(k); continue; }
        s.hay = false;
      }

      // El puntero que manda es el de la mano principal; si hay dos, la que
      // esté más cerca de la cámara manda, que es la que está delante.
      let principal = null;
      for (const p of punteros) {
        if (!principal || p.scale > principal.scale) principal = p;
      }
      if (this.callbacks.onPointer) {
        this.callbacks.onPointer(principal, punteros.length);
      }

      const mode = pinchedGrabs.length >= 2 ? 'zoom' : pinchedGrabs.length === 1 ? 'spin' : 'point';

      if (mode !== this.prevMode) {
        this.prevSpinGrab = null;
        this.prevZoomDist = null;
        this.prevMode = mode;
      }

      if (mode === 'spin') {
        const grab = pinchedGrabs[0];
        if (this.prevSpinGrab) {
          const dx = grab.x - this.prevSpinGrab.x;
          const dy = grab.y - this.prevSpinGrab.y;
          if (Math.abs(dx) > 1e-4 || Math.abs(dy) > 1e-4) {
            this.callbacks.onRotate(dx * ROTATE_SPEED, dy * ROTATE_SPEED);
          }
        }
        this.prevSpinGrab = grab;
      } else if (mode === 'zoom') {
        const d = Math.hypot(
          pinchedGrabs[0].x - pinchedGrabs[1].x,
          pinchedGrabs[0].y - pinchedGrabs[1].y
        );
        if (this.prevZoomDist && d > 1e-4) {
          // Convención de cámara (como OrbitControls dolly): factor = prev/dist.
          // Abrir las manos (d crece) → factor < 1 → la cámara se acerca.
          // Los consumidores que escalan TAMAÑOS (fuente de lector, tarjetas)
          // deben usar 1/factor para que abrir agrande.
          const factor = Math.min(1.18, Math.max(0.85, this.prevZoomDist / d));
          this.callbacks.onZoom(factor);
        }
        this.prevZoomDist = d;
      }

      this.emitStatus({ hands: landmarks.length, mode });
    }

    emitStatus(status) {
      if (
        status.hands !== this.lastStatus.hands ||
        status.mode !== this.lastStatus.mode
      ) {
        this.lastStatus = status;
        this.callbacks.onStatus(status);
      }
    }

    drawOverlay(landmarks) {
      const ctx = this.overlay.getContext('2d');
      if (!ctx) return;
      const { width, height } = this.overlay;
      ctx.clearRect(0, 0, width, height);

      for (const lm of landmarks) {
        const thumb = lm[THUMB_TIP];
        const index = lm[INDEX_TIP];
        const tx = (1 - thumb.x) * width;
        const ty = thumb.y * height;
        const ix = (1 - index.x) * width;
        const iy = index.y * height;

        const handScale = dist2d(lm[WRIST], lm[MIDDLE_MCP]);
        const pinched = handScale > 1e-6 && dist2d(thumb, index) / handScale < PINCH_ON;

        ctx.strokeStyle = pinched ? '#8fc9e0' : 'rgba(93,166,204,0.5)';
        ctx.lineWidth = pinched ? 2 : 1;
        ctx.beginPath();
        ctx.moveTo(tx, ty);
        ctx.lineTo(ix, iy);
        ctx.stroke();

        ctx.fillStyle = pinched ? '#8fc9e0' : 'rgba(93,166,204,0.7)';
        const pts = [[tx, ty], [ix, iy]];
        for (const [x, y] of pts) {
          ctx.beginPath();
          ctx.arc(x, y, pinched ? 5 : 3, 0, Math.PI * 2);
          ctx.fill();
        }
        // Se dibuja la mano entera, no solo la línea pulgar-índice: así el
        // señor ve dónde está la yema del índice y a dónde apunta de verdad.
        ctx.strokeStyle = pinched ? 'rgba(143,201,224,0.75)' : 'rgba(93,166,204,0.3)';
        ctx.lineWidth = pinched ? 2 : 1;
        ctx.beginPath();
        for (const [a, b] of HAND_BONES) {
          if (!lm[a] || !lm[b]) continue;
          ctx.moveTo((1 - lm[a].x) * width, lm[a].y * height);
          ctx.lineTo((1 - lm[b].x) * width, lm[b].y * height);
        }
        ctx.stroke();
        // La yema del índice se marca aparte: es el puntero.
        ctx.fillStyle = pinched ? '#ffd479' : 'rgba(185,230,245,0.95)';
        ctx.beginPath();
        ctx.arc(ix, iy, pinched ? 7 : 5, 0, Math.PI * 2);
        ctx.fill();
      }
    }
  }

  window.JarvisGestures = { HandTracker, loadMediaPipe, acquireCameraStream };
})();