/* Renderer: orbe Ultron (con post-processing) + línea de voz + WS con el brain. */
import { createOrbScene } from './ultron-orb.js';

const root = document.getElementById('orbRoot');
const gesturesBtn = document.getElementById('gesturesBtn');
const cameraPanel = document.getElementById('cameraPanel');
const cameraStatus = document.getElementById('cameraStatus');
const camVideo = document.getElementById('camVideo');
const camOverlay = document.getElementById('camOverlay');
const hudError = document.getElementById('hudError');
const actionPanel = document.getElementById('actionPanel');
const actionText = document.getElementById('actionText');
const gauge = document.getElementById('voiceLine');
const gctx = gauge.getContext('2d');

let orb = null;

/* La escena WebGL (con bloom) se crea justo DESPUÉS de pintar el primer frame
   para no bloquear la ventana durante el arranque. */
function boot() {
  if (orb) return;
  const canvas = document.getElementById('orbRoot');
  orb = createOrbScene(canvas, { lite: true });
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

const MODE_LABEL = { idle: 'STANDBY', spin: 'SPIN', zoom: 'ZOOM' };
let startingGestures = false;

function showStatus(st) {
  cameraStatus.textContent = st.hands > 0
    ? st.hands + ' HAND' + (st.hands > 1 ? 'S' : '') + ' · ' + MODE_LABEL[st.mode]
    : 'SHOW HANDS';
}

async function startGestures() {
  if (startingGestures || tracker) return;
  startingGestures = true;
  gesturesBtn.textContent = 'INITIALIZING…';
  gesturesBtn.disabled = true;
  hudError.textContent = '';
  try {
    // HandTracker solicita y arranca la cámara antes de cargar MediaPipe,
    // para no confundir un fallo de CDN con un fallo de webcam.
    const Tracker = window.JarvisGestures.HandTracker;
    tracker = new Tracker(camVideo, camOverlay, {
      onRotate: (dt, dp) => orb && orb.rotateBy(dt * 1.2, dp * 1.2),
      onZoom: (f) => orb && orb.zoomBy(Math.max(0.90, Math.min(1.10, f))),
      onStatus: showStatus,
    });
    cameraPanel.classList.add('visible');
    await tracker.start();
    cameraPanel.classList.add('visible');
    gesturesBtn.textContent = 'GESTURES ON';
    gesturesBtn.setAttribute('aria-pressed', 'true');
  } catch (err) {
    const cameraReady = !!(tracker && camVideo.videoWidth > 0 && tracker.stream &&
      tracker.stream.getVideoTracks().some((track) => track.readyState === 'live'));
    if (!cameraReady && tracker) { try { tracker.stop(); } catch { /* */ } }
    if (!cameraReady) tracker = null;
    cameraPanel.classList.toggle('visible', cameraReady);
    gesturesBtn.textContent = cameraReady ? 'CAMERA ON' : 'GESTURES OFF';
    gesturesBtn.setAttribute('aria-pressed', String(cameraReady));
    const detail = String((err && err.message) || err || 'error').slice(0, 300);
    hudError.textContent =
      (cameraReady ? 'CAMERA ON · TRACKING ERROR: ' :
        (err && err.name === 'NotAllowedError' ? 'CAMERA ACCESS DENIED: ' : 'CAMERA/TRACKER ERROR: ')) + detail;
    hudError.title = detail;
    // Lo pega al cerebro para que OpenCode lo solucione solo.
    try {
      if (ws && ws.readyState === 1) {
        ws.send(JSON.stringify({ type: 'error_report', source: 'camera-tracking',
                                 error: detail, context: 'HUD renderer startGestures' }));
      }
    } catch { /* noop */ }
  } finally {
    startingGestures = false;
    gesturesBtn.disabled = false;
  }
}

function stopGestures() {
  if (tracker) tracker.stop();
  tracker = null;
  cameraPanel.classList.remove('visible');
  gesturesBtn.textContent = 'GESTURES OFF';
  gesturesBtn.setAttribute('aria-pressed', 'false');
  showStatus({ hands: 0, mode: 'idle' });
}

function toggleGestures() {
  if (tracker) stopGestures();
  else startGestures();
}
gesturesBtn.addEventListener('click', toggleGestures);

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

// ---- panel de pasos (centro-derecha) / acción en curso ----
const stepThink = document.getElementById('stepThink');
const stepAct = document.getElementById('stepAct');
const stepSpeak = document.getElementById('stepSpeak');

const STEPS = {
  think: stepThink,
  act: stepAct,
  speak: stepSpeak,
};

function setSteps(activeKey, doneKey, text) {
  const keys = ['think', 'act', 'speak'];
  keys.forEach((k) => {
    const el = STEPS[k];
    if (!el) return;
    el.classList.toggle('active', k === activeKey);
    const isDone = doneKey === 'all' ||
      (!!doneKey && (k === doneKey || keys.indexOf(k) < keys.indexOf(doneKey)));
    el.classList.toggle('done', isDone);
  });
  if (text !== undefined) {
    actionText.textContent = text || '';
  }
  if (activeKey && activeKey !== 'all') {
    actionPanel.classList.add('visible');
  }
}

let actionTimer = 0;
let runningAction = '';

function hideActionPanel(delay) {
  clearTimeout(actionTimer);
  actionTimer = setTimeout(() => {
    actionPanel.classList.remove('visible');
    keysForHide();
  }, delay);
  function keysForHide() {
    ['think', 'act', 'speak'].forEach((k) => {
      const el = STEPS[k];
      if (el) el.classList.remove('active', 'done');
    });
    actionText.textContent = '—';
  }
}

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

function connect() {
  clearTimeout(rt);
  try {
    ws = new WebSocket('ws://127.0.0.1:8000/ws');
  } catch {
    rt = setTimeout(connect, 3000);
    return;
  }
  ws.onmessage = (e) => {
    let d;
    try { d = JSON.parse(e.data); } catch { return; }
    if (d.type === 'conversation_state') {
      if (d.state === 'listening') { lineState = 'listening'; setOrbMode('active'); hideActionPanel(0); }
      else if (d.state === 'thinking') { lineState = 'processing'; setOrbMode('active'); setSteps('think', null, 'Analizando tu petición…'); }
      else if (d.state === 'speaking') { lineState = 'speaking'; fakeUntil = 0; setOrbMode('speaking'); setSteps('speak', 'think', 'Diciéndote la respuesta…'); }
      else {
        fakeUntil = 0;
        lineState = 'standby';
        setOrbMode('idle');
        if (!runningAction) hideActionPanel(0);
      }
    } else if (d.type === 'speech_start') {
      fakeUntil = 0;
      lineState = 'speaking';
      setOrbMode('speaking');
      setSteps('speak', 'think', 'Diciéndote la respuesta…');
    } else if (d.type === 'speech_end') {
      fakeUntil = 0;
      lineState = 'standby';
      setOrbMode('idle');
    } else if (d.type === 'audio_level') {
      lev = d.level || 0;
      if (orb) orb.setLevel(lev);
    } else if (d.type === 'assistant_reply') {
      // Respuesta por texto: la línea "lee en voz alta" durante unos segundos
      // para interpretar a JARVIS aunque no haya audio.
      if (lineState === 'standby') lineState = 'processing';
      setSteps('speak', 'think', d.text ? 'Listo.' : '');
      const chars = (d.text || '').length;
      fakeUntil = performance.now() + Math.min(9000, Math.max(2000, 80 + chars * 45));
    } else if (d.type === 'action_status') {
      const title = d.label || d.title || d.tool || '';
      const done = d.state === 'done' || d.state === 'failed' || d.state === 'cancelled';
      if (title && !done) {
        runningAction = title;
        setSteps('act', 'think', title);
        clearTimeout(actionTimer);
      } else if (done) {
        runningAction = '';
        setSteps('all', 'all', title);
        clearTimeout(actionTimer);
        actionTimer = setTimeout(() => hideActionPanel(0), 500);
      }
    }
  };
  ws.onclose = () => {
    rt = setTimeout(connect, 3000);
  };
}
connect();