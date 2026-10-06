/* Mini JARVIS en el overlay Win+J: orbe compacto de bajo coste (modo lite),
   conectado al brain. Se crea al cargar la página. */
import { createOrbScene } from '../ultron-orb.js';

const stage = document.getElementById('orbStage');
const orb = createOrbScene(stage, { transparent: true, lite: true });
orb.setCompact(true);
orb.setMode('idle');
orb.setLevel(0);

// Conexión independiente al WS del brain (puerto 8000)
let ws = null;
let reconnectTimer = null;

function connect() {
  clearTimeout(reconnectTimer);
  try { ws = new WebSocket('ws://127.0.0.1:8000/ws'); } catch { reconnectTimer = setTimeout(connect, 5000); return; }
  ws.onmessage = (e) => {
    let d;
    try { d = JSON.parse(e.data); } catch { return; }
    if (d.type === 'conversation_state') {
      if (d.state === 'speaking') orb.setMode('speaking');
      else orb.setMode((d.state === 'listening' || d.state === 'thinking') ? 'active' : 'idle');
    } else if (d.type === 'speech_start') {
      orb.setMode('speaking');
    } else if (d.type === 'speech_end') {
      orb.setMode('idle');
    } else if (d.type === 'audio_level') {
      orb.setLevel(d.level || 0);
    }
  };
  ws.onclose = () => { reconnectTimer = setTimeout(connect, 5000); };
  ws.onerror = () => {};
}
connect();
