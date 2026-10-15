'use strict';
/* Mission Control · FASE A. Núcleo 3D con datos de mentira (demo:true) salvo
   actividad (del bus real) y tiempo encendido (de /health).
   Rendimiento: DPR topado, pausa con pestaña oculta, sondeo lento, sin
   sombras ni posprocesado. Sin OrbitControls: órbita propia en ~40 líneas
   para no traer los addons de Three. */
import * as THREE from 'three';

const $ = (id) => document.getElementById(id);
const token = typeof window.JARVIS_TOKEN === 'string' ? window.JARVIS_TOKEN : '';
const mqReduce = matchMedia('(prefers-reduced-motion: reduce)').matches;

const COLORES = {
  core: 0x8fd0ff, agent: 0x4fd6a0, container: 0x7cc7ff,
  service: 0xb9a7f2, hardware: 0xf0b45a, network: 0x8fd0ff,
};
const ETIQUETAS = {
  core: 'NÚCLEO', agent: 'AGENTE', container: 'CONTENEDOR',
  service: 'SERVICIO', hardware: 'HARDWARE', network: 'RED',
};

function announce(msg) {
  const n = $('notice');
  n.textContent = String(msg || '');
  n.hidden = false;
  clearTimeout(announce.t);
  announce.t = setTimeout(() => { n.hidden = true; }, 4200);
}
async function api(path) {
  if (!token) throw new Error('Sin sesión. Recarga la página.');
  const r = await fetch(path, { headers: { 'X-Jarvis-Token': token },
    cache: 'no-store', credentials: 'same-origin' });
  const d = await r.json().catch(() => ({}));
  if (!r.ok) throw new Error(d.error || `HTTP ${r.status}`);
  return d;
}
const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g,
  (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
const hora = (s) => new Intl.DateTimeFormat('es-ES',
  { hour: '2-digit', minute: '2-digit', second: '2-digit' }).format(new Date(s * 1000));

/* ── escena ── */
const canvas = $('nucleo');
const renderer = new THREE.WebGLRenderer({ canvas, antialias: true, alpha: true });
renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
const scene = new THREE.Scene();
const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 200);
const CAM0 = new THREE.Vector3(0, 9, 26);
camera.position.copy(CAM0);
scene.add(new THREE.AmbientLight(0xbfd4ea, 0.85));
const luz = new THREE.DirectionalLight(0xffffff, 1.4);
luz.position.set(6, 12, 8);
scene.add(luz);

function tamano() {
  const w = canvas.clientWidth || 800, h = canvas.clientHeight || 420;
  renderer.setSize(w, h, false);
  camera.aspect = w / h;
  camera.updateProjectionMatrix();
}
addEventListener('resize', tamano);

/* órbita propia: arrastrar rota, rueda acerca, mayús+arrastrar desplaza */
const orbita = { t: 0.6, p: 0.42, r: 26, cx: 0, cy: 2.5, cz: 0 };
function aplicaOrbita() {
  orbita.p = Math.max(0.08, Math.min(1.45, orbita.p));
  orbita.r = Math.max(9, Math.min(60, orbita.r));
  camera.position.set(
    orbita.cx + orbita.r * Math.sin(orbita.p) * Math.sin(orbita.t),
    orbita.cy + orbita.r * Math.cos(orbita.p),
    orbita.cz + orbita.r * Math.sin(orbita.p) * Math.cos(orbita.t));
  camera.lookAt(orbita.cx, orbita.cy, orbita.cz);
}
let drag = null;
canvas.addEventListener('pointerdown', (e) => {
  canvas.setPointerCapture(e.pointerId);
  drag = { x: e.clientX, y: e.clientY, pan: e.shiftKey };
});
canvas.addEventListener('pointermove', (e) => {
  if (!drag) return;
  const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
  drag.x = e.clientX; drag.y = e.clientY;
  if (drag.pan) { orbita.cx -= dx * 0.02; orbita.cy += dy * 0.02; }
  else { orbita.t -= dx * 0.006; orbita.p -= dy * 0.004; }
  aplicaOrbita();
});
canvas.addEventListener('pointerup', () => { drag = null; });
canvas.addEventListener('wheel', (e) => {
  e.preventDefault();
  orbita.r *= 1 + Math.sign(e.deltaY) * 0.09;
  aplicaOrbita();
}, { passive: false });

/* ── grafo ── */
const grupo = new THREE.Group();
scene.add(grupo);
const nodos = new Map();   // id -> {mesh, halo, datos, base}
const rayo = new THREE.Raycaster();
const puntero = new THREE.Vector2();
let malla = [];
let sel = null;

function esfera(radio, color, intenso) {
  const m = new THREE.Mesh(
    new THREE.SphereGeometry(radio, 28, 20),
    new THREE.MeshStandardMaterial({ color, roughness: 0.35, metalness: 0.55,
      emissive: color, emissiveIntensity: intenso }));
  return m;
}
function anillo(radio, color) {
  const m = new THREE.Mesh(
    new THREE.TorusGeometry(radio * 1.55, 0.035, 10, 64),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.5 }));
  m.rotation.x = Math.PI / 2.4;
  return m;
}
const POS = {
  jarvis: [0, 3, 0], 'ag-prog': [-7, 5.5, -2], 'ag-vig': [7, 5.5, -2],
  'c-jarvis': [-6, 0.5, 3], 'c-mem0': [-9.5, -2.5, 0], 'c-db': [-6, -5, -3],
  'c-media': [6, 0.5, 3], 'svc-asistente': [3.5, 4.5, 4.5], 'svc-memoria': [9.5, -2.5, 0],
  'hw-cpu': [0, -3.5, 5.5], 'hw-ram': [4, -5.5, 2], 'hw-disk': [-3.5, -6.5, -4.5],
  'net-router': [0, 1.5, -7], 'net-nas': [5, -1, -8.5],
};
function construye(grafo) {
  while (grupo.children.length) {
    const c = grupo.children.pop();
    c.traverse?.((o) => { o.geometry?.dispose?.(); });
  }
  nodos.clear(); malla = [];
  for (const n of grafo.nodos) {
    const p = POS[n.id] || [(Math.random() - 0.5) * 16, (Math.random() - 0.5) * 8, (Math.random() - 0.5) * 10];
    const radio = n.tipo === 'core' ? 1.5 : 0.62;
    const color = COLORES[n.tipo] ?? 0x9fb0c7;
    const m = esfera(radio, color, n.tipo === 'core' ? 0.55 : 0.3);
    m.position.set(...p);
    m.userData.nodo = n.id;
    grupo.add(m);
    let halo = null;
    if (n.tipo === 'core') { halo = anillo(radio, color); halo.position.copy(m.position); grupo.add(halo); }
    nodos.set(n.id, { mesh: m, halo, datos: n, base: radio });
    malla.push(m);
  }
  const pts = [];
  for (const [a, b] of grafo.enlaces) {
    const A = nodos.get(a), B = nodos.get(b);
    if (!A || !B) continue;
    pts.push(A.mesh.position.clone(), B.mesh.position.clone());
  }
  const g = new THREE.BufferGeometry().setFromPoints(pts);
  grupo.add(new THREE.LineSegments(g,
    new THREE.LineBasicMaterial({ color: 0x3b4c66, transparent: true, opacity: 0.75 })));
  particulas(grafo.enlaces.length);
  tamano();
}

/* partículas sobre las conexiones: flujo de actividad */
let nube = null, velPart = [];
function particulas(n) {
  if (nube) { grupo.remove(nube); nube.geometry.dispose(); nube = null; }
  const total = Math.min(240, n * 14);
  const pos = new Float32Array(total * 3);
  velPart = [];
  const ids = [...nodos.keys()];
  for (let i = 0; i < total; i++) {
    const a = nodos.get(ids[i % ids.length]).mesh.position;
    velPart.push({ o: a.clone(), d: new THREE.Vector3((Math.random() - 0.5) * 2, (Math.random() - 0.5) * 2, (Math.random() - 0.5) * 2), t: Math.random() });
  }
  const g = new THREE.BufferGeometry();
  g.setAttribute('position', new THREE.BufferAttribute(pos, 3));
  nube = new THREE.Points(g, new THREE.PointsMaterial({ color: 0x8fd0ff, size: 0.09, transparent: true, opacity: 0.8 }));
  grupo.add(nube);
}

/* selección */
function panelNodo(id) {
  const n = nodos.get(id);
  const p = $('node-panel');
  if (!n) { p.hidden = true; sel = null; return; }
  sel = id;
  $('node-kind').textContent = ETIQUETAS[n.datos.tipo] || n.datos.tipo;
  $('node-title').textContent = n.datos.nombre;
  $('node-detail').textContent = `${n.datos.detalle || ''} · Estado: ${n.datos.estado}`;
  $('node-metrics').innerHTML = Object.entries(n.datos.metricas || {})
    .map(([k, v]) => `<dt>${esc(k)}</dt><dd>${esc(v)}</dd>`).join('')
    || '<dt>—</dt><dd>Sin métricas en el prototipo</dd>';
  p.hidden = false;
}
canvas.addEventListener('click', (e) => {
  const r = canvas.getBoundingClientRect();
  puntero.set(((e.clientX - r.left) / r.width) * 2 - 1, -((e.clientY - r.top) / r.height) * 2 + 1);
  rayo.setFromCamera(puntero, camera);
  const hit = rayo.intersectObjects(malla, false)[0];
  panelNodo(hit ? hit.object.userData.nodo : null);
});
$('node-close').addEventListener('click', () => panelNodo(null));
addEventListener('keydown', (e) => {
  if (e.key === 'Escape') { panelNodo(null); paraTour(); }
});

/* recorrido cinematográfico, cancelable */
let tour = null;
const TOUR = [
  ['jarvis', 'Núcleo JARVIS'], ['ag-prog', 'Agentes'],
  ['c-jarvis', 'Docker'], ['svc-memoria', 'Servicios'],
  ['net-router', 'Red'], ['c-db', 'Almacenamiento'], ['hw-cpu', 'Hardware'],
];
function paraTour() {
  if (!tour) return;
  clearTimeout(tour); tour = null;
  $('tour-bar').hidden = true;
  announce('Recorrido detenido.');
}
$('btn-tour-stop').addEventListener('click', paraTour);
$('btn-tour').addEventListener('click', () => {
  if (tour || mqReduce) { if (mqReduce) announce('Recorrido desactivado con movimiento reducido.'); return; }
  let i = 0;
  $('tour-bar').hidden = false;
  const paso = () => {
    if (i >= TOUR.length) { paraTour(); aplicaOrbita(); return; }
    const [id, etiqueta] = TOUR[i];
    $('tour-step').textContent = `${i + 1}/${TOUR.length} · ${etiqueta}`;
    const n = nodos.get(id);
    if (n) {
      const p = n.mesh.position;
      orbita.cx += (p.x - orbita.cx) * 0.55;
      orbita.cy += ((p.y + 1) - orbita.cy) * 0.55;
      orbita.cz += (p.z - orbita.cz) * 0.55;
      orbita.r = Math.max(9, orbita.r * 0.82);
      panelNodo(id);
    }
    i += 1;
    tour = setTimeout(paso, 2600);
  };
  paso();
});

/* presentación: solo núcleo + salud */
$('btn-present').addEventListener('click', () => {
  const on = document.body.classList.toggle('presentacion');
  $('btn-present').setAttribute('aria-pressed', String(on));
  $('btn-present').textContent = on ? 'Salir de presentación' : 'Presentación';
  tamano();
});

/* ── bucle ── */
let visible = true, t0 = performance.now();
document.addEventListener('visibilitychange', () => {
  visible = document.visibilityState === 'visible';
  if (visible) { t0 = performance.now(); tamano(); }
});
const reloj = new THREE.Clock();
function cuadro() {
  requestAnimationFrame(cuadro);
  if (!visible) return;
  const dt = Math.min(reloj.getDelta(), 0.1);
  const t = (performance.now() - t0) / 1000;
  if (!mqReduce) {
    grupo.rotation.y += dt * 0.05;
    for (const [, n] of nodos) {
      const s = 1 + Math.sin(t * 1.6 + n.mesh.position.x) * 0.05;
      n.mesh.scale.setScalar(n.base === 1.5 ? 1 + Math.sin(t * 1.1) * 0.03 : s);
      if (n.halo) n.halo.rotation.z += dt * 0.4;
    }
    if (nube) {
      const p = nube.geometry.attributes.position;
      for (let i = 0; i < velPart.length; i++) {
        const q = velPart[i];
        q.t += dt * 0.5;
        if (q.t > 1) { q.t = 0; q.o.copy(nodos.get([...nodos.keys()][i % nodos.size]).mesh.position); }
        p.setXYZ(i, q.o.x + q.d.x * q.t, q.o.y + q.d.y * q.t, q.o.z + q.d.z * q.t);
      }
      p.needsUpdate = true;
    }
  }
  renderer.render(scene, camera);
}

/* ── datos: grafo mock + salud y actividad reales ── */
function fmtTiempo(s) {
  s = Math.max(0, Math.floor(s));
  const d = Math.floor(s / 86400), h = Math.floor((s % 86400) / 3600), m = Math.floor((s % 3600) / 60);
  return d ? `${d}d ${String(h).padStart(2, '0')}h` : h ? `${h}h ${String(m).padStart(2, '0')}m` : `${m} min`;
}
async function refresca() {
  try {
    const [g, h, ev] = await Promise.all([
      api('/api/mission/graph'), api('/health'),
      api('/api/live/events?after=0').catch(() => ({ events: [] })),
    ]);
    if (malla.length !== g.nodos.length) construye(g);
    $('g-state').textContent = 'En línea';
    $('g-dot').className = 'g-punto on';
    $('g-health').textContent = '98%';
    $('g-containers').textContent = `${g.nodos.filter((n) => n.tipo === 'container' && n.estado === 'online').length}/${g.nodos.filter((n) => n.tipo === 'container').length}`;
    $('g-uptime').textContent = fmtTiempo((Date.now() / 1000) - (h.uptime_started ?? Date.now() / 1000 - 1));
    const hw = Object.fromEntries(g.nodos.filter((n) => n.tipo === 'hardware').map((n) => [n.nombre, n.metricas?.valor ?? null]));
    for (const [id, v] of [['hw-cpu', hw.CPU], ['hw-ram', hw.RAM], ['hw-disk', hw.Almacenamiento]]) {
      $(`${id}-bar`).style.width = v == null ? '0%' : `${v}%`;
      $(`${id}-v`).textContent = v == null ? '—' : `${v}%`;
    }
    const st = $('stream');
    st.innerHTML = (ev.events || []).slice(-14).reverse().map((e) =>
      `<div class="flujo-fila"><time>${esc(hora(e.at))}</time><p>${esc(e.title || '')}</p></div>`).join('')
      || '<div class="flujo-fila"><p>Sin actividad reciente del cerebro.</p></div>';
    $('last-updated').textContent = `Última lectura: ${hora(Date.now() / 1000)}`;
  } catch (e) {
    $('g-state').textContent = 'Desconectado';
    $('g-dot').className = 'g-punto mal';
    if (!$('stream').children.length) announce(`${e.message} Reintentando.`);
  }
  try {
    const s = await api('/api/mission/sentinel');
    $('sentinel-state').textContent = s.estado === 'estable' ? 'Sistema estable' : s.estado;
    $('g-anomalies').textContent = String(s.anomalias ?? 0);
    $('sentinel-list').innerHTML = Object.entries(s.sistemas || {})
      .map(([k, v]) => `<li><b>${esc(k)}</b> · ${esc(v)}</li>`).join('');
  } catch { /* el prototipo sigue con el núcleo aunque falle */
  }
}

aplicaOrbita();
tamano();
cuadro();
refresca();
setInterval(() => { if (visible) void refresca(); }, 10000);
