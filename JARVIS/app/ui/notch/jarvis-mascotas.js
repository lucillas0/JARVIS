// Registro y puesta en marcha de las mascotas de JARVIS dentro de la isla.
//
// Cada pastilla tiene su mascota. El estado de Coucou se traduce al de la
// mascota, y cuando la mascota esta en reposo entra por la puerta: de vez en
// cuando se pone a bailar, volar, disparar o hacer pesas. Esas cosas en el
// HTML eran estados sueltos; aqui son florituras, porque bailar o volar
// mientras el agente esta compilando no tiene ningun sentido.

import { creaMotor } from './jarvis-mascotas-nucleo.js';
import { MASCOTAS, DE_PASTILLA, deEstado, estadoPara, TIENE } from './jarvis-mascotas-datos.js';

const motores = {};
for (const id in MASCOTAS) {
  motores[id] = creaMotor(MASCOTAS[id].def, MASCOTAS[id].estados, MASCOTAS[id].pintar);
}

export function mascotFor(pastilla) {
  return DE_PASTILLA[pastilla] || 'jarvi';
}

let lienzo = null;
let ctx = null;
let motorActivo = null;
let activoId = null;
let pedido = null;
let grande = true;
const mirada = { x: 0, y: 0 };

function apartaMochi() {
  // Mochi tambien se pinta en canvas. Mientras manden las mascotas de JARVIS
  // se aparta, que si no se ven las dos superpuestas.
  for (const id of ['bot-canvas', 'greeting-canvas']) {
    const el = document.getElementById(id);
    if (el) el.style.visibility = 'hidden';
  }
}

function apartaAtras() {
  for (const id of ['bot-canvas', 'greeting-canvas']) {
    const el = document.getElementById(id);
    if (el) el.style.visibility = '';
  }
}

function medida() {
  if (!lienzo) return;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const caja = lienzo.parentElement.getBoundingClientRect();
  const lado = Math.max(80, Math.min(caja.width, caja.height) * 0.9);
  const px = Math.round(lado * dpr);
  // Redimensionar un lienzo lo deja en blanco. Si el tamano no ha cambiado
  // no se toca, o el observer puede borrar un frame en cuanto se dispara.
  if (lienzo.width === px && lienzo.height === px) { grande = lado > 190; return; }
  lienzo.width = px;
  lienzo.height = px;
  lienzo.style.width = lado + 'px';
  lienzo.style.height = lado + 'px';
  grande = lado > 190;
}

export function montaMascotas(ancla) {
  if (lienzo || !ancla) return;
  lienzo = document.createElement('canvas');
  lienzo.id = 'jarvis-mascotas';
  Object.assign(lienzo.style, {
    position: 'absolute',
    left: '50%',
    top: '50%',
    transform: 'translate(-50%,-50%)',
    pointerEvents: 'none',
    zIndex: '5',
  });
  ancla.appendChild(lienzo);
  ctx = lienzo.getContext('2d');
  apartaMochi();
  try { new ResizeObserver(medida).observe(ancla); } catch (e) { /* sin observer */ }
  medida();
  pide();
  requestAnimationFrame(bucle);
}

function pide() {
  const id = pedido || 'jarvi';
  if (!motores[id]) return;
  const cambiado = activoId !== id;
  if (cambiado) {
    activoId = id;
    motores[id].reinicia();
    clearTimeout(temporizador);
    // La mascota nueva nace en reposo, pero si ya habia un estado en marcha
    // hay que ponerselo: si no se queda en reposo mientras trabaja.
    if (canonicoActual && canonicoActual !== 'reposo') {
      motores[id].pon(estadoPara(id, canonicoActual));
    } else {
      programaFloritura();
    }
  }
  motorActivo = motores[id];
}

export function muestra(pastilla) {
  const id = mascotFor(pastilla);
  if (id === activoId && motorActivo) return;
  pedido = id;
  pide();
}

export function muestraPorId(id) {
  if (!motores[id] || id === activoId) return;
  pedido = id;
  pide();
}

let ultimo = 0;
let relojParpadeo = 0;
function bucle(ms) {
  requestAnimationFrame(bucle);
  if (!motorActivo || !ctx) return;
  if (!ultimo) ultimo = ms;
  // La isla no recibe raton de forma fiable, asi que la mirada va sola: es el
  // caso "nadie ha movido el raton" del HTML.
  mirada.x = Math.sin(ms / 1000 * 0.5) * 0.45;
  mirada.y = Math.sin(ms / 1000 * 0.37) * 0.3;
  const ahora = ms / 1000;
  if (ahora - relojParpadeo > 2 + Math.random() * 3) relojParpadeo = ahora;
  const st = motorActivo.avanza(ms);
  st.grande = grande;
  st.parpadeo = relojParpadeo;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const lado = lienzo.width / dpr;
  ctx.save();
  ctx.scale(dpr, dpr);
  motorActivo.dibuja(ctx, lado, lado, st, mirada);
  ctx.restore();
}

// ── Florituras de reposo ─────────────────────────────────────────────────────

let temporizador = null;
let canonicoActual = null;

const floriturasDe = (id) => (TIENE[id] || []).filter((n) => motores[id] && motores[id].estados && motores[id].estados[n]);

function programaFloritura() {
  clearTimeout(temporizador);
  temporizador = setTimeout(() => {
    if (!motorActivo || canonicoActual !== 'reposo') return;
    const lista = floriturasDe(activoId);
    if (!lista.length) { programaFloritura(); return; }
    const elegido = lista[Math.floor(Math.random() * lista.length)];
    motorActivo.pon(elegido);
    clearTimeout(temporizador);
    temporizador = setTimeout(() => {
      if (motorActivo) motorActivo.pon(estadoPara(activoId, 'reposo'));
      programaFloritura();
    }, 4800 + Math.random() * 2200);
  }, 7000 + Math.random() * 9000);
}

/** Estado que llega de Coucou. */
export function sincroniza(estadoCoucou) {
  const canonico = deEstado(estadoCoucou);
  if (canonico === canonicoActual) return;
  canonicoActual = canonico;
  if (!motorActivo) return;
  if (canonico === 'reposo') {
    motorActivo.pon(estadoPara(activoId, 'reposo'));
    programaFloritura();
  } else {
    // Hay trabajo: fuera adornos, y se cancela la que estuviera en curso.
    clearTimeout(temporizador);
    motorActivo.pon(estadoPara(activoId, canonico));
  }
}

export function estadoActual() {
  return motorActivo ? motorActivo.estado() : null;
}

export function apaga() {
  clearTimeout(temporizador);
  lienzo.style.display = 'none';
  apartaAtras();
}

// Enganche para probar y depurar desde la consola del renderer: sin esto no
// hay forma de forzar un estado ni cambiar de mascota a mano.
globalThis.__mascotas = { muestraPorId, sincroniza, estadoActual, motorDe: (id) => motores[id] };
