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

// Coucou pinta a Mochi, que es su mascota. Las de JARVIS son un extra: por
// defecto se queda Mochi, que es lo de Coucou, y las nuestras solo se montan
// si se activan. Asi no se pierde nada y se puede volver cuando se quiera.
let activado = false;

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
  // Coucou coloca el lienzo de la mascota en top:-40, para que sobresalga por
  // encima del panel. Aqui la ventana arranca en y=0 y eso se ve cortado, así
  // que la mascota grande se dibuja MAS PEQUEÑA para que quepa entera dentro
  // del notch. Solo se toca la grande: los avatares de las pastillas, de 15 px,
  // se quedan como estaban.
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const r = hueco.getBoundingClientRect();
  const a = anclaEl.getBoundingClientRect();
  if (!r.width || !r.height) return;

  // El dibujo ocupa unos 160 unidades de alto sobre un lado de 200, y las
  // manos y el globo se salen un poco mas. Con esta altura maxima, lo dibujado
  // no llega a salirse por arriba.
  const altoUtil = Math.max(60, Math.min(window.innerHeight - 10, 176));
  const lado = Math.max(28, Math.min(r.width * 1.7, altoUtil / 0.82));

  const px = Math.round(lado * dpr);
  if (lienzo.width !== px || lienzo.height !== px) {
    lienzo.width = px;
    lienzo.height = px;
  }
  lienzo.style.width = lado + 'px';
  lienzo.style.height = lado + 'px';

  // Centrada en el hueco de Mochi, pero nunca por encima de la ventana.
  const cx = r.left - a.left + r.width / 2;
  const cy = r.top - a.top + r.height / 2;
  const arriba = Math.max(2, cy - lado / 2);
  lienzo.style.left = (cx - lado / 2) + 'px';
  lienzo.style.top = arriba + 'px';
  grande = lado > 150;
}

export function montaMascotas(ancla) {
  if (!activado) return;
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
    clearTimeout(relojCompila);
    // La mascota nueva nace en reposo, pero si ya habia un estado en marcha
    // hay que ponerselo: si no se queda en reposo mientras trabaja.
    if (canonicoActual && canonicoActual !== 'reposo') {
      motores[id].pon(estadoPara(id, canonicoActual));
      if (canonicoActual === 'programando') programaCompilacion();
    } else {
      programaFloritura();
    }
  }
  motorActivo = motores[id];
}

export function muestra(pastilla) {
  if (!activado) return;
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
let relojCompila = null;
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

// ── Compilar ─────────────────────────────────────────────────────────────────
// En los HTML compilar es un estado de TRABAJO, no un adorno: sale el portatil
// con la barra de progreso. Por eso no esta en TIENE (que son cosas de reposo)
// sino que se cuela de vez en cuando MIENTRAS el agente trabaja, y a los pocos
// segundos vuelve solo al estado de trabajo. Asi cada mascota tiene su
// equivalencia de "compilar" sin inventarle ningun estado nuevo.
function programaCompilacion() {
  clearTimeout(relojCompila);
  if (!motorActivo || !tieneCompilando(activoId)) return;
  relojCompila = setTimeout(() => {
    if (!motorActivo || canonicoActual !== 'programando') return;
    // Si mientras esperabamos le tocaba otra cosa (permiso, error), se respeta.
    if (motorActivo.estado() !== estadoPara(activoId, 'programando')) return;
    motorActivo.pon('compilando');
    relojCompila = setTimeout(() => {
      if (motorActivo && canonicoActual === 'programando') motorActivo.pon(estadoPara(activoId, 'programando'));
      programaCompilacion();
    }, 3200 + Math.random() * 2600);
  }, 5000 + Math.random() * 7000);
}

const tieneCompilando = (id) => !!(motores[id] && motores[id].estados && motores[id].estados.compilando);

/** Estado que llega de Coucou. */
export function sincroniza(estadoCoucou) {
  if (!activado) return;
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
  clearTimeout(relojCompila);
  lienzo.style.display = 'none';
  apartaAtras();
}

// ── El interruptor ───────────────────────────────────────────────────────────
//   __mascotas.activa(true)   -> las mascotas de JARVIS tapan a Mochi
//   __mascotas.activa(false)  -> vuelve Mochi, el de Coucou
let focoGuardado = 'jarvis';
export function fijaFoco(id) { focoGuardado = id; }

export function activa(on) {
  if (activado === !!on) return activado;
  activado = !!on;
  clearTimeout(temporizador);
  clearTimeout(relojCompila);
  if (activado) {
    apartaMochi();
    if (lienzo) lienzo.style.display = '';
    muestra(mascotFor(focoGuardado));
    sincroniza('idle');
    programaFloritura();
  } else {
    apartaAtras();
    if (lienzo) lienzo.style.display = 'none';
    // Los avatares de las pastillas se quedan: son adorno de 15 px y no
    // estorban a Mochi.
  }
  return activado;
}

// ── Avatares de las pastillas ────────────────────────────────────────────────
// En segundo plano: cada pastilla lleva su mascota en el circleito. Coucou usa
// ahi un cuadrado de color, asi que el avatar se dibuja al lado en vez de
// encima, para no perder el color de la pastilla.

const DE_ETIQUETA = {
  jarvis: 'jarvi',
  freebuff: 'buffi', 'free buff': 'buffi',
  'vs code': 'vee', vscode: 'vee',
  claude: 'vee', 'claude code': 'vee', 'claude desktop': 'vee',
  opencode: 'opi',
  codex: 'opi',
};

export function mascotaDeEtiqueta(texto) {
  const bruto = String(texto || '').trim().toLowerCase();
  if (!bruto) return null;
  // La pastilla puede traer solo el nombre, o el nombre y debajo el subtitulo
  // o el ultimo paso del ticker. Se prueba el texto entero y, si no cuadra,
  // solo la primera linea, que es el nombre. Asi "VS Code\nClaude Code" cae en
  // Vee, pero un paso que mencione "claude" no cambia la mascota por su cuenta.
  const directo = DE_ETIQUETA[bruto];
  if (directo) return directo;
  const primera = bruto.split('\n')[0].trim();
  return DE_ETIQUETA[primera] || null;
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
  // Los avatares de las pastillas van SIEMPRE, independently de que la mascota
  // grande este encendida o no: son 15 px al lado del circleito de color y no
  // cambian a Mochi ni su tamano, que es lo que hay que dejar como estaba.
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
    const lado = 26;
    const dpr = Math.min(window.devicePixelRatio || 1, 2);
    const lienzoA = document.createElement('canvas');
    lienzoA.className = 'jarvis-avatar';
    // Evita que el canvas haga scroll/reflow en layouts raros.
    lienzoA.style.overflow = 'visible';
    lienzoA.width = Math.round(lado * dpr);
    lienzoA.height = Math.round(lado * dpr);
    // Posicionado en absoluto para no ensanchar el pill (la etiqueta está
    // centrada con posicion:absolute). Esto lo deja "en segundo plano".
    Object.assign(lienzoA.style, {
      width: lado + 'px',
      height: lado + 'px',
      pointerEvents: 'none',
      position: 'absolute',
      left: '37px', // ajustado: .pill .mini (24px) + margin-left (7px) + hueco (3px)
      top: '50%',
      transform: 'translateY(-50%)',
      zIndex: '4',
      // Asegura que el canvas no quede recortado por el contenedor
      // (si algo cambia en CSS/overflow entre vistas).
    });
    // En vez de insertarlo como hijo normal (que puede interferir con el layout
    // del label centrado), lo dejamos como último hijo para asegurar que no
    // empuja el ancho ni cambia el orden de apilado.
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
  activa, fijaFoco,
  lasNuestrasEstan: () => activado,
};
