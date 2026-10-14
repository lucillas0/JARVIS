/* Renderer: orbe Ultron (con post-processing) + línea de voz + WS con el brain. */
import { createOrbScene } from './ultron-orb.js';

const root = document.getElementById('orbRoot');
const gesturesBtn = document.getElementById('gesturesBtn');
const cameraPanel = document.getElementById('cameraPanel');
const cameraStatus = document.getElementById('cameraStatus');
const camVideo = document.getElementById('camVideo');
const camOverlay = document.getElementById('camOverlay');
const hudError = document.getElementById('hudError');
const gauge = document.getElementById('voiceLine');
const gctx = gauge.getContext('2d');

let orb = null;

/* La escena WebGL (con bloom) se crea justo DESPUÉS de pintar el primer frame
   para no bloquear la ventana durante el arranque. */
function boot() {
  if (orb) return;
  const canvas = document.getElementById('orbRoot');
  orb = createOrbScene(canvas, { lite: true });
  // Los ajustes de /config mandan: si el señor tiene animaciones activas, el
  // orbe se construye entero. Solo se salta si las apagó (anims.enabled).
  try {
    fetch('http://127.0.0.1:8000/api/config')
      .then((r) => r.json())
      .then((j) => {
        const a = (j.config && j.config.anims) || {};
        if (a && a.enabled === false) { orb.skipIntro(); return; }
        orb.playIntro({ speed: 1.1 });
      })
      .catch(() => { orb.playIntro({ speed: 1.1 }); });
  } catch {
    orb.playIntro({ speed: 1.1 });
  }
}

if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', () => requestAnimationFrame(boot));
} else {
  requestAnimationFrame(boot);
}

// OrbitControls ya gestiona drag (orbit) y scroll (zoom) sobre el canvas.

// ---- botones ----
document.getElementById('zoomInBtn').addEventListener('click', () => orb && orb.zoomIn());
document.getElementById('zoomOutBtn').addEventListener('click', () => orb && orb.zoomOut());
document.getElementById('resetBtn').addEventListener('click', () => orb && orb.resetView());

// ---- gestos con cámara ----
let tracker = null;
const handCursor = document.getElementById('handCursor');
const handHintEl = document.getElementById('handHint');

// 'point' es el modo tableta: la yema del índice mueve el puntero por la
// pantalla sin pellizcar. Antes solo había 'spin' y 'zoom', y por eso en esta
// ventana no se veía nada que se moviera con la mano.
const MODE_LABEL = {
  idle: 'STANDBY', spin: 'PINCH · SPIN', zoom: 'ZOOM', point: 'POINTER',
};

function handHint(texto) {
  if (!handHintEl) return;
  handHintEl.textContent = texto;
  handHintEl.classList.add('show');
  clearTimeout(handHintEl._t);
  handHintEl._t = setTimeout(() => handHintEl.classList.remove('show'), 2200);
}

function showStatus(st) {
  cameraStatus.classList.toggle('ok', st.hands > 0);
  cameraStatus.classList.toggle('pinch', st.mode === 'spin' || st.mode === 'zoom');
  if (st.hands > 0) {
    cameraStatus.textContent = st.hands + ' HAND' + (st.hands > 1 ? 'S' : '')
      + ' · ' + (MODE_LABEL[st.mode] || st.mode.toUpperCase());
  } else {
    // Si estaba siguiendo a la mano y la ha perdido, se avisa: antes se
    // quedaba en blanco y no se sabía si el problema era la cámara o la luz.
    cameraStatus.textContent = 'NO HAND';
  }
}

async function startGestures() {
  gesturesBtn.textContent = 'ABRIENDO CAMARA';
  gesturesBtn.disabled = true;
  hudError.textContent = '';
  try {
    await window.JarvisGestures.loadMediaPipe();
    const Tracker = window.JarvisGestures.HandTracker;
    tracker = new Tracker(camVideo, camOverlay, {
      // Modo tableta en la ventana normal: el dedo mueve el puntero.
      onPointer: (p) => {
        if (!p) { if (handCursor) handCursor.hidden = true; return; }
        if (!handCursor) return;
        handCursor.hidden = false;
        handCursor.style.left = (p.x * window.innerWidth) + 'px';
        handCursor.style.top = (p.y * window.innerHeight) + 'px';
        handCursor.classList.toggle('pinching', !!p.pinching);
      },
      // Doble pellizco rápido = clic, como en una tableta.
      onClick: () => {
        if (handCursor) {
          handCursor.classList.add('clicking');
          setTimeout(() => handCursor.classList.remove('clicking'), 220);
        }
        const el = document.elementFromPoint(
          handCursor ? handCursor.offsetLeft : 0,
          handCursor ? handCursor.offsetTop : 0);
        const boton = el && el.closest('button');
        if (boton && typeof boton.click === 'function') {
          boton.click();
          handHint('PULSADO: ' + (boton.textContent || '').trim().slice(0, 24));
        } else {
          handHint('Ahí no hay nada que pulsar, señor.');
        }
      },
      onRotate: (dt, dp) => orb && orb.rotateBy(dt, dp),
      onZoom: (f) => orb && orb.zoomBy(f),
      onStatus: showStatus,
    });
    // El panel se enseña ANTES de arrancar el tracker. Estaba oculto con
    // display:none mientras se pedía la cámara, y Chromium no reproduce un
    // vídeo que no está en pantalla: por eso el vídeo se quedaba negro y el
    // arranque fallaba. Ahora se ve y por eso llega imagen.
    cameraPanel.classList.add('visible');
    await tracker.start();
    // Si arrancó bien, el aviso de error anterior se borra. Antes se quedaba
    // puesto 'SIN CAMARA' para siempre aunque la cámara ya funcionase, que es
    // lo que más lío ha creado.
    hudError.textContent = '';
    gesturesBtn.textContent = 'GESTOS ON';
    gesturesBtn.setAttribute('aria-pressed', 'true');
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ type: 'gestures_result', ok: true,
                               state: 'on' }));
    }
  } catch (err) {
    if (tracker) { try { tracker.stop(); } catch { /* */ } }
    tracker = null;
    cameraPanel.classList.remove('visible');
    gesturesBtn.textContent = 'GESTOS OFF';
    gesturesBtn.setAttribute('aria-pressed', 'false');
    const detail = String((err && err.message) || err || 'error').slice(0, 300);
    // Antes solo decía 'TRACKING INIT FAILED' en inglés y sin explicar qué
    // pasaba, así que no había forma de saber si era la cámara, el modelo o la
    // red. Ahora dice exactamente dónde se rompió.
    let motibote = 'GESTOS: NO ARRANCA';
    if (err && err.name === 'NotAllowedError') {
      motibote = 'GESTOS: CAMARA PERMITIDA?';
    } else if (/camara/.test(detail)) {
      motibote = 'GESTOS: SIN CAMARA';
    } else if (/wasm|modelo|mediapipe/i.test(detail)) {
      motibote = 'GESTOS: MODELO NO DESCARGADO';
    } else if (/tard/.test(detail)) {
      motibote = 'GESTOS: SE HA COLGADO';
    }
    hudError.textContent = motibote;
    hudError.title = detail;
    // El detalle se deja preparado para verlo si hace falta, pero no se pinta
    // entero encima del orbe: solo el motivo corto.
    // Lo pega al cerebro para que OpenCode lo solucione solo.
    try {
      if (ws && ws.readyState === 1) {
        ws.send(JSON.stringify({ type: 'error_report', source: 'camera-tracking',
                                 error: detail, context: 'HUD renderer startGestures' }));
        ws.send(JSON.stringify({ type: 'gestures_result', ok: false,
                                 state: 'off', error: detail }));
      }
    } catch { /* noop */ }
  } finally {
    gesturesBtn.disabled = false;
  }
}

function stopGestures() {
  if (tracker) tracker.stop();
  tracker = null;
  cameraPanel.classList.remove('visible');
  gesturesBtn.textContent = 'GESTOS OFF';
  gesturesBtn.setAttribute('aria-pressed', 'false');
  showStatus({ hands: 0, mode: 'idle' });
}

function toggleGestures() {
  if (tracker) stopGestures();
  else startGestures();
}
gesturesBtn.addEventListener('click', toggleGestures);

// ---- micro: silenciar/habilitar + mini transcripción ----
const micBtn = document.getElementById('micBtn');
const micTranscript = document.getElementById('micTranscript');
let micMuted = false;

function micSay(text) {
  if (!micTranscript || micMuted) return;
  micTranscript.textContent = String(text || '').slice(0, 160);
}

function setMicUI(muted, noMic) {
  micMuted = !!muted;
  if (micBtn) {
    micBtn.textContent = muted ? 'MIC OFF' : 'MIC ON';
    micBtn.setAttribute('aria-pressed', String(!muted));
    micBtn.title = muted ? 'Micro silenciado (clic para habilitar)' : 'Micro activo (clic para silenciar)';
  }
  if (micTranscript) {
    micTranscript.classList.toggle('muted', muted);
    if (muted) micTranscript.textContent = 'Micrófono silenciado';
    else if (noMic) micTranscript.textContent = 'Sin micrófono en este equipo';
    else if (micTranscript.textContent.indexOf('silenciado') >= 0) micTranscript.textContent = 'Micrófono activo';
  }
}

if (micBtn) {
  micBtn.addEventListener('click', () => {
    const next = !micMuted;
    setMicUI(next, false);
    try {
      if (ws && ws.readyState === 1) ws.send(JSON.stringify({ type: 'mic_mute', muted: next }));
    } catch { /* noop */ }
  });
}

// ---- teclas ----
window.addEventListener('keydown', (e) => {
  if (!orb) return;
  if (e.key === '+' || e.key === '=') orb.zoomIn();
  else if (e.key === '-' || e.key === '_') orb.zoomOut();
  else if (e.key === 'r' || e.key === 'R') orb.resetView();
  else if (e.key === 'g' || e.key === 'G') toggleGestures();
});

// ---- línea de voz / ecualizador ----
function sizeLine() {
  gauge.width = Math.min(560, Math.max(240, root.clientWidth * 0.5));
  gauge.height = 64;
}
sizeLine();
window.addEventListener('resize', sizeLine);

let lineState = 'standby';
let amp = 0.05;
let lev = 0;
let levSmooth = 0;
let fakeUntil = 0; // fin de la "voz simulada" al responder por texto

const N_BARS = 28;

function loopLine() {
  requestAnimationFrame(loopLine);
  const t = performance.now() / 1000;
  const now = performance.now();
  // "habla" si JARVIS está reproduciendo audio real o si hay una respuesta
  // por texto en curso (simulando que la lee en voz alta)
  const eqActive = lineState === 'speaking' || now < fakeUntil;

  /* En modo habla la onda es un ecualizador que vibra con el nivel de audio
     real que está reproduciendo JARVIS (o simulando ritmo si responde texto). */
  if (lineState === 'speaking') {
    amp += ((0.35 + lev * 0.65) - amp) * 0.2;
    levSmooth += ((lev || 0) - levSmooth) * 0.25;
  } else if (eqActive) {
    amp += (0.3 - amp) * 0.12;
    levSmooth *= 0.85;
  } else if (lineState === 'listening') {
    amp += (0.14 - amp) * 0.12;
    levSmooth *= 0.85;
  } else if (lineState === 'processing') {
    amp += (0.1 - amp) * 0.12;
    levSmooth *= 0.85;
  } else {
    amp += (0.05 - amp) * 0.08;
    levSmooth *= 0.85;
  }

  const w = gauge.width;
  const h = gauge.height;
  const mid = h / 2;
  const ctx = gctx;
  ctx.clearRect(0, 0, w, h);

  if (eqActive) {
    // ---- ecualizador de voz: barras que bailan con las palabras ----
    const barW = w / N_BARS;
    const seed = Math.max(levSmooth, amp * 0.9); // nivel real + latido propio
    for (let i = 0; i < N_BARS; i++) {
      // envoltura tipo espectro: más energía en el centro, decrece hacia los lados
      const env = 1 - Math.pow(Math.abs(i - N_BARS / 2 + 0.5) / (N_BARS / 2), 1.7);
      const pulse = Math.sin(t * 9 - i * 0.55) * 0.5 + 0.5;
      const warp = 0.55 + 0.45 * Math.sin(t * 23 + i * 1.7);
      const barH = Math.max(2, (0.25 + seed * 0.75) * env * (0.55 + 0.45 * pulse) * warp * h * 0.55);
      const x = i * barW;
      const yTop = mid - barH / 2;
      const grad = ctx.createLinearGradient(0, yTop, 0, yTop + barH);
      grad.addColorStop(0, 'rgba(143,201,224,0.95)');
      grad.addColorStop(1, 'rgba(93,166,204,0.35)');
      ctx.fillStyle = grad;
      ctx.shadowBlur = 10;
      ctx.shadowColor = 'rgba(93,166,204,0.85)';
      ctx.fillRect(x + barW * 0.18, yTop, barW * 0.64, barH);
    }
    ctx.shadowBlur = 0;
    ctx.fillStyle = 'rgba(93,166,204,0.16)';
    ctx.fillRect(0, mid - 1, w, 2);
    // "boca": una onda fina bajo las barras que marca el volumen
    ctx.strokeStyle = 'rgba(143,201,224,0.55)';
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let x = 0; x < w; x++) {
      const y = mid + 8 + Math.sin(t * 12 + x * 0.04) * 3 * seed + (Math.random() - 0.5) * 4 * seed;
      if (x === 0) ctx.moveTo(x, y);
      else ctx.lineTo(x, y);
    }
    ctx.stroke();
    return;
  }

  // ---- resto de estados: línea calmada ----
  ctx.beginPath();
  for (let x = 0; x < w; x++) {
    const p = x / w;
    const y = mid
      + Math.sin(t * 10 + p * Math.PI * 2.2) * mid * 0.5 * amp * 0.7
      + Math.sin(t * 17 + p * Math.PI * 5.1) * mid * 0.3 * amp
      + (Math.random() - 0.5) * mid * 0.35 * amp;
    if (x === 0) ctx.moveTo(x, y);
    else ctx.lineTo(x, y);
  }
  ctx.strokeStyle = lineState === 'standby' ? 'rgba(93,166,204,0.25)' : 'rgba(93,166,204,0.95)';
  ctx.lineWidth = 2;
  ctx.lineJoin = 'round';
  ctx.shadowBlur = lineState === 'standby' ? 0 : 12;
  ctx.shadowColor = 'rgba(93,166,204,0.9)';
  ctx.stroke();
}
loopLine();

// ---- panel de pasos: retirado (el árbol de actividad lo sustituye) ----
function setSteps() {}
function hideActionPanel() {}
let actionTimer = 0;
let runningAction = '';

// ---- árbol de actividad: qué está haciendo JARVIS ----
const ttRoot = document.getElementById('thinkTree');
const ttTitle = document.getElementById('ttTitle');
const ttList = document.getElementById('ttList');
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

function ttAdd(text, state) {
  if (!ttList) return;
  ttLastBeat = Date.now();
  const prev = ttSteps[ttSteps.length - 1];
  if (prev && prev.text === text) {
    prev.state = state || prev.state;
    ttRender();
    return;
  }
  ttSteps.push({ text: String(text || '').slice(0, 70), state: state || 'now' });
  if (ttSteps.length > 5) ttSteps.shift();
  ttRender();
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

function ttClear(keepIdle) {
  ttSteps = [];
  if (ttList) ttList.textContent = '';
  if (keepIdle !== false) ttSet('EN REPOSO', null);
}

// Mismo resguardo que en la pizarra: si el rótulo es de los que duran poco y
// pasan segundos sin noticias, vuelve solo a "EN REPOSO". Si no, se quedaba
// diciendo "RESPONDIENDO" con el orbe parado y sin hacer nada.
const TT_TRANSIENT = ['RESPONDIENDO', 'PENSANDO', 'ESCUCHANDO'];
setInterval(() => {
  if (healBusy || runningAction) return;
  if (!TT_TRANSIENT.includes(ttCurrent)) return;
  if (Date.now() - ttLastBeat > 6000) ttClear();
}, 700);

let healBusy = false;

// ---- websocket con el brain ----
let ws = null;
let rt = null;

function reportHudError(source, err) {
  // El orbe/HUD también se autocorrige: pega el error al cerebro por WS
  // y el reparador (OpenCode) lo soluciona solo.
  try {
    const detail = String((err && err.message) || err || 'error').slice(0, 300);
    if (ws && ws.readyState === 1) {
      ws.send(JSON.stringify({ type: 'error_report', source, error: detail, context: 'HUD orbe' }));
    }
  } catch { /* noop */ }
}

window.addEventListener('error', (e) => {
  reportHudError('orb', (e && e.message) || e);
});
window.addEventListener('unhandledrejection', (e) => {
  reportHudError('orb', (e && e.reason && (e.reason.message || e.reason)) || e);
});

function setOrbMode(m) {
  if (orb) orb.setMode(m);
}

// El brain ya no acepta un WebSocket sin token (brain/auth.py). El proceso
// principal lo lee del fichero de datos y lo pasa por el preload; se pide de
// forma asincrona y se abre la conexion cuando llega, con un tope de espera
// para que un brain lento no deje el HUD colgado sin conexion.
let _netToken = '';
let _netListo = false;

// En Electron el token lo pasa el proceso principal por el preload. En el
// navegador NO hay preload, asi que el brain lo inyecta en el HTML de /ui como
// window.JARVIS_TOKEN_URL (ver _inyecta_token en brain/main.py).
//
// Este fichero era el UNO de los seis clientes que solo miraba el preload. Por
// eso en un navegador (Docker, NAS, modo LITE) abria el WebSocket sin token,
// el servidor lo rechazaba con 403 y la interfaz se quedaba con "SIN CONEXION
// CON EL CEREBRO" para siempre: reintentaba cada 3 s y nunca pasaba, porque el
// token no aparecia por ningun lado. Los otros tres si tienen este respaldo.
function tokenDesdeLaPagina() {
  return (typeof window !== 'undefined' && window.JARVIS_TOKEN_URL) || '';
}

async function conectar() {
  _netToken = tokenDesdeLaPagina();
  if (!_netToken && window.jarvisNet) {
    try { _netToken = (await window.jarvisNet.query()) || ''; }
    catch { _netToken = ''; }
  }
  _netListo = true;
  abrirSocket();
}
function abrirSocket() {
  try {
    if (!_netToken) {
      // Sin token no hay nada que reintentar: volver a intentarlo en 3 s daria
      // el mismo 403. Se dice una vez y se deja.
      console.error('[JARVIS] Sin token del cerebro: revisa que /ui lo inyecte.');
    }
    ws = new WebSocket('ws://127.0.0.1:8000/ws' + _netToken);
  } catch {
    rt = setTimeout(_netListo ? abrirSocket : conectar, 3000);
    return;
  }
  ws.onmessage = (e) => {
    let d;
    try { d = JSON.parse(e.data); } catch { return; }
    if (d.type === 'conversation_state') {
      if (d.state === 'listening') { lineState = 'listening'; setOrbMode('active'); hideActionPanel(0); micSay('Escuchando'); ttSet('ESCUCHANDO', 'think'); }
      else if (d.state === 'thinking') { lineState = 'processing'; setOrbMode('active'); setSteps('think', null, 'Analizando su petición…'); micSay('Procesando'); ttClear(false); ttSet('PENSANDO', 'think'); ttAdd('analizando su petición', 'now'); }
      else if (d.state === 'speaking') { lineState = 'speaking'; fakeUntil = 0; setOrbMode('speaking'); setSteps('speak', 'think', 'Diciéndole la respuesta…'); ttSet('RESPONDIENDO', 'speak'); ttAdd('respondiendo', 'now'); }
      else {
        fakeUntil = 0;
        lineState = 'standby';
        setOrbMode('idle');
        if (!runningAction && !healBusy) { hideActionPanel(0); ttClear(); }
      }
    } else if (d.type === 'speech_start') {
      fakeUntil = 0;
      lineState = 'speaking';
      setOrbMode('speaking');
      setSteps('speak', 'think', 'Diciéndole la respuesta…');
      ttSet('RESPONDIENDO', 'speak');
      ttAdd('respondiendo', 'now');
    } else if (d.type === 'speech_end') {
      fakeUntil = 0;
      lineState = 'standby';
      setOrbMode('idle');
      if (!runningAction) ttClear();
    } else if (d.type === 'audio_level') {
      lev = d.level || 0;
      if (orb) orb.setLevel(lev);
    } else if (d.type === 'voice_result') {
      // Transcripción final: esto es lo que JARVIS recibe.
      if (d.text) { micSay('«' + String(d.text).slice(0, 120) + '» recibido'); ttClear(false); ttSet('PENSANDO', 'think'); ttAdd('«' + String(d.text).slice(0, 60) + '»', 'now'); }
      else if (d.error === 'no_heard') micSay('No le he oído, señor.');
    } else if (d.type === 'mic_state') {      setMicUI(!!d.muted, d.has_mic === false);
    } else if (d.type === 'selfheal_update') {
      // Autocorrección visible en el árbol. Mientras repara, el árbol NO
      // vuelve a "EN REPOSO" aunque el cerebro termine su turno.
      const head = d.stage === 'fixed' ? 'ERROR SOLUCIONADO'
        : d.stage === 'needs_user' ? 'NECESITA SU AYUDA'
        : d.stage === 'fixing' ? 'REPARANDO' : 'ERROR DETECTADO';
      healBusy = (d.stage === 'fixing' || d.stage === 'found');
      ttClear(false);
      ttSet(head, d.stage === 'fixed' ? null : 'busy');
      if (d.source) ttAdd('origen: ' + d.source, 'now');
      if (d.error) ttAdd(d.error, d.stage === 'fixed' ? 'done' : 'now');
      if (d.note) ttAdd(d.note, d.stage === 'fixed' ? 'done' : 'now');
    } else if (d.type === 'task_step') {
      const lab = String(d.label || '').slice(0, 70);
      if (d.first) { ttClear(false); ttSet('INVESTIGANDO', 'busy'); }
      else if (!d.done) ttSet('INVESTIGANDO', 'busy');
      if (lab) ttAdd(lab, d.done ? 'done' : 'now');
      if (d.done) { setTimeout(() => { if (!healBusy && !runningAction) ttClear(); }, 12000); }
    } else if (d.type === 'user_heard') {
      const t = String(d.text || '').slice(0, 120);
      if (t) {
        micSay('«' + t + '» recibido');
        ttClear(false);
        ttSet('PENSANDO', 'think');
        ttAdd('«' + t.slice(0, 60) + '»', 'now');
      }
    } else if (d.type === 'assistant_reply') {
      // Respuesta por texto: la línea "lee en voz alta" durante unos segundos
      // para interpretar a JARVIS aunque no haya audio.
      if (lineState === 'standby') lineState = 'processing';
      setSteps('speak', 'think', d.text ? 'Respuesta lista.' : '');
      const chars = (d.text || '').length;
      fakeUntil = performance.now() + Math.min(9000, Math.max(2000, 80 + chars * 45));
    } else if (d.type === 'gestures') {
    // El cerebro puede encender o apagar el tracker con la voz.
    if (d.state === 'on') { if (!tracker) startGestures(); }
    else { if (tracker) stopGestures(); }
  } else if (d.type === 'action_status') {      const title = d.label || d.title || d.tool || '';
      const done = d.state === 'done' || d.state === 'failed' || d.state === 'cancelled';
      if (title && !done) {
        runningAction = title;
        setSteps('act', 'think', title);
        clearTimeout(actionTimer);
        ttClear(false);
        ttSet('ACTUANDO', 'busy');
        ttAdd(title, 'now');
      } else if (done) {
        runningAction = '';
        setSteps('all', 'all', title);
        clearTimeout(actionTimer);
        if (title) ttAdd(title, d.state === 'done' ? 'done' : 'fail');
        if (lineState === 'standby') ttClear();
        actionTimer = setTimeout(() => hideActionPanel(0), 500);
      }
    }
  };
  ws.onclose = () => {
    ttClear(false);
    ttSet('SIN CONEXIÓN CON EL CEREBRO', 'busy');
    rt = setTimeout(_netListo ? abrirSocket : conectar, 3000);
  };
  ws.onopen = () => {
    // Al reconectar se limpia el aviso. Antes no había onopen y el
    // "SIN CONEXIÓN" se quedaba pegado aunque todo volviera a funcionar:
    // por eso el orbe decía siempre que no había conexión.
    ttClear();
  };
  ws.onerror = () => { /* el onclose ya reintenta */ };
}
conectar();