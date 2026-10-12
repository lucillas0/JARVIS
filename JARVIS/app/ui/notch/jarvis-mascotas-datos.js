// Definicion de las cuatro mascotas: sus parametros, sus estados y su dibujo.
//
// Portado de los HTML de mascota (Vee, Buffi, Opi y Jarvi). Se conserva el
// aspecto y los tiempos de cada animacion; lo unico que cambia es que ahora
// se dibujan dentro de la isla de Coucou.

import { cl, G, mixC, sq, manitas, brazo, globo, ojos, mofletes } from './jarvis-mascotas-nucleo.js';

// ── Jarvi · Silver Centurion (la principal de JARVIS) ────────────────────────

const JARVI_DEF = {
  eh: 1, hp: 0, xx: 0, h: 195, bobA: 2, bobS: 2, sw: 0, swS: 2, hop: 0, ly: 0,
  shake: 0, sq: 0, dz: 0, an: 0, rr: 1, dw: .8, bp: 0, hs: 0, hn: 0, hm: 0,
  rX: 50, rY: 14, lX: -50, lY: 14, hwx: 0, hwy: 0, ch: 0, fl: 0, tl: 0, hud: 0, bs: 1,
};
const JARVI_S = {
  reposo: { l: 'en reposo', sy: 'dots', bs: 0, sw: .35, swS: 1.2, bobA: 2.5, bobS: 1.6 },
  saludo: { l: '¡hola, señor!', sy: 'dots', bs: 0, eh: 1.2, hn: 1, rX: 58, rY: -20, hwx: 7, hwy: 3, sw: .3, swS: 7, hop: .25 },
  pensando: { l: 'pensando…', sy: 'dots', eh: .8, h: 265, ly: -.8, hn: 1, rX: 14, rY: 20, hud: 1, dw: 3.5, tl: -.2, bobA: 3 },
  trabajando: { l: 'trabajando', sy: 'dots', eh: .55, h: 200, rr: 8, hn: 1, hm: 1, rX: 16, rY: 34, lX: -16, lY: 34, hwy: 4, swS: 11, hud: 1, dw: 3.5, an: .35, tl: .1, bobA: 1 },
  compilando: { l: 'compilando', sy: 'dots', eh: .7, h: 40, hn: 1, hm: 1, rX: 20, rY: 48, lX: -20, lY: 48, hwy: 4, swS: 11, hud: 1, rr: 14, dw: 3.5, an: .3, bobA: 1 },
  permiso: { l: '¿me da permiso?', sy: '!', eh: 1.4, h: 40, hn: 1, hm: 1, rX: 56, rY: -2, lX: -56, lY: -2, bobA: 4, bobS: 4, hwy: 3, swS: 5 },
  error: { l: 'algo falló', sy: '×', eh: .8, h: 355, shake: 1, an: 1.1, hn: 1, hm: 1, rX: 34, rY: -34, lX: -34, lY: -34 },
  terminado: { l: '¡listo!', sy: '✓', hp: 1, h: 140, hop: .9, sw: .2, swS: 6, hn: 1, hm: 1, rX: 54, rY: -36, lX: -54, lY: -36, hwy: 5 },
  // Adornos: solo en reposo.
  vuelo: { l: 'en vuelo', sy: '↑', fl: 1, tl: .3, hn: 1, hm: 1, rX: 44, rY: 30, lX: -44, lY: 30, bobA: 4, bobS: 3, rr: 3, eh: .8, an: .3 },
  disparo: { l: '¡repulsor!', sy: '»', ch: 1, hn: 1, hm: .8, rX: 64, rY: 4, lX: -40, lY: 22, an: .6, eh: .7, tl: -.1, rr: 5, shake: .15, h: 200 },
  // Jarvi baila sin notitas: en su HTML el baile es solo el salto y el
  // contoneo, las notas musicales son de Vee, Opi y Buffi.
  baile: { l: '♪', sy: '♪', hp: 1, sw: 1, swS: 5, hop: 1, hs: 1, bobA: 3, bobS: 5, rr: 4, hn: 1, hm: 1, rX: 58, rY: 6, lX: -58, lY: 6, hwx: 9, hwy: 11 },
  mareado: { l: '@_@', sy: '@', eh: .9, dz: 1, sw: .25, swS: 7, h: 170 },
  dormido: { l: 'zzz', sy: 'z', eh: .05, h: 225, bobA: 3, bobS: 1, rr: .2, tl: .35 },
};
const SK = { bt: '#ffffff', bm: '#c9d1dc', bb: '#7a8596', pt: '#fff0a6', pm: '#f2bb40', pb: '#b3700f', eye: '#2a0e06', ol: 'rgba(80,35,0,.45)' };

function pintaJarvi(c, u, st, mirar) {
  const { P, ph, t, sp, grande, estado } = st;
  const L = mirar;
  const h = (P.h + P.hs * t * 90) % 360;
  const hc = (l, a) => `hsla(${h},100%,${l}%,${a})`;
  const sy = Math.sin(ph.s), w2 = Math.sin(ph.s + 1.2);
  const hop = Math.abs(sy) * P.hop * 14, bob = Math.sin(ph.b) * P.bobA, lag = Math.sin(ph.b - .7) * 2;
  const bt = t - (st.parpadeo === undefined ? t - 9 : st.parpadeo);
  const blink = bt < .16 ? Math.sin(bt / .16 * 3.1416) : 0;
  const ly = L.y + P.ly, ee = Math.max(.12, P.eh * (1 - blink * .92));

  c.fillStyle = 'rgba(255,150,90,.15)';
  c.beginPath(); c.ellipse(0, 64, 36 - hop * .6 - P.fl * 10, 5, 0, 0, 7); c.fill();
  let g = c.createRadialGradient(0, 6, 20, 0, 6, 92);
  g.addColorStop(0, hc(60, .22)); g.addColorStop(1, hc(60, 0));
  c.fillStyle = g; c.fillRect(-100, -100, 200, 200);

  c.translate(sy * P.sw * 10 + Math.sin(t * 41) * P.shake * 2, 8 + bob - hop);
  c.rotate(sy * P.sw * .12 + P.fl * .25 + P.tl * .3 + Math.sin(ph.d) * P.dz * .12 + Math.sin(t * 37) * P.shake * .04);
  const br = Math.sin(t * 2) * .014;
  c.scale(1 - P.sq * .09 + br, 1 + P.sq * .09 - br);

  // Anillo HUD.
  if (P.hud > .02) {
    c.save(); c.globalAlpha = Math.min(1, P.hud); c.strokeStyle = hc(65, .8); c.lineWidth = 1.3;
    for (let i = 0; i < 2; i++) {
      c.save(); c.rotate(ph.r * (i ? -1 : 1) * .6);
      c.setLineDash(i ? [3, 6] : [22, 10]);
      c.beginPath(); c.arc(0, 0, 66 + i * 7, 0, 6.283); c.stroke(); c.restore();
    }
    c.restore();
  }
  // Llama del propulsor.
  if (P.fl > .02) {
    const len = (22 + Math.sin(t * 35) * 5) * P.fl;
    g = c.createLinearGradient(0, 40, 0, 40 + len);
    G(g, '#fff', hc(70, .9), hc(55, 0));
    c.fillStyle = g;
    c.beginPath(); c.moveTo(-9, 38);
    c.quadraticCurveTo(-11, 40 + len * .6, 0, 40 + len);
    c.quadraticCurveTo(11, 40 + len * .6, 9, 38); c.fill();
  }
  // Orejitas doradas.
  c.fillStyle = G(c.createLinearGradient(0, -8, 0, 8), SK.pt, SK.pb);
  for (const s of [-1, 1]) { c.beginPath(); c.roundRect(s * 52 - 3, -8, 6, 16, 3); c.fill(); }
  // Cuerpo.
  sq(c, 52, 42);
  c.fillStyle = G(c.createRadialGradient(-14, -22, 3, 0, 0, 64), SK.bt, SK.bm, SK.bb); c.fill();
  c.strokeStyle = 'rgba(255,255,255,.35)'; c.lineWidth = 1.4; c.stroke();
  c.fillStyle = 'rgba(255,255,255,.5)';
  c.beginPath(); c.ellipse(-24, -31, 16, 5, -.3, 0, 7); c.fill();
  // Reactor.
  c.save(); c.translate(0, 29);
  c.fillStyle = '#0d141b'; c.beginPath(); c.arc(0, 0, 8, 0, 7); c.fill();
  c.strokeStyle = SK.pm; c.lineWidth = 1.6; c.stroke();
  c.shadowColor = hc(60, 1); c.shadowBlur = 10 * u;
  c.strokeStyle = hc(75, 1); c.lineWidth = 1.3; c.beginPath(); c.arc(0, 0, 5.4, 0, 7); c.stroke();
  c.rotate(ph.r);
  for (let i = 0; i < 6; i++) { c.rotate(1.0472); c.beginPath(); c.moveTo(2, 0); c.lineTo(4.4, 0); c.stroke(); }
  c.fillStyle = hc(90, 1); c.beginPath(); c.arc(0, 0, 1.8 + Math.sin(t * 3) * .4, 0, 7); c.fill();
  c.restore();
  // Carita dorada con paralaje.
  c.save(); c.translate(L.x * 2.5, -6 + L.y * 2);
  sq(c, 35, 22);
  c.fillStyle = G(c.createLinearGradient(0, -22, 0, 22), SK.pt, SK.pm, SK.pb); c.fill();
  c.strokeStyle = SK.ol; c.lineWidth = 1.3; c.stroke();
  c.fillStyle = 'rgba(255,255,255,.45)';
  c.beginPath(); c.ellipse(-14, -14, 11, 3.5, -.25, 0, 7); c.fill();
  mofletes(c, '.33', '255,90,90', 6, 5, 3);
  // Jarvi los quiere un pelin mas grandes, con puntito de brillo y puntas
  // redondeadas: es lo unico que lo distingue de los otros tres.
  ojos(c, P, ph, t, L.x, ly, ee, 13, SK.eye, 13, u, { rx: 3.7, ry: 6.2, lw: 2.6, brillo: true, redondo: true });
  c.restore();
  // Estrellas de mareo.
  if (P.dz > .02) {
    c.fillStyle = '#ffe58a'; c.font = '10px system-ui'; c.textAlign = 'center';
    c.globalAlpha = Math.min(1, P.dz);
    for (let i = 0; i < 3; i++) { const a = ph.d * .8 + i * 2.094; c.fillText('★', Math.cos(a) * 34, -50 + Math.sin(a) * 7); }
    c.globalAlpha = 1;
  }
  // Repulsor.
  const rk = Math.sin(t * 44);
  const R = [P.rX + Math.sin(ph.s) * P.hwx - P.ch * 3 * rk, P.rY + w2 * P.hwy + lag];
  const Lh = [P.lX - Math.sin(ph.s) * P.hwx, P.lY - w2 * P.hwy + lag];
  if (P.ch > .05 && P.hn > .3) {
    const a = Math.min(1, P.ch);
    c.save(); c.lineCap = 'round';
    c.strokeStyle = hc(60, .4 * a); c.lineWidth = (13 + rk * 3) * a;
    c.shadowColor = hc(60, 1); c.shadowBlur = 16 * u;
    c.beginPath(); c.moveTo(R[0] + 6, R[1]); c.lineTo(R[0] + 190, R[1]); c.stroke();
    c.strokeStyle = `rgba(255,255,255,${a})`; c.lineWidth = 4.5 * a;
    c.beginPath(); c.moveTo(R[0] + 6, R[1]); c.lineTo(R[0] + 190, R[1]); c.stroke();
    c.restore();
    if (grande && Math.random() < .7) sp.push({ x: R[0] + 12, y: R[1], vx: 30 + Math.random() * 90, vy: (Math.random() - .5) * 80, l: 1 });
  }
  if (P.shake > .5 && grande && Math.random() < .5) sp.push({ x: (Math.random() - .5) * 50, y: -44, vx: (Math.random() - .5) * 60, vy: -30 - Math.random() * 40, l: 1 });
  for (const [x, y, s, vis] of [[R[0], R[1], 1, P.hn], [Lh[0], Lh[1], -1, P.hm]]) {
    if (vis < .03) continue;
    c.save(); c.translate(x, y); c.scale(Math.min(1.2, vis), Math.min(1.2, vis));
    c.fillStyle = G(c.createRadialGradient(-2, -3, 1, 0, 0, 9), SK.pt, SK.pm, SK.pb);
    c.beginPath(); c.arc(0, 0, 7, 0, 7); c.fill();
    c.strokeStyle = SK.ol; c.lineWidth = 1.1; c.stroke();
    const ch = s > 0 ? P.ch : P.ch * .4;
    c.shadowColor = hc(60, 1); c.shadowBlur = (5 + ch * 10) * u;
    c.fillStyle = hc(78, .8 + ch * .2); c.beginPath(); c.arc(0, 0, 2.4 + ch * 2.6, 0, 7); c.fill();
    c.restore();
  }
  // Jarvi no tiene notitas musicales: en su HTML el baile es solo el salto
  // con el contoneo. Las ♪ son de Vee, Opi y Buffi.
  for (const p of sp) { c.fillStyle = hc(80, p.l); c.fillRect(p.x, p.y, 2.2, 2.2); }
  c.restore();
  globo(c, st.ancho, h, P, ph, sy, P.bp, { sy: JARVI_S[estado] ? JARVI_S[estado].sy : 'dots', globoS: 90, globoL: 52 }, P.bobA, hop, -40);
}

// ── Vee · VS Code ────────────────────────────────────────────────────────────

const CL = ['#569cd6', '#ce9178', '#4ec9b0', '#c586c0', '#dcdcaa'];
const VEE_DEF = {
  eh: 1, hp: 0, xx: 0, h: 205, bobA: 2, bobS: 2, sw: 0, swS: 2, hop: 0, ly: 0,
  shake: 0, sq: 0, dz: 0, an: 0, dw: .8, bp: 0, hs: 0, hn: 0, hm: 0, rX: 50, rY: 14,
  lX: -50, lY: 14, hwx: 0, hwy: 0, lp: 0, cd: 0, pal: 0, dbg: 0, ex: 0, sg: 0, nt: 0, bs: 1, tl: 0, th: 0,
};
const VEE_S = {
  reposo: { l: 'en reposo', sy: 'dots', bs: 0, sw: .35, swS: 1.2, bobA: 2.5, bobS: 1.6 },
  saludo: { l: 'hello, world!', sy: 'dots', bs: 0, eh: 1.2, hn: 1, rX: 58, rY: -20, hwx: 7, hwy: 3, sw: .3, swS: 7, hop: .25 },
  pensando: { l: 'Ctrl+Shift+P', sy: 'dots', eh: .8, ly: -.8, pal: 1, hn: 1, rX: 14, rY: 18, dw: 3.5, tl: -.2, bobA: 3 },
  programando: { l: 'programando', sy: 'dots', eh: .55, h: 190, hn: 1, hm: 1, rX: 20, rY: 48, lX: -20, lY: 48, hwy: 3.5, swS: 12, lp: 1, cd: 1, dw: 3.5, an: .3, tl: .08, bobA: 1 },
  compilando: { l: 'compilando', sy: 'dots', eh: .7, h: 40, hn: 1, hm: 1, rX: 20, rY: 48, lX: -20, lY: 48, lp: 1, dbg: 1, dw: 3.5, bobA: 1 },
  depurando: { l: 'depurando', sy: 'dots', eh: .9, h: 25, dbg: 1, ly: -.3, hn: 1, rX: 26, rY: -32, hwy: 3, swS: 6, dw: 3.5, bobA: 1.5 },
  extension: { l: 'extensión instalada', sy: '+', hp: 1, eh: 1.1, h: 285, hn: 1, rX: 56, rY: -2, ex: 1, hwy: 2, swS: 5, hop: .2 },
  permiso: { l: '¿confías en esta carpeta?', sy: '!', eh: 1.4, h: 42, sg: 1, hn: 1, hm: 1, rX: 56, rY: -2, lX: -56, lY: -2, bobA: 4, bobS: 4, hwy: 3, swS: 5 },
  error: { l: '3 problemas', sy: '×', eh: .8, h: 355, shake: 1, xx: 1, sg: 1, hn: 1, hm: 1, rX: 34, rY: -34, lX: -34, lY: -34 },
  terminado: { l: '0 problemas', sy: '✓', hp: 1, h: 125, hop: .9, sw: .2, swS: 6, hn: 1, hm: 1, rX: 56, rY: -38, lX: -56, lY: -38, hwy: 5 },
  tema: { l: 'tema claro', sy: '☀', hp: .6, eh: 1.1, th: 1, h: 48, hn: 1, rX: 56, rY: -14, hwy: 2, swS: 4, hop: .2 },
  baile: { l: '♪', sy: '♪', hp: 1, sw: 1, swS: 5, hop: 1, hs: 1, bobA: 3, bobS: 5, hn: 1, hm: 1, rX: 58, rY: 6, lX: -58, lY: 6, hwx: 9, hwy: 11, nt: 1 },
  mareado: { l: '@_@', sy: '@', eh: .9, dz: 1, sw: .25, swS: 7, h: 170 },
  dormido: { l: 'zzz', sy: 'z', eh: .05, h: 225, bobA: 3, bobS: 1, tl: .35 },
};

function pintaVee(c, u, st, mirar) {
  const { P, ph, t, sp, grande, estado, ancho } = st;
  const L = mirar;
  const h = (P.h + P.hs * t * 90) % 360;
  const hc = (l, a) => `hsla(${h},100%,${l}%,${a})`;
  const sy = Math.sin(ph.s), w2 = Math.sin(ph.s + 1.2);
  const hop = Math.abs(sy) * P.hop * 14, bob = Math.sin(ph.b) * P.bobA, lag = Math.sin(ph.b - .7) * 2;
  const bt = t - (st.parpadeo === undefined ? t - 9 : st.parpadeo);
  const blink = bt < .16 ? Math.sin(bt / .16 * 3.1416) : 0;
  const ly = L.y + P.ly, ee = Math.max(.12, P.eh * (1 - blink * .92));

  c.fillStyle = 'rgba(80,170,255,.14)';
  c.beginPath(); c.ellipse(0, 64, 36 - hop * .6, 5, 0, 0, 7); c.fill();
  let g = c.createRadialGradient(0, 6, 20, 0, 6, 92);
  g.addColorStop(0, hc(55, .2)); g.addColorStop(1, hc(55, 0));
  c.fillStyle = g; c.fillRect(-100, -100, 200, 200);

  c.translate(sy * P.sw * 10 + Math.sin(t * 41) * P.shake * 2, 8 + bob - hop);
  c.rotate(sy * P.sw * .12 + P.tl * .3 + Math.sin(ph.d) * P.dz * .12 + Math.sin(t * 37) * P.shake * .04);
  const br = Math.sin(t * 2) * .014;
  c.scale(1 - P.sq * .09 + br, 1 + P.sq * .09 - br);

  sq(c, 52, 42);
  c.fillStyle = G(c.createRadialGradient(-14, -22, 3, 0, 0, 64), '#6fd0ff', '#1b8de8', '#06437a'); c.fill();
  c.strokeStyle = 'rgba(255,255,255,.4)'; c.lineWidth = 1.4; c.stroke();
  c.fillStyle = 'rgba(255,255,255,.35)';
  c.beginPath(); c.ellipse(-24, -31, 16, 5, -.3, 0, 7); c.fill();

  // Cara = editor de codigo.
  c.save(); c.translate(L.x * 2.5, -1 + L.y * 2);
  sq(c, 36, 23);
  c.fillStyle = G(c.createLinearGradient(0, -23, 0, 23), mixC('#212121', '#ffffff', P.th), mixC('#141414', '#f0f0f0', P.th));
  c.fill();
  c.save(); c.clip();
  if (P.dbg > .02) { c.fillStyle = `rgba(255,230,0,${.14 * P.dbg})`; c.fillRect(-36, -8, 72, 8); }
  if (P.cd > .02) {
    for (let i = 0; i < 5; i++) {
      const y = -22 + ((i * 9 + ph.c * 16) % 46), w = 12 + ((i * 7) % 5) * 6;
      c.fillStyle = CL[i];
      c.globalAlpha = .4 * P.cd * Math.min(1, (y + 22) / 6, (24 - y) / 6);
      c.fillRect(-22 + ((i * 13) % 10), y, w, 2);
    }
    c.globalAlpha = 1;
  }
  c.save();
  c.globalAlpha = .1 * (1 - Math.min(1, P.cd)) * (1 - Math.min(1, P.dbg));
  c.strokeStyle = '#3da9ff'; c.lineWidth = 5; c.lineCap = 'round'; c.lineJoin = 'round';
  c.beginPath(); c.moveTo(-2, -8); c.lineTo(-14, 0); c.lineTo(-2, 8);
  c.moveTo(3, -18); c.lineTo(17, -12); c.lineTo(17, 12); c.lineTo(3, 18); c.stroke();
  c.restore();
  c.fillStyle = 'rgba(255,255,255,.06)';
  c.beginPath(); c.moveTo(-36, -23); c.lineTo(-6, -23); c.lineTo(-26, 23); c.lineTo(-36, 23); c.fill();
  c.restore();
  sq(c, 36, 23);
  c.strokeStyle = hc(60, .75); c.lineWidth = 1.3; c.shadowColor = hc(62, 1); c.shadowBlur = 6 * u; c.stroke();
  if (P.dbg > .02) {
    c.shadowBlur = 0; c.globalAlpha = P.dbg; c.fillStyle = '#e51400';
    c.beginPath(); c.arc(-29, -4, 2.4 + Math.sin(t * 5) * .4, 0, 7); c.fill(); c.globalAlpha = 1;
  }
  c.shadowBlur = 0;
  mofletes(c, '.3');
  c.shadowBlur = 9 * u;
  ojos(c, P, ph, t, L.x, ly, ee, 11, mixC('#eaf6ff', '#14283d', P.th), 11, u);
  // Cursor como boca.
  c.shadowBlur = 5 * u; c.lineWidth = 1.4; c.lineCap = 'round';
  c.globalAlpha = Math.max(0, Math.min(1, Math.sin(t * 5) * 3 + .6)) * Math.max(0, 1 - P.hp - P.xx);
  c.beginPath(); c.moveTo(0, 8); c.lineTo(0, 17); c.moveTo(-2, 8); c.lineTo(2, 8); c.moveTo(-2, 17); c.lineTo(2, 17); c.stroke();
  if (P.hp > .01) { c.globalAlpha = P.hp; c.beginPath(); c.arc(0, 9, 5, .45, 2.7); c.stroke(); }
  c.globalAlpha = 1; c.shadowBlur = 0;
  if (P.sg > .02) {
    c.globalAlpha = Math.min(1, P.sg);
    c.strokeStyle = estado === 'error' ? '#f14c4c' : '#cca700'; c.lineWidth = 1.4;
    c.beginPath();
    for (let x = -22; x <= 22; x += 2) { const y = 18.5 + Math.sin((x + ph.c * 10) * .9) * 1.4; x === -22 ? c.moveTo(x, y) : c.lineTo(x, y); }
    c.stroke(); c.globalAlpha = 1;
  }
  c.restore();

  c.font = 'bold 11px ui-monospace,Menlo,Consolas,monospace'; c.textAlign = 'center'; c.textBaseline = 'middle';
  if (P.dz > .02) {
    c.fillStyle = '#ffe58a'; c.globalAlpha = Math.min(1, P.dz);
    for (let i = 0; i < 3; i++) { const a = ph.d * .8 + i * 2.094; c.fillText('★', Math.cos(a) * 34, -54 + Math.sin(a) * 7); }
    c.globalAlpha = 1;
  }
  if (P.nt > .02) {
    c.fillStyle = hc(72, 1); c.font = 'bold 14px system-ui';
    for (let i = 0; i < 3; i++) { const p = (ph.w * .15 + i / 3) % 1; c.globalAlpha = Math.min(1, P.nt) * (1 - p); c.fillText('♪', 52 + Math.sin(p * 9 + i) * 7, -10 - p * 44); }
    c.globalAlpha = 1;
  }
  // Paleta de comandos.
  if (P.pal > .02) {
    const a = Math.min(1, P.pal);
    c.save(); c.globalAlpha = a; c.translate(0, -(1 - P.pal) * 14);
    c.fillStyle = '#252526'; c.beginPath(); c.roundRect(-42, -68, 84, 15, 4); c.fill();
    c.strokeStyle = hc(60, .9); c.lineWidth = 1.1; c.stroke();
    const full = 'Format Document', n = Math.floor((ph.c * 3) % (full.length + 6)), s = full.slice(0, Math.min(n, full.length));
    c.font = '6.5px ui-monospace,Menlo,Consolas,monospace'; c.textAlign = 'left';
    c.fillStyle = hc(70, 1); c.fillText('>', -37, -60);
    c.fillStyle = '#d4d4d4'; c.fillText(s, -30, -60);
    c.globalAlpha = a * (Math.sin(t * 6) > 0 ? 1 : .2);
    c.fillRect(-30 + c.measureText(s).width + .8, -64, .9, 8);
    c.restore();
  }
  // Barra del depurador.
  if (P.dbg > .02) {
    const a = Math.min(1, P.dbg);
    c.save(); c.globalAlpha = a; c.translate(0, -(1 - P.dbg) * 14);
    c.fillStyle = '#333'; c.beginPath(); c.roundRect(-27, -68, 54, 14, 4); c.fill();
    c.strokeStyle = '#666'; c.lineWidth = 1; c.stroke();
    c.fillStyle = '#75beff';
    c.beginPath(); c.moveTo(-20, -64.5); c.lineTo(-20, -57.5); c.lineTo(-14.5, -61); c.fill();
    c.strokeStyle = '#75beff'; c.lineWidth = 1.3;
    c.beginPath(); c.arc(-3, -59.5, 3, 3.4, 6.0); c.moveTo(0.4, -62.6); c.lineTo(0.4, -59.4); c.lineTo(-2.4, -60); c.stroke();
    c.strokeStyle = '#89d185'; c.beginPath(); c.arc(9, -61, 3, .5, 5.6); c.stroke();
    c.fillStyle = '#f48771'; c.fillRect(16, -64, 6, 6);
    c.restore();
  }
  // Teclado.
  if (P.lp > .02) {
    const a = Math.min(1, P.lp);
    c.save(); c.globalAlpha = a; c.translate(0, (1 - P.lp) * 34);
    c.fillStyle = '#2b2d31'; c.beginPath(); c.roundRect(-37, 44, 74, 22, 5); c.fill();
    c.strokeStyle = '#5a5f68'; c.lineWidth = 1.1; c.stroke();
    for (let rr = 0; rr < 3; rr++) for (let kk = 0; kk < 9; kk++) {
      const on = Math.sin(ph.c * 9 + kk * 1.7 + rr * 2.9) > .82;
      c.fillStyle = on ? hc(62, 1) : '#454a53';
      c.beginPath(); c.roundRect(-33 + kk * 7.4, 47 + rr * 5.8, 6.2, 4.4, 1.2); c.fill();
    }
    c.restore();
  }
  // Manitas y pieza de puzzel.
  const { R, Lh } = manitas(c, P, ph, t, {}, 0);
  if (P.ex > .02) {
    const e = Math.max(0, P.ex);
    c.save(); c.translate(R[0] + 11, R[1] - 4);
    c.rotate(Math.sin(ph.s * 1.4) * .2); c.scale(e, e);
    c.fillStyle = '#c586c0'; c.strokeStyle = '#5b2c63'; c.lineWidth = 1.2;
    c.beginPath(); c.roundRect(-6, -6, 12, 12, 2); c.arc(6, 0, 3, 0, 7); c.arc(0, -6, 3, 0, 7); c.fill(); c.stroke();
    c.fillStyle = '#c586c0'; c.beginPath(); c.roundRect(-5, -5, 10, 10, 2); c.fill();
    c.restore();
  }
  const coloresVee = { brazoOscuro: '#06437a', brazo: '#1b8de8', mano: '#e6f3ff', manoBorde: '#b5d6f2' };
  for (const [sx, x, y, vis, s] of [[46, R[0], R[1], P.hn, 1], [-46, Lh[0], Lh[1], P.hm, -1]]) {
    if (vis < .03) continue;
    c.save(); c.lineCap = 'round'; c.globalAlpha = Math.min(1, vis * 1.5);
    c.strokeStyle = coloresVee.brazoOscuro; c.lineWidth = 10 * Math.min(1.2, vis);
    c.beginPath(); c.moveTo(sx, 14);
    c.quadraticCurveTo((sx + x) / 2 + s * 8, (14 + y) / 2 - 6, x, y); c.stroke();
    c.strokeStyle = coloresVee.brazo; c.lineWidth = 7.4 * Math.min(1.2, vis); c.stroke();
    c.fillStyle = G(c.createRadialGradient(x - 2, y - 3, 1, x, y, 9), '#ffffff', '#e6f3ff', '#b5d6f2');
    c.beginPath(); c.arc(x, y, 6.8 * Math.min(1.2, vis), 0, 7); c.fill();
    c.strokeStyle = '#06437a'; c.lineWidth = 1.3; c.stroke();
    c.restore();
  }
  if (estado === 'terminado' && grande && Math.random() < .5) sp.push({ x: (Math.random() - .5) * 60, y: -38, vx: (Math.random() - .5) * 70, vy: -70 - Math.random() * 70, l: 1, c: `hsl(${Math.random() * 360},95%,62%)` });
  if (P.shake > .5 && grande && Math.random() < .5) sp.push({ x: (Math.random() - .5) * 50, y: -44, vx: (Math.random() - .5) * 60, vy: -30 - Math.random() * 40, l: 1 });
  for (const p of sp) { c.globalAlpha = Math.max(0, p.l); c.fillStyle = p.c || hc(70, 1); c.fillRect(p.x, p.y, 2.6, 2.6); }
  c.globalAlpha = 1;
  c.restore();
  globo(c, ancho, h, P, ph, sy, P.bp, { sy: VEE_S[estado] ? VEE_S[estado].sy : 'dots', globoS: 85, globoL: 45 }, P.bobA, hop, -44);
}

// ── Opi · OpenCode ───────────────────────────────────────────────────────────

const OPI_DEF = {
  eh: 1, hp: 0, xx: 0, h: 155, bobA: 2, bobS: 2, sw: 0, swS: 2, hop: 0, ly: 0,
  shake: 0, sq: 0, dz: 0, an: 0, dw: .8, bp: 0, hs: 0, hn: 0, hm: 0, rX: 50, rY: 14,
  lX: -50, lY: 14, hwx: 0, hwy: 0, lp: 0, cd: 0, pb: 0, cs: 0, nt: 0, bs: 1, tl: 0,
};
const OPI_S = {
  reposo: { l: 'en reposo', sy: 'dots', bs: 0, sw: .35, swS: 1.2, bobA: 2.5, bobS: 1.6 },
  saludo: { l: 'hello, world!', sy: 'dots', bs: 0, eh: 1.2, hn: 1, rX: 58, rY: -20, hwx: 7, hwy: 3, sw: .3, swS: 7, hop: .25 },
  pensando: { l: 'pensando…', sy: 'dots', eh: .8, h: 265, ly: -.8, hn: 1, rX: 14, rY: 18, cs: 1, dw: 3.5, tl: -.2, bobA: 3 },
  programando: { l: 'programando', sy: 'dots', eh: .55, h: 170, hn: 1, hm: 1, rX: 20, rY: 48, lX: -20, lY: 48, hwy: 3.5, swS: 12, lp: 1, cd: 1, dw: 3.5, an: .3, tl: .08, bobA: 1 },
  compilando: { l: 'compilando', sy: 'dots', eh: .7, h: 40, hn: 1, hm: 1, rX: 20, rY: 48, lX: -20, lY: 48, lp: 1, pb: 1, dw: 3.5, bobA: 1 },
  permiso: { l: '¿ejecuto? [y/n]', sy: '?', eh: 1.4, h: 42, hn: 1, hm: 1, rX: 56, rY: -2, lX: -56, lY: -2, bobA: 4, bobS: 4, hwy: 3, swS: 5 },
  error: { l: 'error en el build', sy: '×', eh: .8, h: 355, shake: 1, xx: 1, hn: 1, hm: 1, rX: 34, rY: -34, lX: -34, lY: -34 },
  terminado: { l: '¡build ok!', sy: '✓', hp: 1, h: 125, hop: .9, sw: .2, swS: 6, hn: 1, hm: 1, rX: 54, rY: -36, lX: -54, lY: -36, hwy: 5 },
  baile: { l: '♪', sy: '♪', hp: 1, sw: 1, swS: 5, hop: 1, hs: 1, bobA: 3, bobS: 5, hn: 1, hm: 1, rX: 58, rY: 6, lX: -58, lY: 6, hwx: 9, hwy: 11, nt: 1 },
  mareado: { l: '@_@', sy: '@', eh: .9, dz: 1, sw: .25, swS: 7, h: 170 },
  dormido: { l: 'zzz', sy: 'z', eh: .05, h: 225, bobA: 3, bobS: 1, tl: .35 },
};

function pintaOpi(c, u, st, mirar) {
  const { P, ph, t, sp, grande, estado, ancho } = st;
  const L = mirar;
  const h = (P.h + P.hs * t * 90) % 360;
  const hc = (l, a) => `hsla(${h},100%,${l}%,${a})`;
  const sy = Math.sin(ph.s), w2 = Math.sin(ph.s + 1.2);
  const hop = Math.abs(sy) * P.hop * 14, bob = Math.sin(ph.b) * P.bobA, lag = Math.sin(ph.b - .7) * 2;
  const bt = t - (st.parpadeo === undefined ? t - 9 : st.parpadeo);
  const blink = bt < .16 ? Math.sin(bt / .16 * 3.1416) : 0;
  const ly = L.y + P.ly, ee = Math.max(.12, P.eh * (1 - blink * .92));

  c.fillStyle = 'rgba(80,255,170,.12)';
  c.beginPath(); c.ellipse(0, 64, 36 - hop * .6, 5, 0, 0, 7); c.fill();
  let g = c.createRadialGradient(0, 6, 20, 0, 6, 92);
  g.addColorStop(0, hc(55, .2)); g.addColorStop(1, hc(55, 0));
  c.fillStyle = g; c.fillRect(-100, -100, 200, 200);

  c.translate(sy * P.sw * 10 + Math.sin(t * 41) * P.shake * 2, 8 + bob - hop);
  c.rotate(sy * P.sw * .12 + P.tl * .3 + Math.sin(ph.d) * P.dz * .12 + Math.sin(t * 37) * P.shake * .04);
  const br = Math.sin(t * 2) * .014;
  c.scale(1 - P.sq * .09 + br, 1 + P.sq * .09 - br);

  // Diadema de auriculares.
  c.lineCap = 'round'; c.strokeStyle = '#1c2028'; c.lineWidth = 5;
  c.beginPath(); c.arc(0, -2, 53, 3.5, 5.92); c.stroke();
  c.strokeStyle = hc(62, .6); c.lineWidth = 1.3;
  c.beginPath(); c.arc(0, -2, 53, 3.5, 5.92); c.stroke();

  sq(c, 52, 42);
  c.fillStyle = G(c.createRadialGradient(-14, -22, 3, 0, 0, 64), '#505768', '#2b303b', '#12151b'); c.fill();
  c.strokeStyle = 'rgba(255,255,255,.2)'; c.lineWidth = 1.4; c.stroke();
  c.fillStyle = 'rgba(255,255,255,.2)';
  c.beginPath(); c.ellipse(-24, -31, 16, 5, -.3, 0, 7); c.fill();
  c.fillStyle = hc(70, .85);
  c.font = 'bold 9px ui-monospace,Menlo,Consolas,monospace';
  c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText('>_', 0, 31);
  for (const s of [-1, 1]) {
    c.fillStyle = G(c.createLinearGradient(0, -14, 0, 8), '#3f4655', '#1a1e26');
    c.beginPath(); c.roundRect(s * 53 - 5, -14, 10, 22, 4); c.fill();
    c.strokeStyle = hc(62, .8); c.lineWidth = 1.3; c.stroke();
    c.fillStyle = hc(75, .6 + Math.sin(t * 3 + s) * .4);
    c.beginPath(); c.arc(s * 53, -3, 1.4, 0, 7); c.fill();
  }

  c.save(); c.translate(L.x * 2.5, -6 + L.y * 2);
  sq(c, 36, 23);
  c.fillStyle = G(c.createLinearGradient(0, -23, 0, 23), '#0c1f17', '#030b08'); c.fill();
  c.save(); c.clip();
  c.fillStyle = 'rgba(255,255,255,.035)';
  for (let y = -22; y < 22; y += 4) c.fillRect(-36, y, 72, 1.4);
  if (P.cd > .02) {
    for (let i = 0; i < 5; i++) {
      const y = -22 + ((i * 9 + ph.c * 16) % 46), w = 12 + ((i * 7) % 5) * 6;
      c.fillStyle = hc(65, .33 * P.cd * Math.min(1, (y + 22) / 6, (24 - y) / 6));
      c.fillRect(-30 + (i * 13) % 10, y, w, 2);
    }
  }
  if (P.pb > .02) {
    c.fillStyle = hc(40, .25 * P.pb);
    c.beginPath(); c.roundRect(-24, 13, 48, 4, 2); c.fill();
    c.fillStyle = hc(72, P.pb);
    c.beginPath(); c.roundRect(-24, 13, Math.max(3, 48 * ((ph.c * .12) % 1)), 4, 2); c.fill();
  }
  c.fillStyle = 'rgba(255,255,255,.07)';
  c.beginPath(); c.moveTo(-36, -23); c.lineTo(-6, -23); c.lineTo(-26, 23); c.lineTo(-36, 23); c.fill();
  c.restore();
  sq(c, 36, 23);
  c.strokeStyle = hc(60, .7); c.lineWidth = 1.3; c.shadowColor = hc(62, 1); c.shadowBlur = 6 * u; c.stroke();
  ojos(c, P, ph, t, L.x, ly, ee, 13, hc(78, 1), 13, u);
  c.globalAlpha = Math.max(0, Math.min(1, Math.sin(t * 5) * 3 + .6)) * Math.max(0, 1 - P.hp - P.xx);
  c.beginPath(); c.roundRect(-4.5, 12, 9, 2.8, 1); c.fill();
  if (P.hp > .01) { c.globalAlpha = P.hp; c.beginPath(); c.arc(0, 8, 5, .45, 2.7); c.stroke(); }
  c.globalAlpha = 1; c.shadowBlur = 0;
  c.restore();

  c.font = 'bold 11px ui-monospace,Menlo,Consolas,monospace'; c.textAlign = 'center'; c.textBaseline = 'middle';
  if (P.cs > .02) {
    c.fillStyle = hc(72, Math.min(1, P.cs));
    ['{', '}', '/>'].forEach((gl, i) => { const a = ph.d * .6 + i * 2.094; c.fillText(gl, Math.cos(a) * 46, -50 + Math.sin(a) * 8); });
  }
  if (P.dz > .02) {
    c.fillStyle = '#ffe58a'; c.globalAlpha = Math.min(1, P.dz);
    for (let i = 0; i < 3; i++) { const a = ph.d * .8 + i * 2.094; c.fillText('★', Math.cos(a) * 34, -52 + Math.sin(a) * 7); }
    c.globalAlpha = 1;
  }
  if (P.nt > .02) {
    c.fillStyle = hc(72, 1); c.font = 'bold 14px system-ui';
    for (let i = 0; i < 3; i++) { const p = (ph.w * .15 + i / 3) % 1; c.globalAlpha = Math.min(1, P.nt) * (1 - p); c.fillText('♪', 52 + Math.sin(p * 9 + i) * 7, -10 - p * 44); }
    c.globalAlpha = 1;
  }
  if (P.lp > .02) {
    const a = Math.min(1, P.lp);
    c.save(); c.globalAlpha = a; c.translate(0, (1 - P.lp) * 34);
    c.fillStyle = G(c.createLinearGradient(0, 24, 0, 54), '#dde1e9', '#9aa1ad');
    c.beginPath(); c.roundRect(-30, 24, 60, 30, 5); c.fill();
    c.strokeStyle = 'rgba(0,0,0,.4)'; c.lineWidth = 1.2; c.stroke();
    c.shadowColor = hc(62, 1); c.shadowBlur = 9 * u;
    c.fillStyle = hc(72, 1); c.beginPath(); c.arc(0, 39, 3.6, 0, 7); c.fill(); c.shadowBlur = 0;
    c.fillStyle = '#7b8290'; c.beginPath(); c.roundRect(-35, 53, 70, 5, 2.5); c.fill();
    c.restore();
  }
  const { R, Lh } = manitas(c, P, ph, t, {}, 0);
  for (const [x, y, vis] of [[R[0], R[1], P.hn], [Lh[0], Lh[1], P.hm]]) {
    if (vis < .03) continue;
    c.save(); c.translate(x, y); c.scale(Math.min(1.2, vis), Math.min(1.2, vis));
    c.fillStyle = G(c.createRadialGradient(-2, -3, 1, 0, 0, 9), '#69728a', '#343a48', '#171a21');
    c.beginPath(); c.arc(0, 0, 7, 0, 7); c.fill();
    c.strokeStyle = hc(62, .8); c.lineWidth = 1.2; c.stroke();
    c.restore();
  }
  if (estado === 'terminado' && grande && Math.random() < .5) sp.push({ x: (Math.random() - .5) * 60, y: -38, vx: (Math.random() - .5) * 70, vy: -70 - Math.random() * 70, l: 1, c: `hsl(${Math.random() * 360},95%,62%)` });
  if (P.shake > .5 && grande && Math.random() < .5) sp.push({ x: (Math.random() - .5) * 50, y: -44, vx: (Math.random() - .5) * 60, vy: -30 - Math.random() * 40, l: 1 });
  for (const p of sp) { c.globalAlpha = Math.max(0, p.l); c.fillStyle = p.c || hc(70, 1); c.fillRect(p.x, p.y, 2.6, 2.6); }
  c.globalAlpha = 1;
  c.restore();
  globo(c, ancho, h, P, ph, sy, P.bp, { sy: OPI_S[estado] ? OPI_S[estado].sy : 'dots', globoS: 85, globoL: 48 }, P.bobA, hop, -40);
}

// ── Buffi · Freebuff ─────────────────────────────────────────────────────────

const BUFFI_DEF = {
  eh: 1, hp: 0, xx: 0, h: 140, bobA: 2, bobS: 2, sw: 0, swS: 2, hop: 0, ly: 0,
  shake: 0, sq: 0, dz: 0, an: 0, dw: .8, bp: 0, hs: 0, hn: 0, hm: 0, rX: 50, rY: 14,
  lX: -50, lY: 14, hwx: 0, hwy: 0, lp: 0, cd: 0, pb: 0, cs: 0, nt: 0, bs: 1, tl: 0, bb: 0,
  fx: 0, ag: 0, ad: 0,
};
const BUFFI_S = {
  reposo: { l: 'en reposo', sy: 'dots', bs: 0, sw: .35, swS: 1.2, bobA: 2.5, bobS: 1.6 },
  saludo: { l: 'hello, world!', sy: 'dots', bs: 0, eh: 1.2, hn: 1, rX: 58, rY: -20, hwx: 7, hwy: 3, sw: .3, swS: 7, hop: .25 },
  pensando: { l: 'pensando…', sy: 'dots', eh: .8, h: 265, ly: -.8, hn: 1, rX: 14, rY: 18, cs: 1, dw: 3.5, tl: -.2, bobA: 3 },
  programando: { l: 'programando', sy: 'dots', eh: .55, h: 150, hn: 1, hm: 1, rX: 20, rY: 48, lX: -20, lY: 48, hwy: 3.5, swS: 12, lp: 1, cd: 1, dw: 3.5, an: .3, tl: .08, bobA: 1 },
  compilando: { l: 'compilando…', sy: 'dots', eh: .7, h: 40, hn: 1, hm: 1, rX: 20, rY: 48, lX: -20, lY: 48, lp: 1, pb: 1, dw: 3.5, bobA: 1 },
  pesas: { l: 'levantando código', sy: 'dots', eh: .6, hn: 1, hm: 1, bb: 1, swS: 3.2, an: .5, dw: 3.5, bobA: 1, fx: .7 },
  agentes: { l: 'agentes en paralelo', sy: 'dots', eh: .7, h: 190, hn: 1, hm: 1, rX: 40, rY: 22, lX: -40, lY: 22, ag: 1, dw: 3.5, an: .2, bobA: 1.5 },
  freebucks: { l: '+25 Freebucks', sy: 'F', hp: 1, h: 48, hop: .5, sw: .2, swS: 5, hn: 1, hm: 1, rX: 54, rY: -30, lX: -54, lY: -30, hwy: 4, fx: .4 },
  anuncio: { l: 'anuncio patrocinado', sy: 'dots', eh: 1, ly: .8, ad: 1, h: 140 },
  permiso: { l: '¿ejecuto? [y/n]', sy: '?', eh: 1.4, h: 42, hn: 1, hm: 1, rX: 56, rY: -2, lX: -56, lY: -2, bobA: 4, bobS: 4, hwy: 3, swS: 5 },
  error: { l: 'error en el build', sy: '×', eh: .8, h: 355, shake: 1, xx: 1, hn: 1, hm: 1, rX: 34, rY: -34, lX: -34, lY: -34 },
  terminado: { l: '¡build ok!', sy: '✓', hp: 1, h: 110, hop: .9, sw: .2, swS: 6, hn: 1, hm: 1, rX: 56, rY: -38, lX: -56, lY: -38, hwy: 5, fx: .5 },
  flex: { l: '¡a tope!', sy: '★', eh: 1.1, hp: .5, hn: 1, hm: 1, rX: 60, rY: -10, lX: -60, lY: -10, fx: 1, hwy: 2.5, swS: 3, hop: .2, sw: .12 },
  baile: { l: '♪', sy: '♪', hp: 1, sw: 1, swS: 5, hop: 1, hs: 1, bobA: 3, bobS: 5, hn: 1, hm: 1, rX: 58, rY: 6, lX: -58, lY: 6, hwx: 9, hwy: 11, nt: 1, fx: .4 },
  mareado: { l: '@_@', sy: '@', eh: .9, dz: 1, sw: .25, swS: 7, h: 170 },
  dormido: { l: 'zzz', sy: 'z', eh: .05, h: 225, bobA: 3, bobS: 1, tl: .35 },
};

function pintaBuffi(c, u, st, mirar) {
  const { P, ph, t, sp, grande, estado, ancho } = st;
  const L = mirar;
  const h = (P.h + P.hs * t * 90) % 360;
  const hc = (l, a) => `hsla(${h},100%,${l}%,${a})`;
  const sy = Math.sin(ph.s), w2 = Math.sin(ph.s + 1.2);
  const hop = Math.abs(sy) * P.hop * 14, bob = Math.sin(ph.b) * P.bobA, lag = Math.sin(ph.b - .7) * 2;
  const bt = t - (st.parpadeo === undefined ? t - 9 : st.parpadeo);
  const blink = bt < .16 ? Math.sin(bt / .16 * 3.1416) : 0;
  const ly = L.y + P.ly, ee = Math.max(.12, P.eh * (1 - blink * .92));

  c.fillStyle = 'rgba(120,255,120,.13)';
  c.beginPath(); c.ellipse(0, 64, 36 - hop * .6, 5, 0, 0, 7); c.fill();
  let g = c.createRadialGradient(0, 6, 20, 0, 6, 92);
  g.addColorStop(0, hc(55, .2)); g.addColorStop(1, hc(55, 0));
  c.fillStyle = g; c.fillRect(-100, -100, 200, 200);

  c.translate(sy * P.sw * 10 + Math.sin(t * 41) * P.shake * 2, 8 + bob - hop);
  c.rotate(sy * P.sw * .12 + P.tl * .3 + Math.sin(ph.d) * P.dz * .12 + Math.sin(t * 37) * P.shake * .04);
  const br = Math.sin(t * 2) * .014;
  c.scale(1 - P.sq * .09 + br, 1 + P.sq * .09 - br);

  sq(c, 52, 42);
  c.fillStyle = G(c.createRadialGradient(-14, -22, 3, 0, 0, 64), '#d2ff86', '#38e87c', '#0b8f55'); c.fill();
  c.strokeStyle = 'rgba(255,255,255,.4)'; c.lineWidth = 1.4; c.stroke();
  c.fillStyle = 'rgba(255,255,255,.4)';
  c.beginPath(); c.ellipse(-24, -31, 16, 5, -.3, 0, 7); c.fill();
  // Moneda de Freebucks.
  c.save(); c.translate(0, 32); c.scale(Math.cos(t * 1.2) * .15 + .85, 1);
  c.fillStyle = G(c.createLinearGradient(-7, -7, 7, 7), '#fff2a0', '#ffc83a', '#c98a10');
  c.beginPath(); c.arc(0, 0, 7.5, 0, 7); c.fill();
  c.strokeStyle = '#8a5a08'; c.lineWidth = 1.2; c.stroke();
  c.fillStyle = '#7a4d06'; c.font = 'bold 9px system-ui,sans-serif';
  c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText('F', 0, .5);
  c.restore();
  // Cinta FREE.
  c.save(); sq(c, 52, 42); c.clip();
  c.fillStyle = '#0d1712'; c.fillRect(-60, -38, 120, 9);
  c.fillStyle = hc(62, .8); c.fillRect(-60, -38, 120, 1.2); c.fillRect(-60, -30.2, 120, 1.2);
  c.fillStyle = '#eaffd8'; c.font = 'bold 6.5px system-ui,sans-serif';
  if ('letterSpacing' in c) c.letterSpacing = '1.4px';
  c.fillText('FREE', 0, -33.2);
  if ('letterSpacing' in c) c.letterSpacing = '0px';
  c.restore();
  // Colitas.
  c.lineCap = 'round'; c.strokeStyle = '#0d1712'; c.lineWidth = 3.6;
  for (let j = 0; j < 2; j++) {
    c.beginPath(); c.moveTo(38, -34 + j * 3);
    c.quadraticCurveTo(48, -31 + j * 6 + Math.sin(ph.b * 2 + j) * 3, 58 + Math.sin(ph.b * 2.5 + j * 1.4) * 4, -26 + j * 10 + Math.sin(ph.b * 2 + j) * 4);
    c.stroke();
  }

  // Cara = terminal.
  c.save(); c.translate(L.x * 2.5, -1 + L.y * 2);
  sq(c, 36, 23);
  c.fillStyle = G(c.createLinearGradient(0, -23, 0, 23), '#0c1f12', '#030a05'); c.fill();
  c.save(); c.clip();
  c.fillStyle = 'rgba(255,255,255,.035)';
  for (let y = -22; y < 22; y += 4) c.fillRect(-36, y, 72, 1.4);
  if (P.cd > .02) {
    for (let i = 0; i < 5; i++) {
      const y = -22 + ((i * 9 + ph.c * 16) % 46), w = 12 + ((i * 7) % 5) * 6;
      c.fillStyle = hc(65, .33 * P.cd * Math.min(1, (y + 22) / 6, (24 - y) / 6));
      c.fillRect(-30 + (i * 13) % 10, y, w, 2);
    }
  }
  if (P.pb > .02) {
    c.fillStyle = hc(40, .25 * P.pb);
    c.beginPath(); c.roundRect(-24, 13, 48, 4, 2); c.fill();
    c.fillStyle = hc(72, P.pb);
    c.beginPath(); c.roundRect(-24, 13, Math.max(3, 48 * ((ph.c * .12) % 1)), 4, 2); c.fill();
  }
  c.fillStyle = 'rgba(255,255,255,.07)';
  c.beginPath(); c.moveTo(-36, -23); c.lineTo(-6, -23); c.lineTo(-26, 23); c.lineTo(-36, 23); c.fill();
  c.restore();
  sq(c, 36, 23);
  c.strokeStyle = hc(60, .7); c.lineWidth = 1.3; c.shadowColor = hc(62, 1); c.shadowBlur = 6 * u; c.stroke();
  ojos(c, P, ph, t, L.x, ly, ee, 13, hc(86, 1), 13, u);
  c.globalAlpha = Math.max(0, Math.min(1, Math.sin(t * 5) * 3 + .6)) * Math.max(0, 1 - P.hp - P.xx);
  c.beginPath(); c.roundRect(-4.5, 12, 9, 2.8, 1); c.fill();
  if (P.hp > .01) { c.globalAlpha = P.hp; c.beginPath(); c.arc(0, 8, 5, .45, 2.7); c.stroke(); }
  c.globalAlpha = 1; c.shadowBlur = 0;
  c.restore();

  c.font = 'bold 11px ui-monospace,Menlo,Consolas,monospace'; c.textAlign = 'center'; c.textBaseline = 'middle';
  if (P.cs > .02) {
    c.fillStyle = hc(72, Math.min(1, P.cs));
    ['{', '}', '/>'].forEach((gl, i) => { const a = ph.d * .6 + i * 2.094; c.fillText(gl, Math.cos(a) * 46, -50 + Math.sin(a) * 8); });
  }
  if (P.dz > .02) {
    c.fillStyle = '#ffe58a'; c.globalAlpha = Math.min(1, P.dz);
    for (let i = 0; i < 3; i++) { const a = ph.d * .8 + i * 2.094; c.fillText('★', Math.cos(a) * 34, -52 + Math.sin(a) * 7); }
    c.globalAlpha = 1;
  }
  if (P.nt > .02) {
    c.fillStyle = hc(72, 1); c.font = 'bold 14px system-ui';
    for (let i = 0; i < 3; i++) { const p = (ph.w * .15 + i / 3) % 1; c.globalAlpha = Math.min(1, P.nt) * (1 - p); c.fillText('♪', 52 + Math.sin(p * 9 + i) * 7, -10 - p * 44); }
    c.globalAlpha = 1;
  }
  // Portatil.
  if (P.lp > .02) {
    const a = Math.min(1, P.lp);
    c.save(); c.globalAlpha = a; c.translate(0, (1 - P.lp) * 34);
    c.fillStyle = G(c.createLinearGradient(0, 24, 0, 54), '#dde1e9', '#9aa1ad');
    c.beginPath(); c.roundRect(-30, 24, 60, 30, 5); c.fill();
    c.strokeStyle = 'rgba(0,0,0,.4)'; c.lineWidth = 1.2; c.stroke();
    c.shadowColor = hc(62, 1); c.shadowBlur = 9 * u;
    c.fillStyle = hc(72, 1); c.beginPath(); c.arc(0, 39, 3.6, 0, 7); c.fill(); c.shadowBlur = 0;
    c.fillStyle = '#7b8290'; c.beginPath(); c.roundRect(-35, 53, 70, 5, 2.5); c.fill();
    c.restore();
  }
  // Ayudantes y anuncio.
  if (P.ag > .02) {
    const cols = ['#7be7ff', '#ffd24a', '#ff93bd'], gs = Math.max(0, P.ag);
    for (let i = 0; i < 3; i++) {
      const a = ph.d * .5 + i * 2.094;
      const hx2 = Math.cos(a) * 72, hy = 34 + Math.sin(a) * 16 - Math.abs(Math.sin(ph.w * 1.3 + i * 2)) * 6;
      c.save(); c.translate(hx2, hy); c.scale(gs, gs);
      sq(c, 11, 9); c.fillStyle = cols[i]; c.fill();
      c.strokeStyle = 'rgba(0,0,0,.35)'; c.lineWidth = 1; c.stroke();
      c.fillStyle = '#0b1f12';
      c.beginPath(); c.ellipse(-3.5, -1, 1.3, 2, 0, 0, 7); c.ellipse(3.5, -1, 1.3, 2, 0, 0, 7); c.fill();
      c.strokeStyle = '#ffffff'; c.lineWidth = 1.5; c.lineCap = 'round';
      c.beginPath();
      if (i === 0) { c.arc(0, -17, 3, 0, 7); c.moveTo(2.2, -14.8); c.lineTo(5, -12); }
      else if (i === 1) { c.moveTo(-3, -12); c.lineTo(3, -18); }
      else { c.moveTo(-3, -15); c.lineTo(-1, -12.5); c.lineTo(3.5, -18); }
      c.stroke(); c.restore();
    }
  }
  if (P.ad > .02) {
    const a = Math.min(1, P.ad);
    c.save(); c.globalAlpha = a; c.translate(0, (1 - P.ad) * 30);
    c.fillStyle = 'rgba(255,255,255,.95)'; c.beginPath(); c.roundRect(-36, 58, 72, 20, 5); c.fill();
    c.fillStyle = '#38e87c'; c.beginPath(); c.roundRect(-32, 62, 14, 6, 2); c.fill();
    c.fillStyle = '#0b3a1f'; c.font = 'bold 5px system-ui';
    c.textAlign = 'center'; c.textBaseline = 'middle'; c.fillText('Ad', -25, 65.2);
    c.fillStyle = '#9aa7a0'; c.fillRect(-14, 62.5, 40, 2.4); c.fillRect(-32, 70, 58, 2.2);
    c.restore();
  }
  // Pesas.
  const { R, Lh, bbm, barY } = manitas(c, P, ph, t, {}, 0);
  if (bbm > .02) {
    c.save(); c.globalAlpha = Math.min(1, bbm);
    c.strokeStyle = '#cfd6df'; c.lineWidth = 3.5;
    c.beginPath(); c.moveTo(-46, barY); c.lineTo(46, barY); c.stroke();
    for (const s of [-1, 1]) for (const [dx, hh, ww] of [[36, 22, 8], [43, 16, 6]]) {
      c.fillStyle = '#1d232b'; c.strokeStyle = hc(62, .9); c.lineWidth = 1.2;
      c.beginPath(); c.roundRect(s * dx - ww / 2, barY - hh / 2, ww, hh, 2.5); c.fill(); c.stroke();
    }
    c.fillStyle = hc(72, .9); c.font = 'bold 6px ui-monospace,monospace'; c.fillText('</>', 0, barY - .5);
    c.restore();
  }
  const coloresBuffi = { brazoOscuro: '#0a6b3f', brazo: '#38e87c', mano: '#e8f7e0', manoBorde: '#b9d9b0', fx: P.fx, brazoClaro: '#5af598' };
  for (const [sx, x, y, vis, s] of [[46, R[0], R[1], P.hn, 1], [-46, Lh[0], Lh[1], P.hm, -1]]) {
    if (vis < .03) continue;
    const cx = (sx + x) / 2 + s * 8, cy = (14 + y) / 2 - 6 - P.fx * 8;
    c.save(); c.lineCap = 'round'; c.globalAlpha = Math.min(1, vis * 1.5);
    c.strokeStyle = coloresBuffi.brazoOscuro; c.lineWidth = 10 * Math.min(1.2, vis);
    c.beginPath(); c.moveTo(sx, 14); c.quadraticCurveTo(cx, cy, x, y); c.stroke();
    c.strokeStyle = coloresBuffi.brazo; c.lineWidth = 7.4 * Math.min(1.2, vis); c.stroke();
    if (P.fx > .02) {
      const bx = .25 * sx + .5 * cx + .25 * x, by = 3.5 + .5 * cy + .25 * y, rr = (4 + P.fx * 4) * Math.min(1.2, vis);
      c.fillStyle = '#5af598'; c.strokeStyle = '#0a6b3f'; c.lineWidth = 1.3;
      c.beginPath(); c.arc(bx, by, rr, 0, 7); c.fill(); c.stroke();
      c.fillStyle = 'rgba(255,255,255,.55)'; c.beginPath(); c.arc(bx - 1.5, by - 2, rr * .35, 0, 7); c.fill();
    }
    c.fillStyle = G(c.createRadialGradient(x - 2, y - 3, 1, x, y, 9), '#ffffff', '#e8f7e0', '#b9d9b0');
    c.beginPath(); c.arc(x, y, 6.8 * Math.min(1.2, vis), 0, 7); c.fill();
    c.strokeStyle = '#0a6b3f'; c.lineWidth = 1.3; c.stroke();
    c.restore();
  }
  if (estado === 'freebucks' && grande && Math.random() < .35) sp.push({ x: (Math.random() - .5) * 80, y: -80, vx: 0, vy: 20, l: 1, d: .7, coin: 1 });
  if (estado === 'terminado' && grande && Math.random() < .5) sp.push({ x: (Math.random() - .5) * 60, y: -38, vx: (Math.random() - .5) * 70, vy: -70 - Math.random() * 70, l: 1, c: `hsl(${Math.random() * 360},95%,62%)` });
  if (P.shake > .5 && grande && Math.random() < .5) sp.push({ x: (Math.random() - .5) * 50, y: -44, vx: (Math.random() - .5) * 60, vy: -30 - Math.random() * 40, l: 1 });
  for (const p of sp) {
    c.globalAlpha = Math.max(0, Math.min(1, p.l * 2));
    if (p.coin) {
      c.fillStyle = '#ffc83a'; c.strokeStyle = '#8a5a08'; c.lineWidth = .8;
      c.beginPath(); c.ellipse(p.x, p.y, 3.4 * Math.abs(Math.cos(p.x * .3 + t * 6)), 3.4, 0, 0, 7); c.fill(); c.stroke();
    } else {
      c.fillStyle = p.c || hc(70, 1); c.fillRect(p.x, p.y, 2.6, 2.6);
    }
  }
  c.globalAlpha = 1;
  c.restore();
  globo(c, ancho, h, P, ph, sy, P.bp, { sy: BUFFI_S[estado] ? BUFFI_S[estado].sy : 'dots', globoS: 80, globoL: 38 }, P.bobA, hop, -44);
}

// ── Tablas ───────────────────────────────────────────────────────────────────

export const MASCOTAS = {
  jarvi: { def: JARVI_DEF, estados: JARVI_S, pintar: pintaJarvi },
  vee: { def: VEE_DEF, estados: VEE_S, pintar: pintaVee },
  opi: { def: OPI_DEF, estados: OPI_S, pintar: pintaOpi },
  buffi: { def: BUFFI_DEF, estados: BUFFI_S, pintar: pintaBuffi },
};

export const DE_PASTILLA = {
  jarvis: 'jarvi',
  // La pastilla de VS Code se llama integration_claude en el catalogo de
  // Coucou; la de Claude Desktop es agent_claude-desktop. Las dos van con Vee,
  // que es la mascota que salio del HTML de VS Code.
  integration_claude: 'vee',
  'agent_claude-desktop': 'vee',
  vee: 'vee',
  freebuff: 'buffi',
  // La pastilla de Freebuff tambien aparece como agente suelto cuando corre
  // por su cuenta; es la misma mascota, asi que va al mismo sitio.
  agent_freebuff: 'buffi',
  buffi: 'buffi',
  agent_opencode: 'opi',
  opi: 'opi',
  agent_codex: 'opi',
};

// El estado canonico es el de Coucou; cada mascota lo traduce al suyo, porque
// no todas tienen los mismos adornos.
const CANONICO = {
  idle: 'reposo',
  working: 'programando',
  thinking: 'pensando',
  searching: 'depurando',
  question: 'permiso',
  approval: 'permiso',
  error: 'error',
  finished: 'terminado',
  sleeping: 'dormido',
  dizzy: 'mareado',
  ratelimit: 'mareado',
};

export function deEstado(estado) {
  return CANONICO[estado] || 'reposo';
}

export function estadoPara(id, canonico) {
  const e = MASCOTAS[id] && MASCOTAS[id].estados;
  if (!e) return 'reposo';
  if (e[canonico]) return canonico;
  if (canonico === 'programando' && e.trabajando) return 'trabajando';
  if (canonico === 'depurando' && e.pensando) return 'pensando';
  return 'reposo';
}

// Adornos de cada una: son los que salen en reposo, nunca mientras trabaja.
// En los HTML eran estados sueltos; aqui son florituras.
//
// Esta es la lista entera de lo que cada mascota traia programado para cuando
// no esta trabajando: volar y disparar el repulsor (Jarvi), la extension, el
// depurador y el cambio de tema (Vee), las pesas, los agentes, los Freebucks,
// el anuncio y el flex (Buffi). Dormir lo traian las cuatro.
//
// Compilar NO esta aqui a proposito: en los HTML es un estado de trabajo (sale
// el portatil con la barra de progreso), asi que se dispara mientras el agente
// trabaja, no en reposo. De eso se encarga programaCompilacion() en
// jarvis-mascotas.js.
export const TIENE = {
  jarvi: ['baile', 'vuelo', 'disparo', 'dormido'],
  vee: ['baile', 'extension', 'depurando', 'tema', 'dormido'],
  opi: ['baile', 'dormido'],
  buffi: ['baile', 'flex', 'pesas', 'freebucks', 'agentes', 'anuncio', 'dormido'],
};

// Las cuatro traen el estado `compilando` en su tabla; el que se ponga a
// compilar mientras trabaja lo decide jarvis-mascotas.js mirando si existe.