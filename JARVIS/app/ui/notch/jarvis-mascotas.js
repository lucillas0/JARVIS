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

export function mascotaActiva() {
  return activoId;
}

let lienzo = null;
let ctx = null;
let motorActivo = null;
let activoId = null;
let pedido = null;
let grande = true;
let hueco = null; // el canvas de Mochi: es el sitio donde va la mascota
let anclaEl = null; // su contenedor, para medir en coordenadas relativas
const mirada = { x: 0, y: 0 };

function apartaMochi() {
  // Mochi tambien se pinta en canvas. Mientras manden las mascotas de JARVIS
  // se aparta, que si no se ven las dos superpuestas.
  if (!hueco) return;
  hueco.style.visibility = 'hidden';
}

function apartaAtras() {
  if (hueco) hueco.style.visibility = '';
}

function medida() {
  if (!lienzo || !hueco || !anclaEl) return;
  // La mascota va ENCIMA del hueco de Mochi. Se dibuja en un lienzo CUADRADO
  // porque en los HTML el lienzo es cuadrado y la mascota esta centrada en el
  // origen; si el lienzo no es cuadrado se estira y deja de parecerse.
  // El lado es el mayor de los dos y se centra sobre el hueco.
  //
  // Las coordenadas van RELATIVAS al ancla y en absoluto a proposito: la isla
  // se centra con un transform, y con position:fixed eso haria que las
  // coordenadas se midieran contra el ancestro transformado y la mascota
  // acabara desplazada.
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const r = hueco.getBoundingClientRect();
  const a = anclaEl.getBoundingClientRect();
  if (!r.width || !r.height) return;
  const lado = Math.max(r.width, r.height);
  const px = Math.round(lado * dpr);
  if (lienzo.width !== px || lienzo.height !== px) {
    lienzo.width = px;
    lienzo.height = px;
  }
  lienzo.style.width = lado + 'px';
  lienzo.style.height = lado + 'px';
  lienzo.style.left = (r.left - a.left + (r.width - lado) / 2) + 'px';
  lienzo.style.top = (r.top - a.top + (r.height - lado) / 2) + 'px';
  grande = lado > 190;
}

export function montaMascotas(ancla) {
  if (lienzo || !ancla) return;
  anclaEl = ancla;
  hueco = document.getElementById('bot-canvas');
  lienzo = document.createElement('canvas');
  lienzo.id = 'jarvis-mascotas';
  Object.assign(lienzo.style, {
    position: 'absolute',
    pointerEvents: 'none',
    zIndex: '5',
  });
  ancla.appendChild(lienzo);
  ctx = lienzo.getContext('2d');
  apartaMochi();
  // Coucou cambia el hueco al abrir y cerrar la isla; se sigue con observer
  // y ademas una medida por frame, que es lo que mantiene la mascota en su
  // sitio cuando el alto del panel cambia.
  try { new ResizeObserver(medida).observe(ancla); } catch (e) { /* sin observer */ }
  if (hueco) { try { new ResizeObserver(medida).observe(hueco); } catch (e) { /* sin observer */ } }
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
  medida();
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

// ── Avatares de las pastillas ────────────────────────────────────────────────
// En segundo plano: cada pastilla lleva su mascota en el circleito. Coucou usa
// ahi un cuadrado de color, asi que el avatar se dibuja al lado en vez de
// encima, para no perder el color de la pastilla.

const DE_ETIQUETA = {
  jarvis: 'jarvi', 'jarvis': 'jarvi',
  freebuff: 'buffi',
  'vs code': 'vee', vscode: 'vee',
  opencode: 'opi',
  codex: 'opi',
};

export function mascotaDeEtiqueta(texto) {
  const t = String(texto || '').trim().toLowerCase();
  return DE_ETIQUETA[t] || null;
}

// Clave por elemento y no por mascota: Codex y OpenCode comparten la misma
// (Opi), y guardando por id la segunda pastilla pisaba a la primera y se
// quedaba en negro.
const avatares = new Map(); // elemento canvas -> { lienzo, ctx, motor }

let ultimaAvatar = 0;
function dibujaAvatar(ms) {
  // Van a paso lento: son 15 pixeles y no se nota, pero cuatro lienzos a 60 fps
  // serian tirar CPU para nada. Se mira el reloj, no el resto de ms % 100,
  // que con los tiempos de rAF cae unas veces y otras no.
  if (ms - ultimaAvatar >= 90) {
    ultimaAvatar = ms;
    for (const [, a] of avatares) {
      const st = a.motor.avanza(ms);
      st.grande = false;
      st.parpadeo = 0;
      const lado = a.lienzo.width;
      a.ctx.save();
      a.motor.dibuja(a.ctx, lado, lado, st, { x: 0, y: 0 });
      a.ctx.restore();
    }
  }
  requestAnimationFrame(dibujaAvatar);
}

let avataresArrancados = false;

export function montaAvatares() {
  if (!avataresArrancados) { avataresArrancados = true; requestAnimationFrame(dibujaAvatar); }
  const pastillas = document.querySelectorAll('#root .pill');
  for (const pill of pastillas) {
    if (pill.dataset.jarvisAvatar) continue;
    const etiqueta = (pill.innerText || '').trim();
    const id = mascotaDeEtiqueta(etiqueta);
    if (!id) continue;
    // Se cuelga de la pastilla y NO del squareito de color: si va dentro, el
    // lienzo agranda el squareito, el squareito agranda la pastilla y al medir
    // otra vez el avatar sale mas grande. Tamano fijo para que no se mueva.
    const lado = 15;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const lienzoA = document.createElement('canvas');
    lienzoA.width = Math.round(lado * dpr);
    lienzoA.height = Math.round(lado * dpr);
    Object.assign(lienzoA.style, {
      width: lado + 'px',
      height: lado + 'px',
      verticalAlign: 'middle',
      marginLeft: '3px',
      pointerEvents: 'none',
      flex: '0 0 auto',
    });
    pill.appendChild(lienzoA);
    pill.dataset.jarvisAvatar = id;
    const motor = creaMotor(MASCOTAS[id].def, MASCOTAS[id].estados, MASCOTAS[id].pintar);
    motor.pon('reposo');
    avatares.set(lienzoA, { lienzo: lienzoA, ctx: lienzoA.getContext('2d'), motor });
  }
}

// Enganche para probar y depurar desde la consola del renderer: sin esto no
// hay forma de forzar un estado ni cambiar de mascota a mano.
globalThis.__mascotas = {
  muestraPorId, sincroniza, estadoActual,
  motorDe: (id) => motores[id],
  activo: () => activoId,
  rect: () => (lienzo ? lienzo.getBoundingClientRect() : null),
};
