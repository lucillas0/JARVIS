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
let cuerpo = 73;   // el ancho que le queda a la mascota dentro de la caja
let centroY = 36;  // su centro vertical, que en Coucou no es el de la caja
let hueco = null; // el canvas de Mochi: es el sitio donde va la mascota
let anclaEl = null; // su contenedor, para medir en coordenadas relativas
const mirada = { x: 0, y: 0 };

// Coucou pinta a Mochi, que es su mascota. Las de JARVIS son un extra: por
// defecto se queda Mochi, que es lo de Coucou, y las nuestras solo se montan
// si se activan. Asi no se pierde nada y se puede volver cuando se quiera.
let activado = false;

function apartaMochi() {
  // Mochi y el saludo (donde Coucou le pone el gorro y las animations de
  // saludo) se pintan en canvas. Mientras manden las mascotas de JARVIS se
  // apartan los dos, que si no el gorro le sale encima de la nuestra.
  if (hueco) hueco.style.visibility = 'hidden';
  const saludo = document.getElementById('greeting-canvas');
  if (saludo) saludo.style.visibility = 'hidden';
}

function apartaAtras() {
  if (hueco) hueco.style.visibility = '';
  const saludo = document.getElementById('greeting-canvas');
  if (saludo) saludo.style.visibility = '';
}

function medida() {
  if (!lienzo || !hueco || !anclaEl) return;
  // La caja es LA MISMA que la de Mochi, tal cual la calcula Coucou: ancho por
  // alto, con el mismo left y el mismo top. Coucou la sube 40 px (BOT_OVERHANG)
  // y por eso parece cortada; no lo esta, porque el cuerpo no va centrado en la
  // caja sino al 79 % de ella, que es justo lo que cae dentro del panel. Se
  // respeta su sitio en vez de inventar uno.
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const r = hueco.getBoundingClientRect();
  const a = anclaEl.getBoundingClientRect();
  if (!r.width || !r.height) return;

  const ancho = r.width;
  const alto = r.height;
  const pxW = Math.round(ancho * dpr);
  const pxH = Math.round(alto * dpr);
  if (lienzo.width !== pxW || lienzo.height !== pxH) {
    lienzo.width = pxW;
    lienzo.height = pxH;
  }
  lienzo.style.width = ancho + 'px';
  lienzo.style.height = alto + 'px';
  lienzo.style.left = (r.left - a.left) + 'px';
  lienzo.style.top = (r.top - a.top) + 'px';

  // Coucou dibuja a Mochi con un margen de BOT_SIDE a cada lado, asi que el
  // cuerpo mide (ancho - 48), y lo baja con la mitad del voladizo mas un pelo
  // de Separacion. Copiado tal cual para que la mascota caiga en el mismo sitio.
  cuerpo = Math.max(6, ancho - 48);
  centroY = alto / 2 + 20 + cuerpo * 0.06;
  grande = cuerpo > 150;
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
  st.ref = cuerpo;
  st.cy = centroY;
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const W = lienzo.width / dpr;
  const H = lienzo.height / dpr;
  ctx.save();
  ctx.scale(dpr, dpr);
  motorActivo.dibuja(ctx, W, H, st, mirada);
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
    avataresVisibles(true);
  } else {
    apartaAtras();
    if (lienzo) lienzo.style.display = 'none';
    avataresVisibles(false);
  }
  return activado;
}

// ── Avatares de las pastillas ────────────────────────────────────────────────
// En segundo plano: cada pastilla lleva su mascota en el circleito. Coucou usa
// ahi un cuadrado de color mas el gorro del atuendo, asi que el avatar se
// dibuja ENCIMA con su mismo tamano y el canvas de Coucou se oculta: la skin
// sustituye al circleito entero en vez de acompanarlo.

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
      // A este tamano se acerca a la cara, que es lo que hace reconocible la
      // mascota: con el dibujo entero a 26 px es una mancha de color.
      st.zoom = 1.9;
      st.zoomY = 6;
      const lado = a.lienzo.width;
      a.ctx.save();
      a.motor.dibuja(a.ctx, lado, lado, st, { x: 0, y: 0 });
      a.ctx.restore();
    }
  }
  requestAnimationFrame(dibujaAvatar);
}

let avataresArrancados = false;

// Los avatares sustituyen al mini de Coucou, asi que van con el interruptor:
// apagadas las mascotas vuelven los circleitos y los gorros de Coucou.
function avataresVisibles(on) {
  for (const [lienzoA, a] of avatares) {
    if (!lienzoA.isConnected) { avatares.delete(lienzoA); continue; }
    lienzoA.style.display = on ? '' : 'none';
    const suyo = a.mini && a.mini.querySelector('canvas:not(.jarvis-avatar)');
    if (suyo) suyo.style.visibility = on ? 'hidden' : '';
  }
}

// Coloca el avatar ENCIMA del circleito de color, centrado en el. Al lado
// empujaba la etiqueta y se leia "Code" en vez de "Codex"; encima, lo sustituye
// y la pastilla queda igual de ancha que la de Coucou.
// Coucou deja el mini justo a su tamano, y a 13 px (la rejilla de la isla
// encogida) no se distingue una mascota de otra. Se agranda un poco mas que el
// hueco y se centra en el, pero nunca pasa del borde de la pastilla ni encima de
// la etiqueta: si no cabe entero se queda en el tamano de Coucou.
const AVATAR_K = 1.2;

// La caja de .lbl se estira a todo lo ancho de la pastilla, asi que medirla
// decía que el mini estaba encima del texto y el recorte salia siempre al
// tamano de Coucou. Lo que hay que medir es donde EMPIEZA el texto.
function dondeEmpiezaElTexto(el) {
  if (!el) return null;
  const r = document.createRange();
  let n = el.firstChild;
  while (n && n.nodeType !== 3) n = n.firstChild;
  if (!n) return null;
  r.selectNodeContents(n);
  const b = r.getBoundingClientRect();
  return b.width ? b : null;
}

// El lienzo cuelga siempre del mini, que ya es position:relative. Colgarlo de
// la pastilla descentraba un pixel por su borde, y de la rejilla no cabia.
function situaSobre(mini, lienzoA) {
  const mr = mini.getBoundingClientRect();
  if (!mr.width) return;
  const cx = mr.left + mr.width / 2;
  const cy = mr.top + mr.height / 2;
  let lado = mr.width * AVATAR_K;
  const pastilla = mini.closest('.pill');
  if (pastilla) {
    const pr = pastilla.getBoundingClientRect();
    const texto = dondeEmpiezaElTexto(pastilla.querySelector('.lbl'));
    const der = Math.min(pr.right, texto ? texto.left - 4 : pr.right);
    // El avatar va en absoluto, asi que al crecer no empuja la etiqueta: el
    // limite es el radio que hay a cada lado del centro del mini. Si se tomaba
    // ese radio como si fuera el ancho entero, nunca llegaba a crecer.
    lado = Math.min(lado, 2 * (der - cx), 2 * (cx - pr.left), pr.height - 1);
    lado = Math.max(lado, Math.min(mr.width, mr.height));
  }
  // En la rejilla no hay pastilla: el limite es el hueco, y las celdas estan a
  // 16 px, asi que 1,2 no llega a tocarse.
  Object.assign(lienzoA.style, {
    position: 'absolute',
    left: ((cx - lado / 2 - mr.left) + 'px'),
    top: ((cy - lado / 2 - mr.top) + 'px'),
    width: lado + 'px',
    height: lado + 'px',
    marginLeft: '0',
    // El CSS de Coucou centra su lienzo con translate(-50%,-50%) porque el suyo
    // es mas grande que el hueco. El nuestro va justa al hueco, asi que ese
    // desplazamiento lo dejaba medio pixel (o medio mini) fuera de sitio.
    transform: 'none',
    zIndex: '6',
    pointerEvents: 'none',
  });
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const px = Math.max(8, Math.round(lado * dpr));
  if (lienzoA.width !== px) { lienzoA.width = px; lienzoA.height = px; }
  // Coucou pinta ahi su squareito de color, y su lienzo es mas grande que el
  // hueco a proposito, para que el gorro y las particulas no se corten. Por eso
  // el gorro se veia por encima de nuestra skin: se oculta su lienzo entero.
  // El span se deja, que es el que recibe el clic.
  const suyo = mini.querySelector('canvas');
  if (suyo && suyo !== lienzoA) suyo.style.visibility = 'hidden';
}

function situaAvatar(pill) {
  const mini = pill.querySelector('.mini');
  if (!mini) return;
  const lienzoA = mini.querySelector('canvas.jarvis-avatar');
  if (lienzoA) situaSobre(mini, lienzoA);
}

function creaAvatar(mini, id, marca) {
  const dpr = Math.min(window.devicePixelRatio || 1, 2);
  const lienzoA = document.createElement('canvas');
  lienzoA.className = 'jarvis-avatar';
  lienzoA.width = 26 * dpr;
  lienzoA.height = 26 * dpr;
  mini.appendChild(lienzoA);
  if (marca) marca.dataset.jarvisAvatar = id;
  const motor = creaMotor(MASCOTAS[id].def, MASCOTAS[id].estados, MASCOTAS[id].pintar);
  motor.pon('reposo');
  avatares.set(lienzoA, { lienzo: lienzoA, ctx: lienzoA.getContext('2d'), motor, mini });
  situaSobre(mini, lienzoA);
}

export function montaAvatares() {
  if (!avataresArrancados) { avataresArrancados = true; requestAnimationFrame(dibujaAvatar); }
  const pastillas = document.querySelectorAll('#root .pill');
  const ids = [];
  for (const pill of pastillas) {
    if (pill.dataset.jarvisAvatar) { ids.push(pill.dataset.jarvisAvatar); situaAvatar(pill); continue; }
    const etiqueta = (pill.innerText || '').trim();
    const id = mascotaDeEtiqueta(etiqueta);
    if (!id) continue;
    const mini = pill.querySelector('.mini');
    if (!mini) continue;
    ids.push(id);
    // Se cuelga del mini y no de la pastilla: el mini es el hueco, y ahi dentro
    // el lienzo es justo del tamano del circleito, sin el desplazamiento que le
    // mete el borde de la pastilla.
    creaAvatar(mini, id, pill);
  }

  // Coucou, cuando la isla esta compacta, enseña 4 minibots con su gorro. Tambien
  // son un sustituto: se les pone la misma mascota que a su pastilla y al mismo
  // tamano. Coucou rehace la cuadricula cuando cambian los agentes, asi que esto
  // se vuelve a pasar en cada vuelta.
  const cuadricula = document.querySelector('#mini-grid');
  if (cuadricula) {
    const minis = cuadricula.querySelectorAll('.mini');
    for (let i = 0; i < minis.length; i++) {
      const mini = minis[i];
      if (!mini.querySelector('canvas.jarvis-avatar')) {
        creaAvatar(mini, ids[i] || 'jarvi');
      } else {
        situaSobre(mini, mini.querySelector('canvas.jarvis-avatar'));
      }
    }
  }

  // Coucou borra la cuadricula entera al cambiar de agentes, asi que sus lienzos
  // viejos se quedan en el mapa sin estar ya en la pagina.
  for (const [lienzoA] of avatares) {
    if (!lienzoA.isConnected) avatares.delete(lienzoA);
  }
  // Los nuevos nacen con lo que diga el interruptor, no siempre con el pelo.
  avataresVisibles(activado);
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
