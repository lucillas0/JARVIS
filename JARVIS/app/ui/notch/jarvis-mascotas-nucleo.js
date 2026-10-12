// Mascotas de JARVIS para la isla de Coucou.
//
// Estas cuatro vienen de los HTML de mascota (Vee, Buffi, Opi y Jarvi) y se
// portan tal cual: mismos estados, mismos parametros y mismo dibujo en canvas.
// Lo unico que se anade es que los estados que en el HTML eran "cosas"
// (bailar, volar, disparar, levantar pesas) aqui se usan SOLO en reposo,
// porque no tiene sentido estar de guardia haciendo el Vuelta al PENEDO.
//
// Cada mascota guarda sus propios valores (P) y sus propias fases (ph), de
// forma que cuatro pueden convivir en el mismo lienzo sin pisarse.

// ── Nucleo comun ─────────────────────────────────────────────────────────────
// Interpolacion de resorte entre estados, fases animadas y particulas.

function creaMotor(def, estados, pintar) {
  const P = {}, V = {}, T = {};
  const HK = { rX: 1, rY: 1, lX: 1, lY: 1, hn: 1, hm: 1, lp: 1, bb: 1 };
  for (const k in def) { P[k] = def[k]; V[k] = 0; T[k] = def[k]; }
  const ph = { s: 0, b: 0, r: 0, d: 0, w: 0, c: 0 };
  const sp = [];
  let estado = 'reposo', t = 0, ultimo = 0, proxParpadeo = 2, parpadeo = -9;

  function pon(n) {
    if (!estados[n]) n = 'reposo';
    estado = n;
    const s = estados[n];
    for (const k in def) T[k] = def[k];
    Object.assign(T, s);
    V.bp = (V.bp || 0) + 6;
    return estado;
  }

  function avanza(ms) {
    const dt = Math.min(0.05, (ms - ultimo) / 1000 || 0.016);
    ultimo = ms;
    t = ms / 1000;
    for (const k in def) {
      const hk = HK[k], kh = k === 'h';
      const rigidez = kh ? 60 : hk ? 95 : 140;
      const freno = kh ? 12 : hk ? 10 : 15;
      V[k] += (T[k] - P[k]) * rigidez * dt;
      V[k] *= Math.exp(-freno * dt);
      P[k] += V[k] * dt;
    }
    ph.s += dt * P.swS;
    ph.b += dt * P.bobS;
    ph.r += dt * (P.rr === undefined ? 0 : P.rr);
    ph.d += dt * 9;
    ph.w += dt * (3 + P.dw * 1.5);
    ph.c += dt * (1 + P.cd * 2);
    if (t > proxParpadeo) { parpadeo = t; proxParpadeo = t + 2 + Math.random() * 3; }
    for (const p of sp) {
      p.x += p.vx * dt; p.y += p.vy * dt; p.vy += 90 * dt;
      p.l -= dt * (p.d || 1.6);
    }
    sp.splice(0, sp.length, ...sp.filter((p) => p.l > 0));
    return { dt, t, ph, P, sp, estado };
  }

  function dibuja(c, W, H, st, mirar) {
    st.ancho = W;
    c.clearRect(0, 0, W, H);
    c.save();
    // Coucou no centra la mascota en la caja: la caja lleva 24 px de margen a
    // cada lado y 40 de voladizo arriba, y el cuerpo va al 79 % de la altura.
    // Si se dibuja centrado y del tamano de la caja, la mascota asoma por
    // debajo del panel en vez de quedar dentro, que es lo que pasaba.
    const ref = st.ref || Math.min(W, H);
    const cy = st.cy != null ? st.cy : H / 2;
    c.translate(W / 2, cy);
    // Zoom: a 26 px el dibujo entero es una mancha y no se sabe que mascota es.
    // Con zoom se acerca a la cara, que es lo que la identifica: los ojos, la
    // pantalla y los colores bastan para reconocerla.
    const z = st.zoom || 1;
    c.scale((ref / 200) * z, (ref / 200) * z);
    if (z !== 1) c.translate(0, st.zoomY || 0);
    pintar(c, ref / 320, st, mirar);
    c.restore();
  }

  return {
    pon,
    estado: () => estado,
    estados,
    P: () => P,
    ph: () => ph,
    avanza,
    dibuja,
    particulas: sp,
    salta() { ultimo = 0; },
    reinicia() {
      for (const k in def) { P[k] = def[k]; V[k] = 0; T[k] = def[k]; }
      sp.length = 0;
    },
  };
}

// Utilidades de dibujo compartidas por las cuatro.
const cl = (v) => Math.max(-1, Math.min(1, v));
const G = (g, ...s) => { s.forEach((v, i) => g.addColorStop(i / (s.length - 1), v)); return g; };
const hx = (s) => [1, 3, 5].map((i) => parseInt(s.slice(i, i + 2), 16));
const mixC = (a, b, k) => {
  const A = hx(a), B = hx(b);
  return `rgb(${A.map((v, i) => Math.round(v + (B[i] - v) * k)).join(',')})`;
};
// Silueta de "squircle": la forma redondeada que comparten todas.
function sq(c, a, b) {
  c.beginPath();
  for (let i = 0; i <= 72; i++) {
    const th = (i / 72) * 6.2832, u = Math.cos(th), v = Math.sin(th);
    c.lineTo(a * Math.sign(u) * Math.sqrt(Math.abs(u)), b * Math.sign(v) * Math.sqrt(Math.abs(v)));
  }
  c.closePath();
}

// Manitas: el trazo del brazo con el guante de cada mascota. Las cuatro las
// dibujan igual salvo el color, asi que se comparte.
function manitas(c, P, ph, t, colores, extra) {
  const sy = Math.sin(ph.s), w2 = Math.sin(ph.s + 1.2);
  const lag = Math.sin(ph.b - 0.7) * 2;
  const R = [P.rX + Math.sin(ph.s) * P.hwx, P.rY + w2 * P.hwy + lag];
  const Lh = [P.lX - Math.sin(ph.s) * P.hwx, P.lY - w2 * P.hwy + lag];
  const bbm = Math.min(1, P.bb === undefined ? 0 : P.bb);
  if (bbm > 0.02) {
    // Barra de pesas: sube y baja mientras levanta.
    const barY = 44 - (Math.sin(ph.s) * 0.5 + 0.5) * 98;
    R[0] = P.rX * (1 - bbm) + 20 * bbm + Math.sin(ph.s) * P.hwx;
    Lh[0] = P.lX * (1 - bbm) - 20 * bbm - Math.sin(ph.s) * P.hwx;
    R[1] = (P.rY + w2 * P.hwy + lag) * (1 - bbm) + barY * bbm;
    Lh[1] = (P.lY - w2 * P.hwy + lag) * (1 - bbm) + barY * bbm;
  }
  return { R, Lh, bbm, barY: 44 - (Math.sin(ph.s) * 0.5 + 0.5) * 98 };
}

function brazo(c, sx, x, y, vis, lado, v, colores, bulto) {
  if (vis < 0.03) return;
  const vv = Math.min(1.2, vis);
  const cx = (sx + x) / 2 + lado * 8;
  const cy = (14 + y) / 2 - 6 - (colores.fx || 0) * 8;
  c.save();
  c.lineCap = 'round';
  c.globalAlpha = Math.min(1, vis * 1.5);
  c.strokeStyle = colores.brazoOscuro;
  c.lineWidth = 10 * vv;
  c.beginPath(); c.moveTo(sx, 14); c.quadraticCurveTo(cx, cy, x, y); c.stroke();
  c.strokeStyle = colores.brazo;
  c.lineWidth = 7.4 * vv;
  c.stroke();
  // Musculo: se hincha cuando el estado es de fuerza.
  if (bulto > 0.02) {
    const bx = 0.25 * sx + 0.5 * cx + 0.25 * x;
    const by = 3.5 + 0.5 * cy + 0.25 * y;
    const rr = (4 + bulto * 4) * vv;
    c.fillStyle = colores.brazoClaro; c.strokeStyle = colores.brazoOscuro; c.lineWidth = 1.3;
    c.beginPath(); c.arc(bx, by, rr, 0, 7); c.fill(); c.stroke();
    c.fillStyle = 'rgba(255,255,255,.55)';
    c.beginPath(); c.arc(bx - 1.5, by - 2, rr * 0.35, 0, 7); c.fill();
  }
  c.fillStyle = G(c.createRadialGradient(x - 2, y - 3, 1, x, y, 9), '#ffffff', colores.mano, colores.manoBorde);
  c.beginPath(); c.arc(x, y, 6.8 * vv, 0, 7); c.fill();
  c.strokeStyle = colores.brazoOscuro; c.lineWidth = 1.3; c.stroke();
  c.restore();
}

// Globo con el simbolo del estado, arriba a la izquierda.
function globo(c, W, h, P, ph, sy, saltitos, estadoDef, bobA, hop, baseY) {
  if (P.bs <= 0.02) return;
  c.save();
  c.translate(W / 2, W / 2);
  c.scale(W / 200, W / 200);
  c.translate(-47, (baseY === undefined ? -44 : baseY) + Math.sin(ph.b) * bobA * 0.5 - hop * 0.6);
  const bs = Math.max(0, P.bs) * (1 + saltitos * 0.18);
  c.scale(bs, bs);
  c.fillStyle = `hsl(${h},${estadoDef.globoS},${estadoDef.globoL}%)`;
  c.strokeStyle = '#fff'; c.lineWidth = 2.4;
  c.beginPath(); c.arc(0, 0, 12.5, 0, 7); c.fill(); c.stroke();
  c.fillStyle = c.strokeStyle = '#fff';
  const sb = estadoDef.sy;
  if (sb === 'dots') {
    for (let i = -1; i < 2; i++) {
      c.beginPath(); c.arc(i * 5, Math.sin(ph.w + i * 0.9) * P.dw * 0.6, 1.7, 0, 7); c.fill();
    }
  } else {
    c.font = 'bold 15px system-ui,sans-serif'; c.textAlign = 'center'; c.textBaseline = 'middle';
    c.fillText(sb, 0, 1);
  }
  c.restore();
}

// Ojos: los cuatro los dibujan casi igual (elipses que se cierran al parpadear,
// o curva al sonreir, o X al fallar). Jarvi es el unico que los quiere un pelo
// mas grandes y con el puntito de brillo, asi que eso entra por `opciones`.
function ojos(c, P, ph, t, lx, ly, ee, mirilla, color, separacion, transformada, opciones) {
  const o = opciones || {};
  const rx = o.rx === undefined ? 3.4 : o.rx;
  const ry = o.ry === undefined ? 6 : o.ry;
  const grosor = o.lw === undefined ? 2.4 : o.lw;
  const dzx = Math.cos(ph.d * 1.3) * 2.5 * P.dz;
  const dzy = Math.sin(ph.d * 1.3) * 2.5 * P.dz;
  c.save();
  c.shadowBlur = 9 * (transformada || 1);
  // Jarvi dibuja los ojos con las puntas redondeadas; las demas lo dejan como
  // lo tuvieran puesto (Opi ya viene con lineCap redondo de la diadema).
  if (o.redondo) c.lineCap = 'round';
  c.strokeStyle = c.fillStyle = color;
  c.lineWidth = grosor;
  for (const s of [-1, 1]) {
    c.save();
    c.translate(s * (separacion - Math.abs(lx) * 1.5) + lx * 5 + dzx, -3 + ly * 3 + dzy);
    c.rotate(-s * P.an * 0.4);
    const a0 = Math.max(0, 1 - P.hp - P.xx);
    if (a0 > 0.01) {
      c.globalAlpha = a0;
      c.beginPath(); c.ellipse(0, 0, rx, ry * ee, 0, 0, 7); c.fill();
      if (o.brillo) {
        c.fillStyle = '#fff';
        c.globalAlpha = a0 * 0.85;
        c.beginPath(); c.arc(1, -2 * ee, 1.2, 0, 7); c.fill();
        c.fillStyle = color;
      }
    }
    if (P.hp > 0.01) {
      c.globalAlpha = P.hp;
      c.beginPath(); c.arc(0, 3, 5, 3.5, 5.9); c.stroke();
    }
    if (P.xx > 0.01) {
      c.globalAlpha = P.xx;
      c.beginPath();
      c.moveTo(-4, -4); c.lineTo(4, 4); c.moveTo(4, -4); c.lineTo(-4, 4);
      c.stroke();
    }
    c.restore();
  }
  c.restore();
}

// Mofletes. Vee los quiere en (21, 8) de 4.6x2.6 y Jarvi mas arriba y algo
// mas gordos, en (21, 6) de 5x3; por eso la posicion y el tamano entran por
// parametro en vez de ir fijos.
function mofletes(c, alpha, color, cy, rx, ry) {
  c.save();
  c.shadowBlur = 0;
  c.fillStyle = `rgba(${color || '255,120,140'},${alpha})`;
  const y = cy === undefined ? 8 : cy;
  const a = rx === undefined ? 4.6 : rx;
  const b = ry === undefined ? 2.6 : ry;
  for (const s of [-1, 1]) {
    c.beginPath(); c.ellipse(s * 21, y, a, b, 0, 0, 7); c.fill();
  }
  c.restore();
}

export { creaMotor, cl, G, mixC, sq, manitas, brazo, globo, ojos, mofletes };