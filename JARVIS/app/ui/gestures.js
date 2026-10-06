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

  const PINCH_ON = 0.30;
  const PINCH_OFF = 0.43;
  const ROTATE_SPEED = 4.2;
  const SMOOTHING = 0.32;
  const DETECTION_INTERVAL_MS = 1000 / 30;

  function dist2d(a, b, aspect = 1) {
    // MediaPipe normaliza x e y por dimensiones distintas; escalar x al ratio
    // real evita que un frame 16:9 distorsione el umbral de pinch.
    return Math.hypot((a.x - b.x) * aspect, a.y - b.y);
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

  // Abre la cámara probando CADA dispositivo (hay equipos con la cámara
  // duplicada: una entrada OK y una fantasma; el dispositivo por defecto
  // puede ser el muerto -> "Could not start video source").
  async function acquireCameraStream() {
    let devs = [];
    try {
      devs = await navigator.mediaDevices.enumerateDevices();
    } catch { /* getUserMedia decidirá */ }
    const ids = devs.filter((d) => d.kind === 'videoinput')
      .map((c) => c.deviceId).filter(Boolean);
    if (!ids.length) ids.push(undefined); // dispositivo por defecto
    let lastErr = null;
    for (const id of ids) {
      const sel = id ? { deviceId: { exact: id } } : {};
      // Preferir HD para landmarks más precisos, pero degradar a VGA para
      // cámaras antiguas o dispositivos virtuales con formatos limitados.
      const attempts = [
        { video: { ...sel, width: { ideal: 1280 }, height: { ideal: 720 }, frameRate: { ideal: 30, max: 30 } }, audio: false },
        { video: { ...sel, width: { ideal: 640 }, height: { ideal: 480 }, frameRate: { ideal: 30, max: 30 } }, audio: false },
        { video: id ? sel : true, audio: false },
      ];
      for (const cons of attempts) {
        try {
          const stream = await navigator.mediaDevices.getUserMedia(cons);
          const track = stream.getVideoTracks()[0];
          if (track) {
            try { await track.applyConstraints({ frameRate: { ideal: 30, max: 30 } }); } catch { /* opcional */ }
          }
          return stream;
        } catch (e) {
          lastErr = e;
        }
      }
    }
    const msg = lastErr && lastErr.message ? lastErr.message : lastErr;
    const denied = lastErr && lastErr.name === 'NotAllowedError';
    throw new Error('[camara] sin acceso: ' + msg +
      (denied
        ? ' — permite la cámara para JARVIS en Configuración > Privacidad > Cámara'
        : ' — si es USB externa, desenchúfala y conéctala a OTRO puerto USB (trasero mejor); si no, cierra apps que usen la cámara y reintenta'));
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
      this.lastDetectAt = 0;
      this.handStates = new Map();
      this.prevMode = 'idle';
      this.prevSpinGrab = null;
      this.prevZoomDist = null;
      this.lastStatus = { hands: 0, mode: 'idle' };
    }

    async start() {
      this._stopStream();
      this.stream = await acquireCameraStream();
      this.video.srcObject = this.stream;
      await this.video.play();
      if (this.video.readyState < 2) {
        await new Promise((resolve, reject) => {
          const cleanup = () => {
            clearTimeout(timer);
            this.video.removeEventListener('loadeddata', onData);
            this.video.removeEventListener('error', onError);
          };
          const onData = () => { cleanup(); resolve(); };
          const onError = () => { cleanup(); reject(new Error('[camara] error al iniciar el vídeo')); };
          const timer = setTimeout(() => { cleanup(); reject(new Error('[camara] no llegan fotogramas de vídeo')); }, 8000);
          this.video.addEventListener('loadeddata', onData, { once: true });
          this.video.addEventListener('error', onError, { once: true });
        });
      }
      this.lastVideoTime = -1;
      this.lastDetectAt = 0;

      // Abre la cámara primero: si la descarga remota de MediaPipe falla,
      // las vistas aún pueden mostrar la imagen y distinguir ese error.
      await loadMediaPipe();
      const FilesetResolver = resolveVisionGlobal('FilesetResolver');
      const HandLandmarker = resolveVisionGlobal('HandLandmarker');
      if (!FilesetResolver || !HandLandmarker) {
        throw new Error('[mediapipe] librería no cargada (CDN)');
      }

      let fileset;
      try {
        fileset = await FilesetResolver.forVisionTasks(WASM_CDN);
      } catch (e) {
        throw new Error('[modelo] WASM no descargado: ' + (e && e.message ? e.message : e));
      }
      const options = {
        baseOptions: { modelAssetPath: MODEL_URL, delegate: 'GPU' },
        runningMode: 'VIDEO',
        numHands: 2,
        minHandDetectionConfidence: 0.52,
        minHandPresenceConfidence: 0.52,
        minTrackingConfidence: 0.55,
      };
      try {
        this.landmarker = await HandLandmarker.createFromOptions(fileset, options);
      } catch {
        try {
          this.landmarker = await HandLandmarker.createFromOptions(fileset, {
            ...options,
            baseOptions: { ...options.baseOptions, delegate: 'CPU' },
          });
        } catch (e2) {
          throw new Error('[modelo] ni GPU ni CPU: ' + (e2 && e2.message ? e2.message : e2));
        }
      }

      this.running = true;
      this.loop();
    }

    _stopStream() {
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
      if (this.callbacks.onPointer) this.callbacks.onPointer(0.5, 0.5, 'idle');
    }

    loop = () => {
      if (!this.running) return;
      this.rafId = requestAnimationFrame(this.loop);
      if (!this.landmarker || this.video.readyState < 2) return;
      const now = performance.now();
      if (this.video.currentTime === this.lastVideoTime || now - this.lastDetectAt < DETECTION_INTERVAL_MS) return;
      this.lastVideoTime = this.video.currentTime;
      this.lastDetectAt = now;
      try {
        const result = this.landmarker.detectForVideo(this.video, now);
        const labels = result.handedness.map((h) => (h[0] && h[0].categoryName) || '?');
        this.processHands(result.landmarks, labels);
        this.drawOverlay(result.landmarks);
      } catch (e) {
        console.warn('[gestures] fallo de detección:', e);
      }
    };

    processHands(landmarks, labels) {
      const pinchedGrabs = [];
      const seen = new Set();

      landmarks.forEach((lm, i) => {
        const label = labels[i];
        seen.add(label);

        const frameAspect = this.video.videoWidth / Math.max(1, this.video.videoHeight);
        const handScale = dist2d(lm[WRIST], lm[MIDDLE_MCP], frameAspect);
        if (handScale < 1e-6) return;
        const pinchRatio = dist2d(lm[THUMB_TIP], lm[INDEX_TIP], frameAspect) / handScale;

        const raw = {
          // Coordenadas normalizadas: espejo coherente con el vídeo y no ligadas
          // a un tamaño de pantalla específico.
          x: 1 - (lm[INDEX_TIP].x),
          y: lm[INDEX_TIP].y,
        };

        let state = this.handStates.get(label);
        if (!state) {
          state = { pinching: false, grab: raw, pinchFrames: 0, openFrames: 0 };
          this.handStates.set(label, state);
        }

        // Histeresis temporal: exige estabilidad breve y reduce falsos
        // pellizcos cuando se cruzan los dedos o hay baja iluminación.
        if (pinchRatio < PINCH_ON) {
          state.pinchFrames++;
          state.openFrames = 0;
          if (state.pinchFrames >= 2) state.pinching = true;
        } else if (pinchRatio > PINCH_OFF) {
          state.openFrames++;
          state.pinchFrames = 0;
          if (state.openFrames >= 2) state.pinching = false;
        } else {
          state.pinchFrames = 0;
          state.openFrames = 0;
        }

        state.grab = {
          x: state.grab.x + (raw.x - state.grab.x) * SMOOTHING,
          y: state.grab.y + (raw.y - state.grab.y) * SMOOTHING,
        };

        if (state.pinching) pinchedGrabs.push(state.grab);
      });

      for (const key of this.handStates.keys()) {
        if (!seen.has(key)) this.handStates.delete(key);
      }

      const mode = pinchedGrabs.length >= 2 ? 'zoom' : pinchedGrabs.length === 1 ? 'spin' : 'idle';
      const pointer = pinchedGrabs.length ? pinchedGrabs[0] :
        (landmarks.length && labels.length ? this.handStates.get(labels[0])?.grab : null);
      if (pointer && this.callbacks.onPointer) this.callbacks.onPointer(pointer.x, pointer.y, mode);

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
        // El canvas se espeja por CSS igual que el vídeo: dibujar en
        // coordenadas de cámara naturales mantiene ambos landmark overlays alineados.
        const tx = thumb.x * width;
        const ty = thumb.y * height;
        const ix = index.x * width;
        const iy = index.y * height;

        const frameAspect = this.video.videoWidth / Math.max(1, this.video.videoHeight);
        const handScale = dist2d(lm[WRIST], lm[MIDDLE_MCP], frameAspect);
        const pinched = handScale > 1e-6 &&
          dist2d(thumb, index, frameAspect) / handScale < PINCH_ON;

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
      }
    }
  }

  window.JarvisGestures = { HandTracker, loadMediaPipe, acquireCameraStream };
})();