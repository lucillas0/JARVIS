/* JARVIS en el overlay Win+J: el MISMO orbe que el de la ventana principal.
   Se renderiza con el mismo pipeline (bloom + aberración cromática) dentro de
   su propio marco, y se construye capa a capa con la misma secuencia. */
import { createOrbScene } from './ultron-orb.js';

const stage = document.getElementById('orbStage');
const orb = createOrbScene(stage, { transparent: false, lite: true });
orb.setCompact(true);
orb.setMode('idle');
orb.setLevel(0);

// El orbe nace oculto y lo construye app.js al mostrar la pizarra, para que
// la secuencia no se pierda antes de abrirse. Aquí solo se queda disponible.
// (Antes había un temporizador que lo construía solo a los 3,5 s de cargar la
// página: como la ventana se reutiliza oculta, el orbe quedaba ya montado y
// al abrir se veía completo un instante antes de la animación.)
window.__jarvisOrb = orb;

// Conexión independiente al WS del brain (puerto 8000)
let ws = null;
let reconnectTimer = null;


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

function connect() {
  clearTimeout(reconnectTimer);
  try { ws = new WebSocket(_jarvisWsUrl()); } catch { reconnectTimer = setTimeout(connect, 5000); return; }
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
