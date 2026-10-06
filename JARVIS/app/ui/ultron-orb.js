/* Orbe Ultron (portado de SAGAR-TAMANG/ultron-by-sagar-builds, lib/orbScene.ts)
   a JavaScript vanilla. Se carga como ES module vía import map (three + addons
   se sirven desde vendor/, copiados de app/node_modules/three r160). */
import * as THREE from 'three';
import { OrbitControls } from 'three/addons/controls/OrbitControls.js';
import { EffectComposer } from 'three/addons/postprocessing/EffectComposer.js';
import { RenderPass } from 'three/addons/postprocessing/RenderPass.js';
import { UnrealBloomPass } from 'three/addons/postprocessing/UnrealBloomPass.js';
import { ShaderPass } from 'three/addons/postprocessing/ShaderPass.js';

const HOME_POSITION = new THREE.Vector3(0, 0.5, 4.6);
const MIN_DISTANCE = 0.6;
const MAX_DISTANCE = 40;

export function createOrbScene(container, opts = {}) {
  const width = container.clientWidth || window.innerWidth || 300;
  const height = container.clientHeight || window.innerHeight || 300;
  const transparent = opts.transparent === true;
  const lite = opts.lite === true; // orbe compacto de bajo coste (overlay, flotante)

  // ——— SCENE ———
  const scene = new THREE.Scene();
  const camera = new THREE.PerspectiveCamera(55, width / height, 0.1, 500);
  camera.position.copy(HOME_POSITION);

  const renderer = new THREE.WebGLRenderer({
    antialias: true,
    alpha: transparent,
    premultipliedAlpha: false,
  });
  renderer.setSize(width, height);
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, lite ? 1.25 : 1.5));
  renderer.toneMapping = THREE.ACESFilmicToneMapping;
  renderer.toneMappingExposure = 0.9;
  if (transparent) {
    renderer.setClearColor(0x000000, 0);
  } else {
    scene.background = new THREE.Color(0x000000);
  }
  container.appendChild(renderer.domElement);

  // ——— POST PROCESSING ———
  // El compositor (bloom + aberración cromática) pinta alpha=1 en la salida final,
  // destruyendo la transparencia. En modo transparente se renderiza directo al
  // canvas para que el fondo sea transparente de verdad (PNG-like).
  let composer = null;
  let bloom = null;
  let chromaticPass = null;

  if (!transparent) {
    composer = new EffectComposer(renderer);
    composer.addPass(new RenderPass(scene, camera));

    bloom = new UnrealBloomPass(
      new THREE.Vector2(width, height),
      2.1,   // strength
      0.45,  // radius
      0.15,  // threshold
    );
    composer.addPass(bloom);

    // Chromatic aberration + color grade shader (del repo)
    const chromaticShader = {
      uniforms: {
        tDiffuse: { value: null },
        uTime: { value: 0 },
        uIntensity: { value: 0.003 },
      },
      vertexShader: `
        varying vec2 vUv;
        void main() {
          vUv = uv;
          gl_Position = projectionMatrix * modelViewMatrix * vec4(position, 1.0);
        }
      `,
      fragmentShader: `
        uniform sampler2D tDiffuse;
        uniform float uTime;
        uniform float uIntensity;
        varying vec2 vUv;
        void main() {
          vec2 dir = vUv - vec2(0.5);
          float d = length(dir);
          float offset = uIntensity * d;
          float flicker = 1.0 + 0.02 * sin(uTime * 30.0) * sin(uTime * 7.3);
          vec4 cr = texture2D(tDiffuse, vUv + dir * offset);
          vec4 cg = texture2D(tDiffuse, vUv);
          vec4 cb = texture2D(tDiffuse, vUv - dir * offset * 0.5);
          gl_FragColor = vec4(cr.r, cg.g * 1.05, cb.b * 0.6, 1.0) * flicker;
          gl_FragColor.rgb = mix(gl_FragColor.rgb, gl_FragColor.rgb * vec3(1.15, 0.85, 0.55), 0.3);
        }
      `,
    };
    chromaticPass = new ShaderPass(chromaticShader);
    composer.addPass(chromaticPass);
  }

  // Controls
  const controls = new OrbitControls(camera, renderer.domElement);
  controls.enabled = !transparent;
  controls.enableDamping = true;
  controls.dampingFactor = 0.04;
  controls.minDistance = MIN_DISTANCE;
  controls.maxDistance = MAX_DISTANCE;
  controls.zoomSpeed = 1.4;
  controls.enablePan = false;

  // ——— COLORS ———
  // Azul acero desaturado estilo JARVIS (sin neón: gris-azul frío).
  const C_BRIGHT = 0x5da6cc;
  const C_MID = 0x3c7ea3;
  const C_DIM = 0x28566f;
  const C_FAINT = 0x16374a;
  const C_HOT = 0x8fc9e0;

  // ——— ORB ROOT ———
  const orbGroup = new THREE.Group();
  scene.add(orbGroup);

  function lineMat(color, opacity = 1) {
    return new THREE.LineBasicMaterial({
      color,
      transparent: true,
      opacity,
      blending: THREE.AdditiveBlending,
      depthWrite: false,
    });
  }

  function latRing(radius, lat, segs = 120) {
    const r = radius * Math.cos(lat);
    const y = radius * Math.sin(lat);
    const pts = [];
    for (let i = 0; i <= segs; i++) {
      const a = (i / segs) * Math.PI * 2;
      pts.push(new THREE.Vector3(r * Math.cos(a), y, r * Math.sin(a)));
    }
    return new THREE.BufferGeometry().setFromPoints(pts);
  }

  function meridian(radius, lon, segs = 120) {
    const pts = [];
    for (let i = 0; i <= segs; i++) {
      const lat = (i / segs) * Math.PI - Math.PI / 2;
      pts.push(
        new THREE.Vector3(
          radius * Math.cos(lat) * Math.cos(lon),
          radius * Math.sin(lat),
          radius * Math.cos(lat) * Math.sin(lon),
        ),
      );
    }
    return new THREE.BufferGeometry().setFromPoints(pts);
  }

  // LAYER 1: OUTER SHELL
  const outerShell = new THREE.Group();
  const R1 = 2.0;

  for (let i = -15; i <= 15; i++) {
    const lat = (i / 15) * (Math.PI / 2) * 0.95;
    const opacity = i % 3 === 0 ? 0.5 : 0.12;
    const color = i % 3 === 0 ? C_MID : C_FAINT;
    outerShell.add(new THREE.Line(latRing(R1, lat), lineMat(color, opacity)));
  }

  for (let i = 0; i < 24; i++) {
    const lon = (i / 24) * Math.PI * 2;
    const isMajor = i % 6 === 0;
    outerShell.add(
      new THREE.Line(
        meridian(R1, lon),
        lineMat(isMajor ? C_MID : C_FAINT, isMajor ? 0.6 : 0.1),
      ),
    );
  }

  const CROSS_LINES = 18;
  const CROSS_SPREAD = 0.25;
  for (let i = 0; i < 4; i++) {
    const lon = (i / 4) * Math.PI * 2;
    for (let j = 0; j < CROSS_LINES; j++) {
      const t = (j / (CROSS_LINES - 1)) * 2 - 1;
      const offset = (t * CROSS_SPREAD) / 2;
      const falloff = 1 - Math.abs(t) * 0.7;
      const opacity = 0.85 * falloff;
      const color = Math.abs(t) < 0.3 ? C_BRIGHT : C_MID;
      outerShell.add(
        new THREE.Line(meridian(R1, lon + offset, 200), lineMat(color, opacity)),
      );
    }
  }

  const EQ_LINES = 20;
  const EQ_SPREAD = 0.35;
  for (let j = 0; j < EQ_LINES; j++) {
    const t = (j / (EQ_LINES - 1)) * 2 - 1;
    const offset = (t * EQ_SPREAD) / 2;
    const falloff = 1 - Math.abs(t) * 0.65;
    const opacity = 0.8 * falloff;
    const color = Math.abs(t) < 0.3 ? C_BRIGHT : C_MID;
    outerShell.add(
      new THREE.Line(latRing(R1, offset, 200), lineMat(color, opacity)),
    );
  }
  orbGroup.add(outerShell);

  // LAYER 2: GRID PANELS
  const panelGroup = new THREE.Group();

  function createSpherePanel(latCenter, lonCenter, latSpan, lonSpan, radius, divisions = 4) {
    const group = new THREE.Group();
    const mat = lineMat(C_DIM, 0.25);
    for (let i = 0; i <= divisions; i++) {
      const lat = latCenter - latSpan / 2 + (i / divisions) * latSpan;
      const pts = [];
      for (let j = 0; j <= divisions * 4; j++) {
        const lon = lonCenter - lonSpan / 2 + (j / (divisions * 4)) * lonSpan;
        pts.push(
          new THREE.Vector3(
            radius * Math.cos(lat) * Math.cos(lon),
            radius * Math.sin(lat),
            radius * Math.cos(lat) * Math.sin(lon),
          ),
        );
      }
      group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), mat));
    }
    for (let j = 0; j <= divisions; j++) {
      const lon = lonCenter - lonSpan / 2 + (j / divisions) * lonSpan;
      const pts = [];
      for (let i = 0; i <= divisions * 4; i++) {
        const lat = latCenter - latSpan / 2 + (i / (divisions * 4)) * latSpan;
        pts.push(
          new THREE.Vector3(
            radius * Math.cos(lat) * Math.cos(lon),
            radius * Math.sin(lat),
            radius * Math.cos(lat) * Math.sin(lon),
          ),
        );
      }
      group.add(new THREE.Line(new THREE.BufferGeometry().setFromPoints(pts), mat));
    }
    return group;
  }

  for (let i = 0; i < 30; i++) {
    const lat = (Math.random() - 0.5) * Math.PI * 0.8;
    const lon = Math.random() * Math.PI * 2;
    const size = 0.15 + Math.random() * 0.25;
    const panel = createSpherePanel(
      lat, lon, size, size, R1 + 0.01, 3 + Math.floor(Math.random() * 3),
    );
    panelGroup.add(panel);
  }
  orbGroup.add(panelGroup);

  // LAYER 3: SECONDARY SHELL
  const shell2 = new THREE.Group();
  const R2 = 2.12;

  for (let i = 0; i < 16; i++) {
    const lat = (Math.random() - 0.5) * Math.PI * 0.85;
    const startLon = Math.random() * Math.PI * 2;
    const arcLen = 0.3 + Math.random() * 1.2;
    const pts = [];
    const segs = 60;
    const r = R2 * Math.cos(lat);
    const y = R2 * Math.sin(lat);
    for (let j = 0; j <= segs; j++) {
      const a = startLon + (j / segs) * arcLen;
      pts.push(new THREE.Vector3(r * Math.cos(a), y, r * Math.sin(a)));
    }
    shell2.add(
      new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(pts),
        lineMat(C_MID, 0.2 + Math.random() * 0.3),
      ),
    );
  }

  for (let i = 0; i < 12; i++) {
    const lon = Math.random() * Math.PI * 2;
    const startLat = (Math.random() - 0.5) * Math.PI * 0.8;
    const arcLen = 0.3 + Math.random() * 0.8;
    const pts = [];
    const segs = 40;
    for (let j = 0; j <= segs; j++) {
      const lat = startLat + (j / segs) * arcLen;
      pts.push(
        new THREE.Vector3(
          R2 * Math.cos(lat) * Math.cos(lon),
          R2 * Math.sin(lat),
          R2 * Math.cos(lat) * Math.sin(lon),
        ),
      );
    }
    shell2.add(
      new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(pts),
        lineMat(C_DIM, 0.15 + Math.random() * 0.2),
      ),
    );
  }
  orbGroup.add(shell2);

  // LAYER 4: INNER CORE (espiral geodésica)
  const innerCore = new THREE.Group();
  const R3 = 0.9;

  for (let s = 0; s < 8; s++) {
    const pts = [];
    const turns = 3 + Math.random() * 2;
    const segs = 300;
    const phase = (s / 8) * Math.PI * 2;
    for (let i = 0; i <= segs; i++) {
      const t = i / segs;
      const lat = t * Math.PI - Math.PI / 2;
      const lon = t * turns * Math.PI * 2 + phase;
      pts.push(
        new THREE.Vector3(
          R3 * Math.cos(lat) * Math.cos(lon),
          R3 * Math.sin(lat),
          R3 * Math.cos(lat) * Math.sin(lon),
        ),
      );
    }
    innerCore.add(
      new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(pts),
        lineMat(C_BRIGHT, 0.3 + Math.random() * 0.2),
      ),
    );
  }

  for (let i = -6; i <= 6; i++) {
    const lat = (i / 6) * (Math.PI / 2) * 0.9;
    innerCore.add(new THREE.Line(latRing(R3, lat, 80), lineMat(C_DIM, 0.2)));
  }

  for (let i = 0; i < 12; i++) {
    const lon = (i / 12) * Math.PI * 2;
    innerCore.add(new THREE.Line(meridian(R3, lon, 80), lineMat(C_DIM, 0.15)));
  }
  orbGroup.add(innerCore);

  // LAYER 5: CORE MÁS ÍNTIMO
  const coreR = 0.25;
  const icoGeo = new THREE.IcosahedronGeometry(coreR, 1);
  const icoEdges = new THREE.EdgesGeometry(icoGeo);
  const icoWireMat = lineMat(C_HOT, 0.9);
  const icoWire = new THREE.LineSegments(icoEdges, icoWireMat);
  orbGroup.add(icoWire);

  const coreSphereMat = new THREE.MeshBasicMaterial({
    color: C_HOT,
    transparent: true,
    opacity: 0.15,
    blending: THREE.AdditiveBlending,
  });
  const coreSphere = new THREE.Mesh(new THREE.SphereGeometry(0.15, 16, 16), coreSphereMat);
  orbGroup.add(coreSphere);

  const glowSphereMat = new THREE.MeshBasicMaterial({
    color: C_MID,
    transparent: true,
    opacity: 0.04,
    blending: THREE.AdditiveBlending,
  });
  const glowSphere = new THREE.Mesh(new THREE.SphereGeometry(0.5, 16, 16), glowSphereMat);
  orbGroup.add(glowSphere);

  // CODE TEXT
  const codeSnippets = [
    'sys.init()', '0xFF3A', 'malloc()', '>> SCAN', 'void*', 'ACK',
    'SYNC OK', 'ptr_ref', 'exec()', 'hash256', '::bind', 'core.0',
    '01101001', '10110100', '>>> RDY', 'HEAP 4K', 'TCP/SYN',
    'mutex.lk', 'IRQ 0x7', 'DMA xfer', 'REG EAX', 'FAULT 0',
    'kernel.d', 'pipe |>', 'chmod +x', 'fork()', 'SIGTERM',
    'eth0: UP', 'AES-256', 'RSA 4096', 'TLS 1.3', 'HTTP/2',
    'latency', '200 OK', 'PATCH /', 'fn main', 'use std',
    'impl Orb', 'async {}', 'spawn()', 'arc::new', '.unwrap',
  ];

  function makeTextSprite(text, size = 0.08) {
    const c = document.createElement('canvas');
    c.width = 256;
    c.height = 32;
    const ctx = c.getContext('2d');
    ctx.font = 'bold 14px Courier New';
    const alpha = 0.35 + Math.random() * 0.55;
    ctx.fillStyle = `rgba(${(90 + Math.random() * 60) | 0}, ${(150 + Math.random() * 40) | 0}, ${(195 + Math.random() * 35) | 0}, ${alpha})`;
    ctx.textAlign = 'center';
    ctx.textBaseline = 'middle';
    ctx.fillText(text, 128, 16);
    const tex = new THREE.CanvasTexture(c);
    tex.minFilter = THREE.LinearFilter;
    const s = new THREE.Sprite(
      new THREE.SpriteMaterial({
        map: tex,
        transparent: true,
        blending: THREE.AdditiveBlending,
        depthWrite: false,
      }),
    );
    s.scale.set(size * 5, size * 0.7, 1);
    return s;
  }

  function scatterText(count, sizeFn, rFn, speedScale) {
    const group = new THREE.Group();
    for (let i = 0; i < count; i++) {
      const sp = makeTextSprite(
        codeSnippets[Math.floor(Math.random() * codeSnippets.length)],
        sizeFn(),
      );
      const phi = Math.acos(2 * Math.random() - 1);
      const theta = Math.random() * Math.PI * 2;
      const r = rFn();
      sp.position.set(
        r * Math.sin(phi) * Math.cos(theta),
        r * Math.cos(phi),
        r * Math.sin(phi) * Math.sin(theta),
      );
      sp.userData = {
        phi, theta, r,
        speed: (speedScale[0] + Math.random() * speedScale[1]) * (Math.random() > 0.5 ? 1 : -1),
      };
      group.add(sp);
    }
    return group;
  }

  const textOuter = scatterText(
    lite ? 360 : 1200, () => 0.04 + Math.random() * 0.04,
    () => R1 + 0.03 + Math.random() * 0.08, [0.0002, 0.0008],
  );
  orbGroup.add(textOuter);

  const textInner = scatterText(
    lite ? 58 : 100, () => 0.03 + Math.random() * 0.03,
    () => R3 + 0.02, [0.0005, 0.001],
  );
  orbGroup.add(textInner);

  const textAmbient = scatterText(
    lite ? 160 : 400, () => 0.03,
    () => R3 + 0.2 + Math.random() * (R1 - R3 - 0.3), [0.0003, 0.0006],
  );
  orbGroup.add(textAmbient);

  // ORBITING DEBRIS
  const debrisGeos = [
    new THREE.IcosahedronGeometry(0.012, 0),
    new THREE.IcosahedronGeometry(0.02, 0),
    new THREE.IcosahedronGeometry(0.03, 1),
    new THREE.IcosahedronGeometry(0.008, 0),
    new THREE.TetrahedronGeometry(0.015, 0),
    new THREE.OctahedronGeometry(0.018, 0),
  ];
  const debris = [];
  const debrisCount = lite ? 56 : 250;
  for (let i = 0; i < debrisCount; i++) {
    const geo = debrisGeos[Math.floor(Math.random() * debrisGeos.length)];
    const mat = new THREE.MeshBasicMaterial({
      color: Math.random() > 0.7 ? C_BRIGHT : C_MID,
      transparent: true,
      opacity: 0.3 + Math.random() * 0.6,
      blending: THREE.AdditiveBlending,
    });
    const mesh = new THREE.Mesh(geo, mat);
    const orbitR = 1.2 + Math.random() * 4.0;
    const speed = (0.08 + Math.random() * 0.6) * (Math.random() > 0.5 ? 1 : -1);
    const tiltX = (Math.random() - 0.5) * Math.PI * 0.9;
    const tiltZ = (Math.random() - 0.5) * Math.PI * 0.5;
    const phase = Math.random() * Math.PI * 2;
    mesh.userData = { orbitR, speed, tiltX, tiltZ, phase };
    debris.push(mesh);
    orbGroup.add(mesh);

    if (Math.random() > 0.85) {
      const trailPts = [];
      for (let j = 0; j <= 15; j++) {
        const a = -(j / 15) * 0.3;
        trailPts.push(
          new THREE.Vector3(
            orbitR * Math.cos(a + phase),
            orbitR * 0.08 * Math.sin(a * 3),
            orbitR * Math.sin(a + phase),
          ),
        );
      }
      const trail = new THREE.Line(
        new THREE.BufferGeometry().setFromPoints(trailPts),
        lineMat(C_FAINT, 0.08),
      );
      mesh.add(trail);
    }
  }

  // DUST
  const dustCount = lite ? 600 : 2000;
  const dustPos = new Float32Array(dustCount * 3);
  for (let i = 0; i < dustCount; i++) {
    const rr = 0.5 + Math.pow(Math.random(), 0.6) * 7;
    const theta = Math.random() * Math.PI * 2;
    const phi = Math.acos(2 * Math.random() - 1);
    dustPos[i * 3] = rr * Math.sin(phi) * Math.cos(theta);
    dustPos[i * 3 + 1] = rr * Math.cos(phi);
    dustPos[i * 3 + 2] = rr * Math.sin(phi) * Math.sin(theta);
  }
  const dustGeo = new THREE.BufferGeometry();
  dustGeo.setAttribute('position', new THREE.Float32BufferAttribute(dustPos, 3));

  const dotC = document.createElement('canvas');
  dotC.width = dotC.height = 64;
  const dCtx = dotC.getContext('2d');
  const g = dCtx.createRadialGradient(32, 32, 0, 32, 32, 32);
  g.addColorStop(0, 'rgba(93,166,204,1)');
  g.addColorStop(0.2, 'rgba(60,126,163,0.6)');
  g.addColorStop(0.5, 'rgba(40,86,115,0.15)');
  g.addColorStop(1, 'rgba(22,55,74,0)');
  dCtx.fillStyle = g;
  dCtx.fillRect(0, 0, 64, 64);

  const dustMat = new THREE.PointsMaterial({
    map: new THREE.CanvasTexture(dotC),
    size: 0.04,
    transparent: true,
    opacity: 0.5,
    blending: THREE.AdditiveBlending,
    depthWrite: false,
    sizeAttenuation: true,
    color: C_BRIGHT,
  });
  const dustPoints = new THREE.Points(dustGeo, dustMat);
  orbGroup.add(dustPoints);

  // SCANNING RINGS
  function makeScanRing(radius, thickness = 0.015) {
    const geo = new THREE.RingGeometry(radius - thickness, radius + thickness, 120);
    const mat = new THREE.MeshBasicMaterial({
      color: C_BRIGHT,
      transparent: true,
      opacity: 0,
      blending: THREE.AdditiveBlending,
      side: THREE.DoubleSide,
      depthWrite: false,
    });
    const mesh = new THREE.Mesh(geo, mat);
    mesh.rotation.x = Math.PI / 2;
    return mesh;
  }
  const scanRing1 = makeScanRing(R1, 0.01);
  const scanRing2 = makeScanRing(R1 * 0.7, 0.008);
  orbGroup.add(scanRing1, scanRing2);

  // TALK RINGS: anillos que se expanden cuando JARVIS habla, como ondas de voz.
  const TALK_RINGS = 4;
  const talkRings = [];
  for (let i = 0; i < TALK_RINGS; i++) {
    const ring = new THREE.Mesh(
      new THREE.RingGeometry(R3, R3 + 0.02, 64),
      new THREE.MeshBasicMaterial({
        color: C_HOT,
        transparent: true,
        opacity: 0,
        blending: THREE.AdditiveBlending,
        side: THREE.DoubleSide,
        depthWrite: false,
      }),
    );
    ring.userData = { life: 1 };
    ring.visible = false;
    talkRings.push(ring);
    orbGroup.add(ring);
  }
  function spawnTalkRing() {
    const r = talkRings.find((x) => x.userData.life >= 1 && !x.visible);
    if (!r) return;
    r.userData.life = 0;
    r.visible = true;
  }
  function advanceTalkRings(dt) {
    for (const r of talkRings) {
      const u = r.userData;
      if (u.life >= 1) continue;
      u.life += dt * (1.6 + level * 1.6);
      const k = Math.min(1, u.life);
      const ease = 1 - Math.pow(1 - k, 3);
      const rad = R3 * 0.6 + ease * (R1 * 1.3 - R3 * 0.6);
      r.scale.setScalar(rad / R3);
      r.material.opacity = Math.max(0, 0.55 * (1 - k));
      r.quaternion.copy(camera.quaternion);
      if (k >= 1) r.visible = false;
    }
  }

  // HEXAGONAL NODES
  for (let i = 0; i < 15; i++) {
    const phi = Math.acos(2 * Math.random() - 1);
    const theta = Math.random() * Math.PI * 2;
    const r = R1 + 0.02;
    const hexGeo = new THREE.CircleGeometry(0.03 + Math.random() * 0.02, 6);
    const hexEdges = new THREE.EdgesGeometry(hexGeo);
    const hex = new THREE.LineSegments(hexEdges, lineMat(C_MID, 0.5));
    hex.position.set(
      r * Math.sin(phi) * Math.cos(theta),
      r * Math.cos(phi),
      r * Math.sin(phi) * Math.sin(theta),
    );
    hex.lookAt(0, 0, 0);
    outerShell.add(hex);
  }

  // CAMERA API
  const sphericalScratch = new THREE.Spherical();
  const offsetScratch = new THREE.Vector3();

  function rotateBy(deltaTheta, deltaPhi) {
    offsetScratch.copy(camera.position).sub(controls.target);
    sphericalScratch.setFromVector3(offsetScratch);
    sphericalScratch.theta -= deltaTheta;
    sphericalScratch.phi = THREE.MathUtils.clamp(
      sphericalScratch.phi - deltaPhi, 0.05, Math.PI - 0.05,
    );
    sphericalScratch.makeSafe();
    offsetScratch.setFromSpherical(sphericalScratch);
    camera.position.copy(controls.target).add(offsetScratch);
    camera.lookAt(controls.target);
  }

  function zoomBy(factor) {
    offsetScratch.copy(camera.position).sub(controls.target);
    const dist = THREE.MathUtils.clamp(
      offsetScratch.length() * factor, MIN_DISTANCE, MAX_DISTANCE,
    );
    offsetScratch.setLength(dist);
    camera.position.copy(controls.target).add(offsetScratch);
  }

  function resetView() {
    camera.position.copy(HOME_POSITION);
    controls.target.set(0, 0, 0);
    camera.lookAt(controls.target);
    controls.update();
  }

  // ANIMATION
  const clock = new THREE.Clock();
  let flickerTimer = 0;
  let rafId = 0;
  let disposed = false;
  let level = 0; // nivel de audio (0..1) para reaccionar al habla, vía setLevel()
  let active = false; // modo 'active' (escuchando/pensando/hablando) acelera el orbe
  let speaking = false; // modo 'speaking': JARVIS está hablando, el orbe "habla" con ondas
  let compact = false; // orbe flotante pequeño de reposo
  let lastT = 0;
  let liteSkip = false;

  function animate() {
    if (disposed) return;
    rafId = requestAnimationFrame(animate);
    // En modo lite y en reposo ~30 fps: alivia mucho el hilo principal.
    if (lite && !speaking && !active) {
      liteSkip = !liteSkip;
      if (liteSkip) return;
    }
    const t = clock.getElapsedTime();
    const dt = lastT ? t - lastT : 0.016;
    lastT = t;
    const speed = active ? 3 : 1;
    const boost = active ? 1 + level : 0;
    const speakPulse = speaking ? (0.35 + level * 0.9) : 0;

    outerShell.rotation.y += 0.0015 * speed;
    outerShell.rotation.x = Math.sin(t * 0.08) * 0.05;

    panelGroup.rotation.y += 0.0018 * speed;
    panelGroup.rotation.x = Math.sin(t * 0.08 + 0.5) * 0.04;

    shell2.rotation.y -= 0.001 * speed;
    shell2.rotation.z = Math.sin(t * 0.12) * 0.03;

    innerCore.rotation.y -= 0.005 * speed;
    innerCore.rotation.z += 0.002 * speed;
    innerCore.rotation.x = Math.cos(t * 0.1) * 0.08;

    icoWire.rotation.x += 0.008 * speed;
    icoWire.rotation.y += 0.012 * speed;

    const wave1 = Math.sin(t * 1.2);
    const wave3 = Math.pow(Math.max(0, Math.sin(t * 0.4)), 5);
    const wave4 = Math.pow(Math.max(0, Math.sin(t * 0.7 + 2)), 8);
    const fadeOut = Math.pow(Math.max(0, Math.sin(t * 0.25)), 3);
    const surge = wave3 * 1.5 + wave4 * 2.0 + boost;
    const coreScale = (1 + surge + Math.sin(t * 5) * 0.05) * (1 + speakPulse * 0.6);
    coreSphere.scale.setScalar(coreScale);
    const coreOpacity = Math.max(
      0,
      (0.08 + wave1 * 0.05 + surge * 0.2) * (1 - fadeOut * 0.95) + level * 0.25 + speakPulse * 0.3,
    );
    coreSphereMat.opacity = Math.min(0.65, coreOpacity);
    glowSphere.scale.setScalar((1 + surge * 0.8) * (1 + speakPulse * 0.9));
    glowSphereMat.opacity = Math.max(0, (0.03 + surge * 0.08) * (1 - fadeOut * 0.9) + level * 0.1 + speakPulse * 0.12);
    icoWire.scale.setScalar((1 + surge * 0.6) * (1 + speakPulse * 0.7));
    icoWireMat.opacity = Math.min(1, 0.5 + surge * 0.4 + level * 0.3 + speakPulse * 0.45);
    innerCore.scale.setScalar(1 + (speaking ? Math.sin(t * 7) * 0.035 * (0.5 + level) : 0));

    debris.forEach((d) => {
      const u = d.userData;
      const a = t * u.speed * speed + u.phase;
      d.position.set(
        u.orbitR * Math.cos(a) * Math.cos(u.tiltX),
        u.orbitR * Math.sin(u.tiltX) * Math.sin(a * 0.8) + Math.sin(a * 0.3 + u.tiltZ) * 0.2,
        u.orbitR * Math.sin(a) * Math.cos(u.tiltZ),
      );
      d.rotation.x += 0.015 * speed;
      d.rotation.z += 0.01 * speed;
    });

    const driftGroups = [
      [textOuter, 1],
      [textInner, 2],
      [textAmbient, 1.2],
    ];
    for (const [group, mult] of driftGroups) {
      group.children.forEach((sp) => {
        const u = sp.userData;
        u.theta += u.speed * mult * speed;
        sp.position.set(
          u.r * Math.sin(u.phi) * Math.cos(u.theta),
          u.r * Math.cos(u.phi),
          u.r * Math.sin(u.phi) * Math.sin(u.theta),
        );
      });
    }

    const scanY1 = Math.sin(t * 0.4 * speed) * R1;
    scanRing1.position.y = scanY1;
    const scanS1 = Math.sqrt(Math.max(0, R1 * R1 - scanY1 * scanY1)) / R1;
    scanRing1.scale.set(scanS1, scanS1, 1);
    scanRing1.material.opacity = (0.2 + boost * 0.2) * scanS1;

    const scanY2 = Math.sin(t * 0.6 * speed + 2) * R3;
    scanRing2.position.y = scanY2;
    const scanS2 = Math.sqrt(Math.max(0, R3 * R3 - scanY2 * scanY2)) / R3;
    scanRing2.scale.set(scanS2, scanS2, 1);
    scanRing2.material.opacity = (0.15 + boost * 0.15) * scanS2;

    dustPoints.rotation.y += 0.0002 * speed;

    flickerTimer += 0.016;
    if (flickerTimer > 0.1) {
      flickerTimer = 0;
      panelGroup.children.forEach((p) => {
        if (Math.random() > 0.95) p.visible = !p.visible;
      });
    }

    if (speaking) {
      if (level > 0.06) spawnTalkRing();
      advanceTalkRings(dt);
    } else {
      for (const r of talkRings) {
        r.visible = false;
        r.material.opacity = 0;
      }
    }

    if (bloom) {
      const compactBloom = compact ? 0.45 : 1;
      bloom.strength = (2.1 + Math.sin(t * 0.8) * 0.4 + level * 1.2 + (active ? 0.8 : 0) + speakPulse * 0.9) * compactBloom;
    }
    if (chromaticPass) chromaticPass.uniforms.uTime.value = t;

    controls.update();
    if (composer) composer.render();
    else renderer.render(scene, camera);
  }

  animate();

  // RESIZE
  function onResize() {
    const w = container.clientWidth || window.innerWidth || 0;
    const h = container.clientHeight || window.innerHeight || 0;
    if (!w || !h) return; // ventana oculta/minimizada: no tocar el compositor
    camera.aspect = w / h;
    camera.updateProjectionMatrix();
    renderer.setSize(w, h);
    if (composer) composer.setSize(w, h);
  }
  window.addEventListener('resize', onResize);

  // CLEANUP
  function dispose() {
    disposed = true;
    cancelAnimationFrame(rafId);
    window.removeEventListener('resize', onResize);
    controls.dispose();
    scene.traverse((obj) => {
      const mesh = obj;
      if (mesh.geometry) mesh.geometry.dispose();
      const mats = Array.isArray(mesh.material) ? mesh.material : [mesh.material];
      for (const mat of mats) {
        if (!mat) continue;
        if (mat.map) mat.map.dispose();
        mat.dispose();
      }
    });
    if (composer) composer.dispose();
    renderer.dispose();
    renderer.domElement.remove();
  }

  return {
    rotateBy,
    zoomBy,
    zoomIn: () => zoomBy(0.65),
    zoomOut: () => zoomBy(1.55),
    resetView,
    forceRender: () => {
      try {
        onResize();
        if (composer) composer.render();
        else renderer.render(scene, camera);
      } catch { /* contexto aún no listo */ }
    },
    setLevel: (v) => { level = Math.max(0, Math.min(1, Number(v) || 0)); },
    setMode: (mode) => {
      speaking = mode === 'speaking';
      active = speaking || mode === 'active';
      if (compact) {
        const s = active ? 1 : 1.45;
        orbGroup.scale.set(s, s, s);
      }
    },
    setCompact: (v) => {
      compact = !!v;
      if (compact) {
        const s = active ? 1 : 1.45;
        orbGroup.scale.set(s, s, s);
        camera.position.set(0, 0.5, 6.4);
      }
    },
    dispose,
  };
}