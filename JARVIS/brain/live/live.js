'use strict';
/* JARVIS Live · centro de mando.
 *
 * Un solo WebSocket, un solo token, una sola fuente de verdad: el backend de
 * /api/cmd y /api/dev. Aquí no se calcula nada que el backend no haya medido.
 *
 * Regla de refresco: el bus avisa, el REST manda. Cuando llega un evento se
 * repinta lo afectado; el resto llega por sondeo corto mientras haya algo en
 * marcha y se alarga cuando la pantalla está tranquila.
 */
(() => {
  const $ = (id) => document.getElementById(id);
  const token = typeof window.JARVIS_TOKEN === 'string' ? window.JARVIS_TOKEN : '';

  const S = {
    conectado: false, ws: null, reintento: null,
    estado: null, act: null, per: null, ag: null, voz: null,
    vista: 'dashboard', tab: 'propuesta',
    diff: '', cargando: false, repetir: false,
    errores: 0, ultimo: 0, incidente: null,
    cache: {},
  };

  const ICONO_VACIO = '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="2"/><path d="M8 9h8M8 13h5"/></svg>';

  /* ───────────────────────── utilidades ───────────────────────── */
  const esc = (v) => String(v == null ? '' : v).replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
  const num = (v) => (typeof v === 'number' && Number.isFinite(v) ? v : null);
  const ts = (v) => (typeof v === 'number' && v > 0 ? v * 1000
    : (typeof v === 'string' && v ? Date.parse(v) : NaN));

  function dur(seg) {
    const s = Math.max(0, Math.floor(Number(seg) || 0));
    const h = Math.floor(s / 3600), m = Math.floor((s % 3600) / 60);
    if (h) return `${String(h).padStart(2, '0')}:${String(m).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`;
    if (m) return `${m} min ${String(s % 60).padStart(2, '0')} s`;
    return `${s} s`;
  }
  function relojDe(valor) {
    const ms = ts(valor);
    if (!Number.isFinite(ms)) return '--:--';
    return new Intl.DateTimeFormat('es-ES', { hour: '2-digit', minute: '2-digit' }).format(ms);
  }
  function relativo(valor) {
    const ms = ts(valor);
    if (!Number.isFinite(ms)) return '—';
    const dif = Math.round((Date.now() - ms) / 1000);
    if (dif < 45) return 'ahora mismo';
    if (dif < 3600) return `hace ${Math.round(dif / 60)} min`;
    if (dif < 86400) return `hace ${Math.round(dif / 3600)} h`;
    return `hace ${Math.round(dif / 86400)} d`;
  }
  const nivel = (pct) => (pct >= 90 ? 'critico' : pct >= 75 ? 'aviso' : 'ok');
  const vacio = (titulo, nota) => `<div class="estado-vacio">${ICONO_VACIO}<b>${esc(titulo)}</b>`
    + (nota ? `<span>${esc(nota)}</span>` : '') + '</div>';

  async function api(ruta, opciones = {}) {
    const r = await fetch(ruta, {
      ...opciones,
      headers: { ...(opciones.body ? { 'Content-Type': 'application/json' } : {}),
        'X-Jarvis-Token': token, ...(opciones.headers || {}) },
      cache: 'no-store', credentials: 'same-origin',
    });
    let datos;
    try { datos = await r.json(); } catch { throw new Error(`El cerebro respondió con HTTP ${r.status}.`); }
    if (!r.ok || datos.ok === false) throw new Error(datos.error || `El cerebro respondió con HTTP ${r.status}.`);
    return datos;
  }
  const get = (ruta) => api(ruta);
  const post = (ruta, cuerpo) => api(ruta, { method: 'POST', body: JSON.stringify(cuerpo || {}) });
  const borrar = (ruta) => api(ruta, { method: 'DELETE' });

  let avisoTimer = null;
  function avisar(texto, malo = false) {
    const caja = $('aviso');
    if (!caja) return;
    caja.textContent = String(texto || '');
    caja.style.borderColor = malo ? 'var(--error)' : 'var(--acento)';
    caja.hidden = false;
    clearTimeout(avisoTimer);
    avisoTimer = setTimeout(() => { caja.hidden = true; }, malo ? 7000 : 4200);
  }

  /* ───────────────────────── navegación ───────────────────────── */
  function ir(vista, empujar = true) {
    const destino = document.querySelector(`.vista[data-vista="${vista}"]`);
    if (!destino) return;
    S.vista = vista;
    document.querySelectorAll('.vista').forEach((v) => v.classList.toggle('activa', v === destino));
    document.querySelectorAll('.nav-item[data-vista]').forEach((b) => {
      if (b.dataset.vista === vista) b.setAttribute('aria-current', 'page');
      else b.removeAttribute('aria-current');
    });
    if (empujar && location.hash.slice(1) !== vista) history.replaceState(null, '', `#${vista}`);
    document.getElementById('principal').scrollTop = 0;
    cargarVista(vista);
  }

  function cargarVista(vista) {
    if (vista === 'tareas') return pintarTareas();
    if (vista === 'agentes') return pintarAgentes();
    if (vista === 'cambios') return pintarCambios(true);
    if (vista === 'permisos') return pintarPermisos();
    if (vista === 'preguntas') return pintarPreguntas();
    if (vista === 'tests') return pintarTests();
    if (vista === 'historial') return pintarHistorial();
    if (vista === 'ia') return pintarIA();
    if (vista === 'sistema') return pintarSistema();
    if (vista === 'nueva') return pintarNueva();
    return undefined;
  }

  /* ───────────────────────── cabecera ───────────────────────── */
  function chip(rotulo, valor, extra, opts = {}) {
    const nivelChip = opts.nivel || (num(extra) == null ? 'no' : nivel(extra));
    const pista = num(extra) == null ? ''
      : `<span class="pista"><i style="width:${Math.min(100, Math.max(0, extra))}%"></i></span>`;
    return `<div class="metrica-chip" data-nivel="${nivelChip}">
      <span class="rotulo">${esc(rotulo)}</span>
      <span class="valor">${esc(valor)}</span>${pista}${opts.pie || ''}</div>`;
  }

  function pintarCabecera() {
    const e = S.estado;
    const r = (e && e.recursos) || {};
    const cpu = r.cpu || {};
    const ram = r.ram || {};
    const gpu = r.gpu || {};
    const red = r.red || {};
    const discos = Array.isArray(r.discos) ? r.discos : [];
    const disco = discos.reduce((a, b) => ((a && (b.porcentaje || 0) > (a.porcentaje || 0)) ? b : (a || b)), null);

    $('metricas').innerHTML = [
      chip('CPU', cpu.porcentaje == null ? 'sin datos' : `${Math.round(cpu.porcentaje)}%`,
        cpu.porcentaje, { pie: cpu.nucleos ? `<span class="velocidad">${cpu.nucleos} núcleos</span>` : '' }),
      chip('RAM', ram.porcentaje == null ? 'sin datos' : `${Math.round(ram.porcentaje)}%`,
        ram.porcentaje,
        { pie: ram.total_gb ? `<span class="velocidad">${ram.usado_gb}/${ram.total_gb} GB</span>` : '' }),
      chip('GPU', !gpu.available ? 'no disponible' : (gpu.usage == null ? 'sin lectura' : `${Math.round(gpu.usage)}%`),
      gpu.available ? gpu.usage : null,
      { pie: gpu.available ? `<span class="velocidad">${esc(gpu.name || '')}${gpu.vram_total_mb ? ` · VRAM ${Math.round(gpu.vram_used_mb)}/${Math.round(gpu.vram_total_mb)} MB` : ''}</span>`
        : `<span class="velocidad">${esc(gpu.reason || 'sin sensor')}</span>` }),
      chip('DISCO', disco && disco.porcentaje != null ? `${Math.round(disco.porcentaje)}%` : 'sin datos',
        disco ? disco.porcentaje : null,
        { pie: disco ? `<span class="velocidad">${esc(disco.etiqueta || disco.path || '')}</span>` : '' }),
      `<div class="metrica-chip" data-nivel="no">
        <span class="rotulo">RED</span>
        <span class="velocidad">${red.available
          ? (red.download_bps == null ? 'midiendo…'
            : `↓${(red.download_bps / 1048576).toFixed(1)} ↑${(red.upload_bps / 1048576).toFixed(1)} MB/s`)
          : 'no disponible'}</span>
        <span class="velocidad">${esc(red.note || red.reason || '')}</span></div>`,
    ].join('');

    const salud = (e && e.salud) || {};
    const caja = $('estado-global');
    const nombre = { online: 'EN LÍNEA', degraded: 'DEGRADADO', error: 'CON FALLOS', unknown: 'SIN DATOS' }[salud.estado] || 'SIN DATOS';
    caja.dataset.estado = salud.estado || 'unknown';
    $('estado-global-txt').textContent = nombre;
    $('marca-sub').textContent = salud.estado === 'online'
      ? 'Centro de mando · todo en marcha'
      : `${salud.cuentas ? `${salud.cuentas.degraded || 0} degradados, ` : ''}revisa Sistema`;

    const c = (e && e.contadores) || {};
    $('notis-contador').hidden = !c.notificaciones_sin_leer;
    $('notis-contador').textContent = String(c.notificaciones_sin_leer || 0);
    pintarContadoresNav(c);
  }

  function pintarContadoresNav(c) {
    const set = (nombre, valor, tono) => {
      const nodo = document.querySelector(`.contador[data-contador="${nombre}"]`);
      if (!nodo) return;
      nodo.hidden = !valor;
      nodo.textContent = String(valor || 0);
      if (tono) nodo.dataset.tono = tono; else delete nodo.dataset.tono;
    };
    set('tareas', c.misiones_vivas);
    set('permisos', c.permisos, c.permisos ? 'alerta' : '');
    set('preguntas', c.preguntas, c.preguntas ? 'alerta' : '');
    set('cambios', ((S.estado || {}).cambios || {}).total);
    const ia = S.ia || {};
    set('ia', ia.fallos || 0, (ia.fallos || 0) ? 'error' : '');
    const activos = ((S.ag || {}).agentes || []).filter((a) => a.activo).length;
    set('agentes', activos);
  }

  /* ───────────────────────── sidebar: notificaciones y voz ───────────────────────── */
  function pintarNotis() {
    const lista_ = ((S.estado || {}).notificaciones) || [];
    const menu = $('notis-menu');
    if (!menu) return;
    if (!S.notisAbierto) { menu.hidden = true; return; }
    menu.hidden = false;
    menu.innerHTML = `<header><b>Notificaciones</b>
        <button type="button" class="btn chico" data-notis-accion="leer-todas">Marcar leídas</button></header>
      <div class="notis-cuerpo">${lista_.length ? lista_.map((n) => `
        <article class="notis-item${n.seen ? '' : ' nueva'}" data-id="${esc(n.id)}">
          <div><b>${esc(n.title)}</b><small>${esc(n.body || '')}</small>
            <span class="notis-pie">${esc(n.category || '')} · ${relativo(n.created_at)}</span></div>
          <button type="button" class="btn chico" data-notis-accion="dismiss" data-id="${esc(n.id)}">Descartar</button>
        </article>`).join('') : vacio('Nada que requiere atención', 'Las decisiones y avisos de JARVIS aparecerán aquí.')}</div>`;
  }

  function pintarVoz() {
    const orb = $('orb-voz');
    const v = S.voz || {};
    const activo = v.voice_available ? (v.voice || 'inactivo') : 'inactivo';
    orb.dataset.estado = activo;
    $('voz-estado').textContent = !v.voice_available ? 'voz no disponible'
      : ({ idle: 'en espera', listening: 'escuchando', thinking: 'pensando', speaking: 'hablando' }[activo] || activo);
  }

  /* ───────────────────────── protagonista ───────────────────────── */
  function estadoVisual(estado) {
    const mapa = {
      DRAFT: ['BORRADOR', 'espera'], ANALYZING: ['ANALIZANDO', 'espera'],
      PLANNING: ['DISEÑANDO', 'espera'], WAITING_PLAN_APPROVAL: ['ESPERANDO TU APROBACIÓN', 'espera'],
      STARTING_OPENCODE: ['ABRIENDO SESIÓN', 'espera'], RUNNING: ['EJECUTANDO', 'ok'],
      TESTING: ['EJECUTANDO TESTS', 'ok'], WAITING_PERMISSION: ['ESPERANDO PERMISO', 'espera'],
      WAITING_USER: ['ESPERANDO DECISIÓN', 'espera'], COMPLETED: ['COMPLETADA', 'fin'],
      FAILED: ['CON PROBLEMAS', 'parada'], CANCELLED: ['DETENIDA', 'parada'],
      // Estos tres no estaban: salian en ingles crudo dentro de una
      // interfaz en espanol.
      PAUSED: ['PAUSADA', 'espera'],
      ERROR: ['CON ERROR', 'parada'],
      COMPLETED_WITH_WARNINGS: ['COMPLETADA CON AVISOS', 'ok'],
    };
    return mapa[estado] || [(estado || '—'), 'espera'];
  }

  function pintarHero() {
    const a = S.act || {};
    const hay = a.hay_tarea;
    $('heroi-titulo').textContent = hay ? (a.titulo || 'Tarea en marcha') : (a.titulo || 'JARVIS está disponible.');
    $('heroi-sub').textContent = hay ? (a.subtitulo || a.accion || '')
      : (a.detalle || 'Crea una tarea y JARVIS la analiza antes de tocar nada.');
    const [texto, tono] = estadoVisual(a.estado);
    $('heroi-estado').textContent = hay ? texto : 'DISPONIBLE';
    $('heroi-estado').dataset.estado = hay ? tono : 'espera';
    const mis = $('heroi-mision');
    mis.hidden = !a.id;
    mis.textContent = a.id || '';

    const barra = $('heroi-progreso');
    barra.hidden = num(a.progreso) == null;
    if (num(a.progreso) != null) {
      $('heroi-barra').style.width = `${Math.min(100, Math.max(0, a.progreso))}%`;
      $('heroi-pct').textContent = `${a.progreso}%`;
    }
    $('heroi-base').textContent = a.progreso_base || (hay ? '' : 'Sin tarea: no hay progreso que medir.');
    $('heroi-agente').textContent = a.agente || (hay ? '—' : 'JARVIS');
    const fases = Array.isArray(a.pasos) ? a.pasos : [];
    const actual = fases.find((f) => f.estado === 'EN_CURSO');
    $('heroi-fase').textContent = actual ? (actual.titulo || '—')
      : (hay ? (a.accion || estadoVisual(a.estado)[0]) : '—');
    $('heroi-tiempo').textContent = hay ? dur(a.tiempo) : '—';

    $('heroi-fases').innerHTML = fases.length ? fases.map((f) => `
      <div class="fase" data-estado="${esc(f.estado)}">
        <span class="marca" aria-hidden="true">✓</span>
        <span class="titulo-fase">${esc(f.titulo)}
          <span class="nota-fase">${esc(f.evidencia || '')}</span></span>
      </div>`).join('')
      : `<p class="vacio-mini">Sin plan con pasos: no hay fases que enseñar.</p>`;

    $('heroi-acciones').innerHTML = accionesMision(a);
  }

  function accionesMision(a) {
    if (!a.hay_tarea || !a.id) return '';
    const id = esc(a.id);
    const botones = [];
    if (a.estado === 'WAITING_PLAN_APPROVAL') {
      botones.push(`<button type="button" class="btn ok" data-mision="approve" data-id="${id}">Aprobar plan</button>`);
      botones.push(`<button type="button" class="btn" data-mision="revise" data-id="${id}">Modificar plan</button>`);
    }
    if (['RUNNING', 'TESTING', 'ANALYZING', 'PLANNING', 'STARTING_OPENCODE'].includes(a.estado)) {
      botones.push(`<button type="button" class="btn" data-mision="pause" data-id="${id}">Pausar</button>`);
      botones.push(`<button type="button" class="btn peligro" data-mision="cancel" data-id="${id}">Detener</button>`);
    }
    if (a.estado === 'PAUSED') {
      // El endpoint de reanudar existia y funcionaba, pero el boton no se
      // dibujaba, asi que una tarea pausada se quedaba pausada para siempre.
      botones.push(`<button type="button" class="btn ok" data-mision="resume" data-id="${id}">Reanudar</button>`);
      botones.push(`<button type="button" class="btn peligro" data-mision="cancel" data-id="${id}">Detener</button>`);
    }
    if (['FAILED', 'CANCELLED'].includes(a.estado)) {
      botones.push(`<button type="button" class="btn acento" data-mision="retry" data-id="${id}">Reintentar</button>`);
    }
    if (a.estado === 'DRAFT') {
      botones.push(`<button type="button" class="btn acento" data-mision="analyze" data-id="${id}">Analizar</button>`);
    }
    return botones.join('');
  }

  /* ───────────────────────── plan ───────────────────────── */
  function pintarPlan() {
    const a = S.act || {};
    const plan = a.plan || {};
    document.querySelectorAll('.pestana[data-plan]').forEach((t) => {
      t.setAttribute('aria-selected', String(t.dataset.plan === S.tab));
    });
    const cuerpo = $('plan-cuerpo');
    if (!a.hay_tarea) {
      cuerpo.innerHTML = vacio('No hay tarea', 'Crea una desde «Nueva tarea» y el plan aparecerá aquí.');
      $('plan-acciones').innerHTML = '';
      return;
    }
    if (S.tab === 'propuesta') {
      const arquitectura = Array.isArray(plan.arquitectura) ? plan.arquitectura : [];
      const comps = Array.isArray(plan.componentes) ? plan.componentes : [];
      const riesgos = Array.isArray(a.riesgos) ? a.riesgos : [];
      cuerpo.innerHTML = `
        <p style="margin:0;font-size:13.5px;color:var(--texto)">${esc(plan.objetivo || a.titulo || '')}</p>
        ${plan.analisis ? `<p class="nota">${esc(plan.analisis)}</p>` : ''}
        ${arquitectura.length ? `<h3 class="tarjeta-sub">Arquitectura propuesta</h3>
          <ul class="lista-simple">${arquitectura.map((x) => `<li>${esc(x)}</li>`).join('')}</ul>` : ''}
        ${comps.length ? `<h3 class="tarjeta-sub">Componentes</h3>
          <ul class="lista-simple">${comps.map((c) => `<li><b>${esc(c.nombre || '')}</b>
            ${c.ruta ? `<code>${esc(c.ruta)}</code>` : ''}${c.descripcion ? `<br><span class="nota">${esc(c.descripcion)}</span>` : ''}</li>`).join('')}</ul>` : ''}
        ${riesgos.length ? `<h3 class="tarjeta-sub">Riesgos previstos</h3>
          <div class="riesgos">${riesgos.map((r) => `
            <div class="riesgo" data-nivel="${esc((r.nivel || 'medio').toLowerCase())}">
              <div><b>${esc(r.titulo || '')}</b>
                ${r.impacto ? `<small>Qué se rompe: ${esc(r.impacto)}</small>` : ''}
                ${r.mitigacion ? `<small>Cómo se evita: ${esc(r.mitigacion)}</small>` : ''}</div>
            </div>`).join('')}</div>` : ''}
        <div class="fila-dato"><span>Prompt enviado a OpenCode</span><strong>${a.prompt_chars || 0} caracteres</strong></div>
        ${a.sesion ? `<div class="fila-dato"><span>Sesión</span><strong>${esc(a.sesion)}</strong></div>` : ''}`;
    } else {
      const pasos = Array.isArray(a.pasos) ? a.pasos : [];
      cuerpo.innerHTML = pasos.length ? `<ol class="plan-pasos">${pasos.map((p) => `
        <li data-estado="${esc(p.estado)}"><div>${esc(p.titulo)}
          ${p.detalle ? `<span class="nota-fase">${esc(p.detalle)}</span>` : ''}
          ${Array.isArray(p.archivos) && p.archivos.length
            ? `<span class="nota-fase">${esc(p.archivos.join(', '))}</span>` : ''}</div></li>`).join('')}</ol>
        <p class="nota">Un paso solo se marca como hecho si hay un fichero suyo tocado o tests ejecutados.</p>`
        : vacio('El plan no tiene pasos', 'JARVIS recibirá los pasos cuando OpenCode proponga el diseño.');
    }
    const botones = [];
    if (a.estado === 'WAITING_PLAN_APPROVAL') {
      botones.push(`<button type="button" class="btn ok" data-mision="approve" data-id="${esc(a.id)}">Aprobar plan</button>`);
      botones.push(`<button type="button" class="btn" data-mision="revise" data-id="${esc(a.id)}">Modificar plan</button>`);
    }
    $('plan-acciones').innerHTML = botones.join('');
  }

  /* ───────────────────────── cambios recientes ───────────────────────── */
  function pintarCambiosRecientes() {
    const a = S.act || {};
    const cambios = Array.isArray(a.cambios) ? a.cambios : [];
    $('cambios-lista').innerHTML = cambios.length ? cambios.map((c) => `
      <div class="linea" data-accion="${esc(c.action || 'modified')}">
        <span class="marca-cambio" aria-hidden="true">${esc(c.mark || '~')}</span>
        <span class="ruta">${esc(c.path)}</span>
        <span class="hora">${esc(c.at ? relativo(c.at) : '')}</span>
      </div>`).join('')
      : `<p class="vacio-mini">${a.hay_tarea ? 'Aún no hay ficheros modificados.' : 'Sin tarea en marcha.'}</p>`;

    const caja = $('caja-commit');
    const commit = a.commit || {};
    caja.hidden = !commit.texto;
    $('commit-texto').textContent = commit.texto || '';
    $('btn-commit').dataset.mision = a.id || '';
    $('btn-commit').dataset.mensaje = commit.texto || '';

    const t = a.tests || {};
    $('caja-tests').innerHTML = t.ran
      ? `<span class="ok">${t.passed == null ? '✓' : `${t.passed} correctos`}</span>
         <span class="ko">${t.failed == null ? '' : `${t.failed} fallos`}</span>
         <span class="nota" style="margin-left:auto">${esc(t.resumen || 'tests ejecutados')}</span>`
      : `<span class="nota">Tests: ${a.hay_tarea ? 'todavía no ejecutados en esta misión.' : 'sin misiones.'}</span>`;
  }

  /* ───────────────────────── actividad en tiempo real ───────────────────────── */
  const CLASE_EVENTO = {
    'opencode.completed': 'ok', 'opencode.test_finished': 'ok', 'opencode.permission_resolved': 'ok',
    'opencode.question_answered': 'ok', 'opencode.started': 'ok',
    'opencode.error': 'error', 'opencode.stopped': 'error',
    'opencode.permission_requested': 'aviso', 'opencode.question_requested': 'aviso',
    'opencode.test_started': 'aviso', 'opencode.file_changed': '',
  };
  const GLIFO_EVENTO = {
    'opencode.started': '▶', 'opencode.completed': '✓', 'opencode.error': '!',
    'opencode.permission_requested': '?', 'opencode.question_requested': '?',
    'opencode.file_changed': '~', 'opencode.test_started': 'T', 'opencode.test_finished': '✓',
    'opencode.tool_call': '·', 'opencode.output': '»',
  };

  function pintarTiempoReal() {
    const a = S.act || {};
    const eventos = Array.isArray(a.eventos) ? a.eventos : [];
    $('tiempo-real').innerHTML = eventos.length ? eventos.map((ev) => {
      const tipo = String(ev.type || '');
      const clase = CLASE_EVENTO[tipo] || '';
      const detalle = ev.detail || ev.text || ev.error || ev.path || tipo;
      return `<div class="linea-tiempo" data-clase="${clase}">
        <span class="hora">${esc(relojDe(ev.at))}</span>
        <span class="glifo" aria-hidden="true">${esc(GLIFO_EVENTO[tipo] || '·')}</span>
        <span class="cuerpo"><b>${esc(tipo.replace('opencode.', ''))}</b>
          <span class="detalle">${esc(String(detalle).slice(0, 220))}</span></span></div>`;
    }).join('') : vacio('Sin actividad todavía', 'Los pasos de OpenCode aparecerán aquí en directo.');
  }

  /* ───────────────────────── agentes ───────────────────────── */
  function agentesConEstado() {
    const lista_ = ((S.ag || {}).agentes) || [];
    const a = S.act || {};
    const trabajando = a.hay_tarea && ['RUNNING', 'TESTING', 'ANALYZING', 'PLANNING',
      'STARTING_OPENCODE'].includes(a.estado);
    return lista_.map((ag) => {
      let estado = ag.estado;
      if (ag.id === 'agent_opencode' && trabajando) estado = 'WORKING';
      else if (ag.id === 'agent_opencode' && a.hay_tarea
        && ['WAITING_PERMISSION', 'WAITING_USER'].includes(a.estado)) estado = 'WAITING';
      else if (ag.activo) estado = 'WORKING';
      return {
        ...ag, estado,
        detalle: ag.id === 'agent_opencode' && a.hay_tarea
          ? (a.accion || estadoVisual(a.estado)[0]) : (ag.descripcion || ''),
      };
    });
  }

  function pintarAgentes() {
    const lista_ = agentesConEstado();
    const html = lista_.length ? lista_.map((ag) => `
      <div class="fila-agente" data-estado="${esc(ag.estado)}">
        <span class="luz" aria-hidden="true"></span>
        <span><b>${esc(ag.nombre)}</b>
          <span class="detalle-agent">${esc(ag.detalle || '')}</span></span>
        <span class="chip">${esc({ WORKING: 'TRABAJANDO', IDLE: 'INACTIVO', WAITING: 'ESPERANDO', DISABLED: 'NO INSTALADO' }[ag.estado] || ag.estado)}</span>
      </div>`).join('')
      : vacio('Sin agentes detectados', 'JARVIS lista los programas que encuentra de verdad en el equipo.');
    $('agentes-lista').innerHTML = html;

    const todos = lista_.length ? lista_.map((ag) => `
      <article class="tarjeta" data-agente="${esc(ag.id)}" style="gap:8px">
        <header><h2>${esc(ag.nombre)}</h2>
          <span class="insignia-estado" data-s="${ag.activo ? 'ok' : 'no'}">${ag.activo ? 'ACTIVO' : 'INACTIVO'}</span></header>
        <p class="nota">${esc(ag.descripcion || '')}</p>
        <p class="nota">${esc(ag.ejecutable ? `Ejecutable: ${ag.ejecutable}` : 'Sin ejecutable en este equipo.')}</p>
        <p class="nota">${esc(ag.nota || '')}</p>
        <div class="acciones">
          ${ag.acciones && ag.acciones.start ? `<button type="button" class="btn chico acento" data-agente-start="${esc(ag.id)}">Abrir</button>` : ''}
          ${ag.acciones && ag.acciones.stop ? `<button type="button" class="btn chico" data-agente-stop="${esc(ag.id)}">Cerrar</button>` : ''}
        </div>
      </article>`).join('') : vacio('Sin agentes', 'No se ha detectado ningún programa de agente.');
    $('ag-lista').innerHTML = todos;
    pintarGestores();
  }

  /* El Agent Manager y el Resource Manager: cola, prioridad y límites. Todo lo
   * que sale de aquí viene del backend; si no está, se dice que no está. */
  function pintarGestores() {
    const cont = $('ag-gestores');
    if (!cont) return;
    const ag = S.agentesMgr;
    const rec = S.recursosMgr;
    if (!ag && !rec) {
      cont.innerHTML = '';
      return;
    }
    const cola = (ag && ag.cola && ag.cola.resumen) || {};
    const conc = (ag && ag.concurrencia) || {};
    const trabajos = (ag && ag.cola && ag.cola && ag.cola.historial) || [];
    const decisions = (rec && rec.decisiones) || [];
    const m = (rec && rec.metricas) || {};

    const filaRecurso = (nombre, it) => {
      if (!it) return '';
      if (!it.available) return `<div class="fila-agente" data-estado="WAITING">
        <span class="luz"></span><span><b>${esc(nombre)}</b>
        <span class="detalle-agent">${esc(it.motivo || 'no disponible')}</span></span></div>`;
      return `<div class="fila-agente" data-estado="IDLE">
        <span class="luz"></span><span><b>${esc(nombre)}</b>
        <span class="detalle-agent">${num(it.valor)}%</span></span></div>`;
    };

    cont.innerHTML = `
      <section class="bloque">
        <h3>Cola de agentes</h3>
        <p class="nota">Prioridad ${num(conc.maximo_configurado)} de tope;
          en marcha ahora <b>${num(conc.en_vuelo)}</b>,
          límite actual <b>${num(conc.limite_efectivo)}</b>.
          En cola: ${num(cola.en_cola)} · ejecutándose: ${num(cola.ejecutando)}
          · atascados: ${num(cola.atascado)} · fallidos: ${num(cola.fallo)}.</p>
        ${trabajos.length ? `<ul class="lista-simple">${trabajos.slice(0, 6).map((t) => `
          <li><b>${esc(t.id)}</b> · ${esc(t.agent_id)} ·
            <span class="chip">${esc(t.estado)}</span> ·
            prioridad ${num(t.prioridad)}
            ${t.mission_id ? ` · misión ${esc(t.mission_id)}` : ''}
            ${t.error ? ` · ${esc(t.error)}` : ''}
            ${t.estado === 'en_cola' || t.estado === 'ejecutando' ? `<button type="button" class="btn mini" data-job-cancelar="${esc(t.id)}">Cancelar</button>` : ''}
          </li>`).join('')}</ul>` : '<p class="nota">No hay trabajos en cola.</p>'}
      </section>
      ${rec ? `<section class="bloque">
        <h3>Recursos y límites</h3>
        <p class="nota">Nivel actual: <b>${esc(rec.nivel)}</b>. ${esc(rec.motivo || '')}</p>
        ${filaRecurso('CPU', m.cpu)}
        ${filaRecurso('Memoria', m.ram)}
        ${filaRecurso('Disco', m.disco)}
        <p class="nota">Concurrencia: ${num(conc.limite_efectivo)}${rec.degradado && rec.degradado.falta
          ? ` · sin: ${esc(rec.degradado.falta.join(', '))}` : ''}</p>
        ${decisions.length ? `<ul class="lista-simple">${decisions.slice(0, 4).map((d) => `
          <li>${esc(d.motivo)} <span class="chip">${esc(d.valor_anterior)} → ${esc(d.valor_nuevo)}</span></li>`).join('')}</ul>` : ''}
      </section>` : ''}`;
  }

  /* ───────────────────────── lateral derecho ───────────────────────── */
  function pintarLateral() {
    const per = S.per || {};
    const pendientes = Array.isArray(per.pendientes) ? per.pendientes : [];
    const incidentes = ((S.estado || {}).incidentes) || [];
    const atencion = [];
    pendientes.slice(0, 3).forEach((p) => atencion.push(`
      <div class="caja alerta">
        <h3>${p.origen === 'OpenCode' ? 'OpenCode pide permiso' : 'Acción protegida de JARVIS'}</h3>
        <p>${esc(p.accion || '')}</p>
        <div class="motivo">${esc(p.detalle || '')}</div>
        ${p.origen === 'OpenCode' && p.mision ? `<div class="acciones" style="padding-top:2px">
          <button type="button" class="btn chico peligro" data-permiso="reject" data-id="${esc(p.id)}" data-mision="${esc(p.mision)}">Denegar</button>
          <button type="button" class="btn chico ok" data-permiso="once" data-id="${esc(p.id)}" data-mision="${esc(p.mision)}">Permitir</button>
          <button type="button" class="btn chico" data-permiso="always" data-id="${esc(p.id)}" data-mision="${esc(p.mision)}">Siempre</button>
        </div>` : `<div class="acciones" style="padding-top:2px">
          <button type="button" class="btn chico" data-ir="permisos">Revisar</button></div>`}
      </div>`));
    incidentes.slice(0, 2).forEach((i) => atencion.push(`
      <div class="caja alerta">
        <h3>Incidente ${esc(i.severity || '')}</h3>
        <p>${esc(i.title || '')}</p>
        <div class="motivo">${esc(i.description || i.impact || '')}</div>
        <div class="acciones" style="padding-top:2px">
          <button type="button" class="btn chico ok" data-incidente="resolve" data-id="${esc(i.id)}">Marcar resuelto</button></div>
      </div>`));
    const pendientesDeDecision = ((S.estado || {}).contadores || {}).preguntas || 0;
    if (pendientesDeDecision) {
      const plural = pendientesDeDecision > 1 ? 's' : '';
      atencion.push('<div class="caja"><h3>' + pendientesDeDecision + ' pregunta'
        + plural + ' de OpenCode</h3>'
        + '<p>OpenCode necesita una decisi&oacute;n tuya para seguir.</p>'
        + '<div class="acciones" style="padding-top:2px">'
        + '<button type="button" class="btn chico acento" data-ir="preguntas">Responder</button>'
        + '</div></div>');
    }
    $('atencion-lista').innerHTML = atencion.length ? atencion.join('')
      : '<p class="vacio-mini">Nada requiere tu atención ahora mismo.</p>';

    const ambitos = Array.isArray(per.ambitos) ? per.ambitos : [];
    $('lateral-permisos').innerHTML = `
      <p class="vacio-mini">${(per.total_pendientes || 0)} pendiente(s) ahora mismo.</p>
      ${ambitos.length ? ambitos.map((a) => `
        <div class="permiso-linea" data-estado="${esc(a.estado)}">
          <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v18M3 12h18"/></svg>
          <span>${esc(a.ambito)}</span>
          <span class="estado">${esc({ permitido: 'Permitido', preguntar: 'Preguntar siempre', denegado: 'Denegado' }[a.estado] || a.estado)}</span>
        </div>`).join('')
        : '<p class="vacio-mini">Sin decisiones de permisos registradas.</p>'}`;

    pintarSistemaLateral();
    const notis = ((S.estado || {}).notificaciones) || [];
    $('lateral-historial').innerHTML = notis.length ? notis.slice(0, 6).map((n) => `
      <div class="linea">
        <span class="glifo-mini" aria-hidden="true">${n.seen ? '·' : '●'}</span>
        <span style="min-width:0"><b style="font-weight:600">${esc(n.title)}</b>
          <span class="detalle-agent">${esc(n.body || '')}</span></span>
        <span class="hora">${relativo(n.created_at)}</span>
      </div>`).join('') : '<p class="vacio-mini">Sin historial reciente.</p>';
  }

  function pintarSistemaLateral() {
    const r = ((S.estado || {}).recursos) || {};
    const medidor = (rotulo, pct) => {
      const valor = num(pct);
      const off = valor == null ? 138 : Math.round(138 - (Math.min(100, valor) / 100) * 138);
      return `<div class="medidor" data-nivel="${valor == null ? 'no' : nivel(valor)}"
        title="${esc(rotulo)}: ${valor == null ? 'no disponible' : `${Math.round(valor)}%`}">
        <svg viewBox="0 0 48 48" aria-hidden="true"><circle class="fondo" cx="24" cy="24" r="22"/>
          <circle class="valor-c" cx="24" cy="24" r="22" stroke-dasharray="138" stroke-dashoffset="${off}"/></svg>
        <b>${valor == null ? '—' : `${Math.round(valor)}%`}</b><span>${esc(rotulo)}</span></div>`;
    };
    $('lateral-medidores').innerHTML = [
      medidor('CPU', r.cpu && r.cpu.porcentaje),
      medidor('RAM', r.ram && r.ram.porcentaje),
      medidor('GPU', r.gpu && r.gpu.available ? r.gpu.usage : null),
      medidor('DISCO', (Array.isArray(r.discos) && r.discos.length)
        ? Math.max(...r.discos.map((d) => d.porcentaje || 0)) : null),
    ].join('');
    const disco = (Array.isArray(r.discos) && r.discos.length)
      ? r.discos.reduce((a, b) => ((b.porcentaje || 0) > (a.porcentaje || 0) ? b : a)) : null;
    const carga = (r.carga && r.carga.procesos) || 0;
    $('lateral-barras').innerHTML = `
      <div class="barra-recurso" data-nivel="${disco ? nivel(disco.porcentaje) : 'no'}">
        <div class="rotulo"><span>Almacenamiento${disco ? ` (${esc(disco.etiqueta || disco.path || '')})` : ''}</span>
          <span>${disco ? `${disco.usado_gb ?? '—'}/${disco.total_gb ?? '—'} GB` : 'no disponible'}</span></div>
        <span class="pista"><i style="width:${disco && disco.porcentaje != null ? disco.porcentaje : 0}%"></i></span></div>
      <div class="barra-recurso">
        <div class="rotulo"><span>Procesos</span><span>${carga || '—'}</span></div>
        <span class="pista"><i style="width:${carga ? Math.min(100, carga) : 0}%"></i></span></div>`;
  }

  /* ───────────────────────── vistas: nueva tarea ───────────────────────── */
  async function pintarNueva() {
    const misiones = ((S.estado || {}).misiones) || [];
    const caja = $('idea-historial');
    caja.hidden = !misiones.length;
    $('idea-lista').innerHTML = misiones.slice(0, 6).map((m) => `
      <div class="linea">
        <span class="marca-cambio" aria-hidden="true">·</span>
        <span style="min-width:0"><b style="font-weight:600">${esc((m.idea || '').slice(0, 70))}</b>
          <span class="detalle-agent">${esc(m.id)} · ${esc(estadoVisual(m.status)[0])} · ${relativo(m.created_at)}</span></span>
      </div>`).join('');
  }

  /* ───────────────────────── vistas: tareas programadas ───────────────────────── */
  async function pintarTareas() {
    const d = await get('/api/cmd/automatizaciones');
    const lista_ = Array.isArray(d.tareas) ? d.tareas : [];
    $('ta-lista').innerHTML = lista_.length ? lista_.map((t) => `
      <div class="linea">
        <span class="luz" aria-hidden="true" style="background:${t.paused ? 'var(--aviso)' : 'var(--ok)'}"></span>
        <span style="min-width:0"><b style="font-weight:600">${esc(t.nombre)}</b>
          <span class="detalle-agent">${esc(t.mensaje || '')}</span>
          <span class="detalle-agent">${t.estado} · ${t.en ? `en ${dur(t.en)}` : 'ahora'} ${t.repeat ? `· repite ${esc(t.repeat)}` : ''}</span></span>
        <span class="acciones" style="margin-left:auto;padding-top:0">
          ${t.paused
            ? `<button type="button" class="btn chico" data-auto="reanudar" data-id="${esc(t.id)}">Reanudar</button>`
            : `<button type="button" class="btn chico" data-auto="pausar" data-id="${esc(t.id)}">Pausar</button>`}
          <button type="button" class="btn chico" data-auto="correr" data-id="${esc(t.id)}">Correr</button>
          <button type="button" class="btn chico peligro" data-auto="borrar" data-id="${esc(t.id)}">Borrar</button>
        </span>
      </div>`).join('') : vacio('Sin tareas programadas', 'Programa una y JARVIS la ejecutará a su hora.');
  }

  /* ───────────────────────── vistas: agentes ───────────────────────── */
  /* La lista de agentes del dashboard y la vista completa comparten pintado. */

  /* ───────────────────────── vistas: cambios ───────────────────────── */
  async function pintarCambios(forzar = false) {
    const d = S.estado && S.estado.cambios && S.estado.cambios.repo
      ? S.estado.cambios : await get('/api/cmd/cambios');
    if (!d.repo) {
      $('cam-estado').innerHTML = vacio('No hay repositorio Git aquí',
        d.error || 'Los cambios se guardan solo si la carpeta es un repositorio.');
      $('cam-checkpoints').innerHTML = '';
      $('cam-diff').textContent = 'Sin repositorio no hay diff que enseñar.';
      return;
    }
    const cambios = Array.isArray(d.cambios) ? d.cambios : [];
    $('cam-estado').innerHTML = `
      <div class="fila-dato"><span>Rama</span><strong>${esc(d.rama || '—')}</strong></div>
      <div class="fila-dato"><span>Ficheros con cambios</span><strong>${d.total || 0}</strong></div>
      <div class="fila-dato"><span>Últimos commits</span><strong>${(d.ultimos_commits || []).length}</strong></div>
      <pre class="codigo" style="max-height:120px">${esc((d.ultimos_commits || []).join('\n') || 'sin commits')}</pre>
      ${cambios.length ? cambios.map((c) => `
        <div class="linea">
          <span class="marca-cambio" aria-hidden="true">${esc(c.marca || '~')}</span>
          <button type="button" class="ruta" style="text-align:left;cursor:pointer" data-diff="${esc(c.path)}">${esc(c.path)}</button>
        </div>`).join('') : '<p class="vacio-mini">Árbol limpio: no hay nada modificado.</p>'}`;
    if (forzar) await pintarDiff(S.diff || (cambios[0] ? cambios[0].path : ''));
    const cps = await get('/api/cmd/checkpoints');
    const filas = Array.isArray(cps.checkpoints) ? cps.checkpoints : [];
    $('cam-checkpoints').innerHTML = filas.length ? filas.map((c) => `
      <div class="linea">
        <span style="min-width:0"><b style="font-weight:600">${esc(c.note || 'Checkpoint')}</b>
          <span class="detalle-agent">${esc(c.id)} · ${c.files || 0} ficheros · ${relativo(c.created_at)}${c.dirty ? ' · con cambios sin confirmar' : ''}</span></span>
        <span class="acciones" style="margin-left:auto;padding-top:0">
          <button type="button" class="btn chico" data-cp="comparar" data-id="${esc(c.id)}">Comparar</button>
          <button type="button" class="btn chico" data-cp="restaurar" data-id="${esc(c.id)}">Restaurar</button>
          <button type="button" class="btn chico peligro" data-cp="borrar" data-id="${esc(c.id)}">Borrar</button>
        </span>
      </div>`).join('') : '<p class="vacio-mini">No hay puntos de retorno guardados.</p>';
  }

  async function pintarDiff(fichero) {
    const salida = $('cam-diff');
    if (!fichero) { salida.textContent = 'Elige un fichero para ver sus diferencias.'; S.diff = ''; return; }
    S.diff = fichero;
    salida.textContent = 'Leyendo el diff…';
    try {
      const d = await get(`/api/cmd/cambios/diff?fichero=${encodeURIComponent(fichero)}`);
      salida.innerHTML = (d.diff || d.patch || d.error || 'Sin diferencias.')
        .split('\n').map((l) => esc(l)
          .replace(/^(\+[^+]|\+)/, '<span class="mas">$1</span>')
          .replace(/^(-[^-]|-)/, '<span class="menos">$1</span>')
          .replace(/^(diff --git|index |--- |\+\+\+ |@@)/, '<span class="fichero">$1</span>')
          .replace(/^([^+\-@\s].*)$/, '$1')).join('\n');
    } catch (e) { salida.textContent = e.message; }
  }

  /* ───────────────────────── vistas: permisos ───────────────────────── */
  function pintarPermisos() {
    const per = S.per || {};
    const pend = Array.isArray(per.pendientes) ? per.pendientes : [];
    // Antes los botones solo se dibujaban si el permiso traía `mision`, que
    // es un dato de las misiones de OpenCode. Los permisos del propio JARVIS
    // (los del Risk Engine) salían en la lista SIN ninguna forma de
    // resolverlos, así que el panel no podía aprobar nada.
    //
    // Ahora se dibujan para los dos orígenes, y cada uno va a su endpoint:
    // los de JARVIS a `/api/cmd/permisos/{id}/decidir` y los de OpenCode al
    // que ya usaba el panel.
    const botones = (p) => {
      if (!p.resoluble && !p.mision) return '';
      if (p.origen === 'OpenCode') {
        return `<div class="acciones">
          <button type="button" class="btn chico peligro" data-permiso="reject" data-id="${esc(p.id)}" data-mision="${esc(p.mision)}">Denegar</button>
          <button type="button" class="btn chico ok" data-permiso="once" data-id="${esc(p.id)}" data-mision="${esc(p.mision)}">Permitir una vez</button>
          <button type="button" class="btn chico" data-permiso="always" data-id="${esc(p.id)}" data-mision="${esc(p.mision)}">Permitir siempre</button>
        </div>`;
      }
      return `<div class="acciones">
        <button type="button" class="btn chico peligro" data-permiso="reject" data-id="${esc(p.id)}" data-herramienta="${esc(p.herramienta || '')}">Denegar</button>
        <button type="button" class="btn chico ok" data-permiso="once" data-id="${esc(p.id)}" data-herramienta="${esc(p.herramienta || '')}">Permitir una vez</button>
        <button type="button" class="btn chico" data-permiso="always" data-id="${esc(p.id)}" data-herramienta="${esc(p.herramienta || '')}">Permitir siempre</button>
      </div>`;
    };

    // La peticion de la autocorreccion va primero: si esta esperando, es lo
    // mas urgente que hay en la pantalla.
    const heal = S.heal || {};
    const healHtml = heal.action_id ? `
      <div class="caja alerta">
        <h3>JARVIS quiere arreglar algo <span class="insignia-estado">autorreparación</span></h3>
        <p>${esc(heal.description || heal.detail || 'Pide permiso para tocar el sistema.')}</p>
        <div class="acciones">
          <button type="button" class="btn chico peligro" data-heal="reject" data-id="${esc(heal.action_id)}">Denegar</button>
          <button type="button" class="btn chico ok" data-heal="once" data-id="${esc(heal.action_id)}">Permitir</button>
        </div>
      </div>` : '';

    $('per-pendientes').innerHTML = (healHtml + (pend.length ? pend.map((p) => `
      <div class="caja ${p.origen === 'OpenCode' ? 'alerta' : ''}">
        <h3>${esc(p.accion || '')} <span class="insignia-estado">${esc(p.origen || '')}</span></h3>
        <p>${esc(p.detalle || p.mensaje || '')}</p>
        ${p.espera != null ? `<p class="nota">Esperando ${dur(p.espera)}</p>` : ''}
        ${botones(p)}
      </div>`).join('') : '')) + (healHtml || (pend.length ? '' : vacio('No hay permisos pendientes', 'JARVIS no hace nada protegido sin tu visto bueno.')));

    const amb = Array.isArray(per.ambitos) ? per.ambitos : [];
    $('per-ambitos').innerHTML = amb.length ? amb.map((a) => `
      <div class="permiso-linea" data-estado="${esc(a.estado)}">
        <svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v18M3 12h18"/></svg>
        <span>${esc(a.ambito)}<span class="detalle-agent">${a.usos || 0} vez/veces registrada(s)${a.ultimo ? ` · ${relativo(a.ultimo)}` : ''}</span></span>
        <span class="estado">${esc({ permitido: 'Permitido', preguntar: 'Preguntar siempre', denegado: 'Denegado' }[a.estado] || a.estado)}</span>
      </div>`).join('') : '<p class="vacio-mini">Sin ámbitos registrados.</p>';

    const hist = Array.isArray(per.historial) ? per.historial : [];
    $('per-historial').innerHTML = hist.length ? hist.map((h) => `
      <div class="linea">
        <span class="glifo-mini" aria-hidden="true">${h.concedido ? '✓' : '✕'}</span>
        <span style="min-width:0"><b style="font-weight:600">${esc(h.accion || '')}</b>
          <span class="detalle-agent">${esc(h.detalle || '')}</span></span>
        <span class="hora">${relativo(h.at)}</span>
      </div>`).join('') : '<p class="vacio-mini">La auditoría está vacía.</p>';
  }

  /* ───────────────────────── vistas: preguntas ───────────────────────── */
  function pintarPreguntas() {
    const a = S.act || {};
    const pendientes = ((S.estado || {}).preguntas_lista) || (S.preguntas || []);
    const fuente = (a.hay_tarea && S.per && S.preguntas) ? S.preguntas : pendientes;
    const items = Array.isArray(fuente) ? fuente : [];
    $('preguntas-lista').innerHTML = items.length ? items.map((q) => {
      const opciones = Array.isArray(q.opciones) || Array.isArray(q.options)
        ? (q.opciones || q.options) : [];
      return `<div class="caja" style="margin-bottom:10px">
        <h3>${esc(q.title || q.question || 'OpenCode necesita una decisión')}</h3>
        ${q.description ? `<p>${esc(q.description)}</p>` : ''}
        ${opciones.length ? opciones.map((o, i) => `
          <button type="button" class="btn chico" data-respuesta="${esc(o.value ?? o.label)}"
            data-qid="${esc(q.id)}" data-mision="${esc(q.task_id || a.id || '')}">
            ${i + 1}. ${esc(o.label || o.value)}</button>`).join('')
          : `<input class="campo" placeholder="Escribe tu respuesta" maxlength="1000"
              data-qid="${esc(q.id)}" data-mision="${esc(q.task_id || a.id || '')}">`}
        <div class="acciones">
          <button type="button" class="btn chico ok" data-enviar-respuesta data-qid="${esc(q.id)}"
            data-mision="${esc(q.task_id || a.id || '')}">Enviar</button>
        </div></div>`;
    }).join('') : vacio('Ninguna pregunta pendiente', 'Cuando OpenCode necesite decidir algo, aparecerá aquí.');
  }

  /* ───────────────────────── vistas: tests ───────────────────────── */
  async function pintarTests() {
    const d = await get('/api/cmd/pruebas');
    const cands = Array.isArray(d.candidatos) ? d.candidatos : [];
    $('tes-deteccion').innerHTML = cands.length ? cands.map((c) => `
      <div class="linea">
        <span style="min-width:0"><b style="font-weight:600;font-family:var(--mono)">${esc(c.comando)}</b>
          <span class="detalle-agent">${esc(c.detalle || c.tipo || '')}</span></span>
        <button type="button" class="btn chico acento" style="margin-left:auto" data-test-cmd="${esc(c.comando)}">Ejecutar</button>
      </div>`).join('') : vacio('El proyecto no declara comando de tests',
      'JARVIS no inventa comandos: solo ofrece los que el proyecto define.');
    const trabajos = Array.isArray(d.trabajos) ? d.trabajos : [];
    $('tes-trabajos').innerHTML = trabajos.length ? trabajos.map((t) => `
      <div class="linea">
        <span style="min-width:0"><b style="font-weight:600;font-family:var(--mono)">${esc(t.comando)}</b>
          <span class="detalle-agent">${esc(t.estado)}${t.duracion ? ` · ${dur(t.duracion)}` : ''}${t.resumen ? ` · ${esc(t.resumen)}` : ''}${t.error ? ` · ${esc(t.error)}` : ''}</span></span>
        <button type="button" class="btn chico" style="margin-left:auto" data-test-ver="${esc(t.id)}">Salida</button>
      </div>`).join('') : '<p class="vacio-mini">Aún no se ha ejecutado nada.</p>';
  }

  /* Boton «Ejecutar» de la cabecera de «Comandos detectados».
     Antes no tenia handler: era un boton que no hacia nada. Ahora ejecuta el
     comando que el proyecto declara (el primero), y si no hay ninguno dice
     por que y se desactiva, en vez de fallar en silencio. */
  async function ejecutarTests() {
    const boton = $('tes-ejecutar');
    try {
      const d = await get('/api/cmd/pruebas');
      const cands = Array.isArray(d.candidatos) ? d.candidatos : [];
      if (!cands.length) {
        avisar('El proyecto no declara comando de tests, asi que no hay nada que ejecutar.', true);
        return;
      }
      await conBoton(boton, () => post('/api/cmd/pruebas', { comando: cands[0].comando }));
      avisar(`Ejecutando: ${cands[0].comando}`);
      await refrescar(true);
    } catch (e) {
      avisar(e.message, true);
    }
  }

  async function verTrabajo(id) {
    const d = await get(`/api/cmd/pruebas/${encodeURIComponent(id)}`);
    const t = d.trabajo || d;
    $('tes-salida-caja').hidden = false;
    $('tes-salida').textContent = t.output || t.salida || t.error || JSON.stringify(t, null, 2);
  }

  /* ───────────────────────── vistas: historial ───────────────────────── */
  async function pintarHistorial() {
    const e = S.estado || {};
    const au = e.ausente || {};
    $('hist-ausente').innerHTML = au.texto
      ? `<p style="margin:0">${esc(au.texto)}</p>
         <p class="nota">Ventana: ${au.minutos || 0} min · ${au.completadas || 0} completadas · ${au.fallidas || 0} fallidas · ${au.pendientes || 0} pendientes.</p>`
      : '<p class="vacio-mini">Sin novedades mientras estabas ausente.</p>';

    const misiones = Array.isArray(e.misiones) ? e.misiones : [];
    $('hist-misiones').innerHTML = misiones.length ? misiones.map((m) => `
      <div class="linea">
        <span style="min-width:0"><b style="font-weight:600">${esc((m.idea || '').slice(0, 60))}</b>
          <span class="detalle-agent">${esc(estadoVisual(m.status)[0])} · ${dur(m.elapsed)} · ${m.changes || 0} cambios</span></span>
        <span class="insignia-estado" data-s="${m.status === 'COMPLETED' ? 'ok' : m.status === 'FAILED' ? 'error' : 'aviso'}">${esc(m.status)}</span>
      </div>`).join('') : '<p class="vacio-mini">Sin misiones registradas.</p>';

    const dec = await get('/api/cmd/decisiones?limite=20');
    const filas = Array.isArray(dec.decisiones) ? dec.decisiones : [];
    $('hist-decisiones').innerHTML = filas.length ? filas.map((d) => `
      <div class="linea">
        <span style="min-width:0"><b style="font-weight:600">${esc(d.chosen || '')}</b>
          <span class="detalle-agent">${esc(d.question || '')}${d.reason ? ` · ${esc(d.reason)}` : ''}</span></span>
        <span class="hora">${relativo(d.created_at)}</span>
      </div>`).join('') : '<p class="vacio-mini">Sin decisiones registradas.</p>';

    const aud = await get('/api/cmd/auditoria?limite=25');
    const ent = Array.isArray(aud.entradas) ? aud.entradas : [];
    $('hist-auditoria').innerHTML = ent.length ? ent.map((a) => `
      <div class="linea">
        <span style="min-width:0"><b style="font-weight:600">${esc(a.accion || '')}</b>
          <span class="detalle-agent">${esc(a.detalle || '')}</span></span>
        <span class="insignia-estado" data-s="${a.resultado === 'success' ? 'ok' : a.resultado === 'denied' ? 'error' : 'aviso'}">${esc(a.resultado || '')}</span>
        <span class="hora">${relativo(a.at)}</span>
      </div>`).join('') : '<p class="vacio-mini">Auditoría vacía.</p>';
  }

  
/* ───────────────────────── vistas: IA ───────────────────────── */
  const ESTADOS_IA = {
    CONNECTED: ['Conectado', 'ok'], RATE_LIMITED: ['Sin cuota', 'aviso'],
    AUTH_ERROR: ['Clave no valida', 'error'], ERROR: ['Error', 'error'],
    TIMEOUT: ['Se paso el plazo', 'aviso'], DISCONNECTED: ['Sin conexion', 'aviso'],
    SIN_CLAVE: ['Sin clave', 'aviso'], APAGADO: ['Apagado', 'aviso'],
    SIN_DATOS: ['Sin datos', ''],
  };

  async function pintarIA() {
    let d;
    try { d = await get('/api/cmd/ia'); }
    catch (e) { $('ia-estado').innerHTML = vacio('No se pudo leer', e.message); return; }
    const salud = d.salud || [];
    const uso = d.uso || {};
    const groq = salud.find((p) => p.id === 'groq') || {};
    const global = ESTADOS_IA[d.estado_global] || ['Desconocido', ''];

    $('ia-estado').innerHTML = [
      `<article class="tarjeta tarjeta-estado">
        <span class="rotulo-m">ESTADO DE LOS MODELOS</span>
        <span class="cifra" style="color:var(--${global[1] === 'ok' ? 'ok' : global[1] === 'error' ? 'error' : 'aviso'})">${esc(global[0])}</span>
        <span class="nota">${esc(contarConectados(salud))}</span>
      </article>`,
      `<article class="tarjeta tarjeta-estado">
        <span class="rotulo-m">EN MARCHA AHORA</span>
        <span class="cifra">${d.en_vuelo || 0}</span>
        <span class="nota">peticiones abiertas</span>
      </article>`,
      `<article class="tarjeta tarjeta-estado">
        <span class="rotulo-m">PETICIONES (24 h)</span>
        <span class="cifra">${uso.peticiones || 0}</span>
        <span class="nota">${uso.fallos || 0} con error · ${uso.ok || 0} bien</span>
      </article>`,
      `<article class="tarjeta tarjeta-estado">
        <span class="rotulo-m">TOKENS (24 h)</span>
        <span class="cifra">${uso.total_tokens == null ? '—' : uso.total_tokens}</span>
        <span class="nota">coste: ${esc(uso.coste || 'unknown')}</span>
      </article>`,
      `<article class="tarjeta tarjeta-estado">
        <span class="rotulo-m">TIEMPO MEDIO</span>
        <span class="cifra">${uso.media_ms == null ? '—' : `${Math.round(uso.media_ms)} ms`}</span>
        <span class="nota">ultima: ${uso.ultimo ? relativo(uso.ultimo.at) : 'sin peticiones'}</span>
      </article>`,
    ].join('');

    $('ia-ventana').textContent = `ultimas 24 horas · proveedor Groq: ${groq.estado || 'SIN DATOS'}`;

    const asignados = d.asignados || [];
    $('ia-asignados').innerHTML = asignados.map((a) => {
      if (a.provider === 'opencode') {
        return `<div class="selector-modelo"><span class="etq">${esc(a.tarea)}</span>
          <span class="cap">OpenCode · agente de programacion</span></div>`;
      }
      const opciones = (d.modelos || []).filter((m) => m.provider === 'groq');
      const elegido = a.model || '';
      return `<div class="selector-modelo">
        <span class="etq">${esc(a.tarea)}</span>
        <select data-modelo-tarea="${esc(a.tarea)}" aria-label="Modelo para ${esc(a.tarea)}">
          ${opciones.map((m) => `<option value="${esc(m.model)}"${m.model === elegido ? ' selected' : ''}>${esc(m.display_name || m.model)}</option>`).join('')}
        </select>
        <span class="cap">${a.context_window ? `${Math.round(a.context_window / 1000)}k ctx` : 'ctx s/d'}${a.supports_tools === true ? ' · tools' : a.supports_tools === false ? ' · sin tools' : ' · tools ?'}</span>
      </div>`;
    }).join('');
    const sel = $('ia-fallback');
    if (sel) sel.checked = d.fallback !== false;

    $('ia-proveedores').innerHTML = salud.map((p) => {
      const info = ESTADOS_IA[p.estado] || [p.estado, ''];
      return `<div class="fila-estado" data-estado="${esc(p.estado)}">
        <span class="luz" aria-hidden="true"></span>
        <span><b>${esc(p.id)}</b>
          <span class="detalle-agent">${p.tiene_clave ? 'clave puesta' : 'sin clave'} · ${p.peticiones || 0} peticiones · ${p.fallos || 0} fallos${p.ultimo_uso ? ` · ultimo uso ${relativo(p.ultimo_uso.at)}` : ''}${p.ultimo_error ? ` · ultimo error: ${esc(p.ultimo_error.tipo || '')}` : ''}</span></span>
        <span class="chip-estado">${esc(info[0])}</span>
      </div>`;
    }).join('');

    $('ia-metricas').innerHTML = [
      metricaIA('ENTRADA', uso.input_tokens, 'tokens de entrada'),
      metricaIA('SALIDA', uso.output_tokens, 'tokens de salida'),
      metricaIA('REINTENTOS', (uso.por_proveedor && Object.values(uso.por_proveedor).reduce((a, b) => a + (b.fallos || 0), 0)) || 0, 'peticiones con fallo'),
      metricaIA('MAS RAPIDO', uso.min_ms, 'ms · minimo real'),
      metricaIA('MAS LENTO', uso.max_ms, 'ms · maximo real'),
      metricaIA('ERRORES', Object.keys(uso.errores || {}).length, Object.entries(uso.errores || {}).map(([k, v]) => `${k}: ${v}`).join(' · ') || 'sin errores'),
    ].join('');

    const pet = await get('/api/cmd/ia/peticiones?limite=20');
    const filas = pet.peticiones || [];
    $('ia-peticiones').innerHTML = filas.length ? filas.slice().reverse().map((f) => `
      <div class="linea">
        <span class="glifo-mini" aria-hidden="true">${f.ok ? '✓' : '✕'}</span>
        <span style="min-width:0"><b style="font-weight:600">${esc(f.provider || '?')} · ${esc(f.model || '—')}</b>
          <span class="detalle-agent">${esc(f.task || '')}${f.streaming ? ' · streaming' : ''}${f.error_kind ? ` · ${esc(f.error_kind)}` : ''}${f.retries ? ` · ${f.retries} reintentos` : ''}</span></span>
        <span class="hora">${f.total_tokens == null ? '—' : `${f.total_tokens} tok`} · ${Math.round(f.latency_ms || 0)} ms</span>
      </div>`).join('') : vacio('Sin peticiones registradas', 'En cuanto JARVIS hable con un modelo, aquí saldrán.');
  }

  function contarConectados(salud) {
    const ok = salud.filter((p) => p.estado === 'CONNECTED').length;
    const conClave = salud.filter((p) => p.tiene_clave).length;
    return `${ok} de ${conClave} proveedores con clave contestando`;
  }

  function metricaIA(rotulo, valor, pie) {
    return `<div class="metrica-ia"><span class="rotulo-m">${esc(rotulo)}</span>
      <span class="cifra">${valor == null ? '—' : valor}</span>
      <span class="pie">${esc(pie || '')}</span></div>`;
  }

/* ───────────────────────── vistas: sistema ───────────────────────── */
  function pintarSistema() {
    const e = S.estado || {};
    const r = e.recursos || {};
    const temp = r.temperatura || {};
    $('sis-recursos').innerHTML = `
      <div class="fila-dato"><span>CPU</span><strong>${r.cpu ? `${Math.round(r.cpu.porcentaje)}% · ${r.cpu.nucleos} núcleos` : 'no disponible'}</strong></div>
      <div class="fila-dato"><span>RAM</span><strong>${r.ram ? `${Math.round(r.ram.porcentaje)}% · ${r.ram.usado_gb}/${r.ram.total_gb} GB` : 'no disponible'}</strong></div>
      <div class="fila-dato"><span>GPU</span><strong>${r.gpu && r.gpu.available ? `${esc(r.gpu.name || '')} ${r.gpu.usage != null ? `${Math.round(r.gpu.usage)}%` : ''}${r.gpu.vram_total_mb ? ` · VRAM ${Math.round(r.gpu.vram_used_mb)}/${Math.round(r.gpu.vram_total_mb)} MB` : ''}${r.gpu.temperature != null ? ` · ${Math.round(r.gpu.temperature)}°C` : ''}` : `no disponible (${r.gpu && r.gpu.reason ? r.gpu.reason : 'sin sensor'})`}</strong></div>
      <div class="fila-dato"><span>Red</span><strong>${r.red && r.red.available ? (r.red.download_bps == null ? `midiendo (${esc(r.red.note || '')})` : `↓${(r.red.download_bps / 1048576).toFixed(1)} MB/s en ${r.red.since_seconds} s`) : `no disponible (${esc(r.red && r.red.reason ? r.red.reason : '')})`}</strong></div>
      <div class="fila-dato"><span>Temperatura CPU</span><strong>${temp.available ? `${temp.valor}°C` : `no disponible (${esc(temp.reason || '')})`}</strong></div>
      ${(r.discos || []).map((d) => `<div class="fila-dato"><span>Disco ${esc(d.etiqueta || '')}</span>
        <strong>${d.porcentaje != null ? `${Math.round(d.porcentaje)}% · ${d.usado_gb}/${d.total_gb} GB` : 'no disponible'}</strong></div>`).join('')}`;

    const salud = e.salud || {};
    $('sis-salud').innerHTML = (salud.subsistemas || []).map((s) => `
      <div class="linea">
        <span style="min-width:0"><b style="font-weight:600">${esc(s.nombre)}</b>
          <span class="detalle-agent">${esc(s.detalle || '')}</span></span>
        <span class="insignia-estado" data-s="${s.estado === 'healthy' ? 'ok' : s.estado === 'degraded' ? 'aviso' : s.estado === 'down' ? 'error' : 'no'}">${esc(s.estado)}</span>
      </div>`).join('') || vacio('Sin datos de salud', 'El backend aún no ha podido comprobar los subsistemas.');

    const inc = Array.isArray(e.incidentes) ? e.incidentes : [];
    $('sis-incidentes').innerHTML = inc.length ? inc.map((i) => `
      <div class="linea">
        <span style="min-width:0"><b style="font-weight:600">${esc(i.title)}</b>
          <span class="detalle-agent">${esc(i.description || '')} · ${i.occurrences || 1} vez/veces · ${relativo(i.created_at)}</span></span>
        <button type="button" class="btn chico ok" style="margin-left:auto" data-incidente="resolve" data-id="${esc(i.id)}">Resolver</button>
      </div>`).join('') : '<p class="vacio-mini">Sin incidentes abiertos.</p>';
  }

  /* ───────────────────────── refresco ───────────────────────── */
  async function refrescar(forzar = false) {
    if (S.cargando) { S.repetir = true; return; }
    S.cargando = true;
    try {
      const tareas = [get('/api/cmd/estado' + (forzar ? '?forzar=1' : ''))];
      tareas.push(get('/api/cmd/actividad'));
      tareas.push(get('/api/cmd/permisos'));
      tareas.push(get('/api/cmd/agentes'));
      tareas.push(get('/api/cmd/ia').catch(() => ({ ok: false })));
      // Agent Manager y Resource Manager. Si el servidor todavía no los tiene
      // (o fallan), se sigue pintando todo lo demás: un panel que no arranca
      // por un extra es peor que un panel sin el extra.
      tareas.push(get('/api/cmd/agentes/estado').catch(() => ({ ok: false })));
      tareas.push(get('/api/cmd/recursos/decisiones').catch(() => ({ ok: false })));
      // El estado de la voz va en el mismo lote: el indicador vive en el pie
      // del menu lateral y se ve desde cualquier vista.
      tareas.push(get('/api/live/overview').catch(() => ({ voice_available: false })));
      const [estado, act, per, ag, ia, agMgr, recMgr, vozDatos] = await Promise.all(tareas);
      S.estado = estado; S.act = act; S.per = per; S.ag = ag;
      // El indicador de voz se pintaba una sola vez al arrancar, asi que
      // ponia 'inactivo' durante toda la sesion aunque JARVIS estuviera
      // hablando. Va aqui, en cada pasada del panel, porque el indicador
      // esta en el pie del menu lateral, que se ve desde cualquier vista.
      voz(vozDatos);
      S.ia = ia && ia.ok ? (ia.uso || {}) : {};
      S.agentesMgr = agMgr && agMgr.ok ? agMgr : null;
      S.recursosMgr = recMgr && recMgr.ok ? recMgr : null;
      const c = estado.contadores || {};
      S.preguntas = await preguntasPendientes();
      S.ultimo = Date.now();
      pintarCabecera();
      pintarHero();
      pintarPlan();
      pintarCambiosRecientes();
      pintarTiempoReal();
      pintarAgentes();
      pintarLateral();
      pintarNotis();
      if (c.preguntas) pintarPreguntas();
      if (S.vista === 'sistema') pintarSistema();
      if (S.vista === 'nueva') pintarNueva();
    } catch (e) {
      if (S.errores === 0) { avisar(`${e.message} La pantalla seguirá reintentando.`, true); console.error('[live]', e); }
      S.errores += 1;
    } finally {
      S.cargando = false;
      if (S.repetir) { S.repetir = false; setTimeout(() => refrescar(), 300); }
    }
  }

  async function preguntasPendientes() {
    try {
      const d = await get('/api/cmd/preguntas');
      return Array.isArray(d.preguntas) ? d.preguntas : [];
    } catch { return []; }
  }

  function hayTrabajo() {
    const a = S.act || {};
    const c = (S.estado || {}).contadores || {};
    return a.hay_tarea && !['COMPLETED', 'FAILED', 'CANCELLED'].includes(a.estado);
  }

  function ritmo() {
    clearTimeout(S.pulso);
    const activo = hayTrabajo() || ((S.estado || {}).contadores || {}).permisos;
    S.pulso = setTimeout(() => refrescar(), activo ? 4000 : 12000);
  }

  /* ───────────────────────── WebSocket ───────────────────────── */
  function urlWs() {
    const esquema = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${esquema}//${location.host}/ws?token=${encodeURIComponent(token)}`;
  }

  function conectar() {
    clearTimeout(S.reintento);
    if (!token) { avisar('No se recibió la sesión de JARVIS. Recarga la página.', true); return; }
    try { S.ws = new WebSocket(urlWs()); }
    catch { avisar('No se pudo abrir el enlace en tiempo real.', true); return; }
    S.ws.addEventListener('open', () => { S.conectado = true; S.errores = 0; refrescar(true); });
    let t = null;
    S.ws.addEventListener('message', (ev) => {
      let m = null;
      try { m = JSON.parse(ev.data); } catch { return; }
      if (m.type === 'assistant_reply' || m.type === 'user_heard') {
        const texto = m.message || m.text || m.reply || '';
        if (texto) {
          const r = $('respuesta');
          r.textContent = String(texto).slice(0, 1200);
          r.hidden = false;
        }
      }
      const tipo = String(m.type || '');
      // La autocorreccion pide permiso antes de tocar nada. El evento llegaba
    // por el WebSocket pero el panel no lo pintaba, asi que no habia forma de
    // responderle: se quedaba esperando sola.
    if (tipo === 'heal_approval_request') {
      S.heal = d;
      avisar('JARVIS quiere tocar algo para arreglar un fallo. Decide en «Permisos».');
    }
    if (/^(opencode\.|live_task|confirmation_|heal_|agent_|action_status|selfheal_)/.test(tipo)) {
        clearTimeout(t);
        t = setTimeout(() => refrescar(), 250);
      }
    });
    S.ws.addEventListener('close', () => {
      S.conectado = false;
      S.reintento = setTimeout(conectar, 3000);
    });
    S.ws.addEventListener('error', () => { try { S.ws.close(); } catch { /* ya cerrado */ } });
  }

  /* ───────────────────────── acciones ───────────────────────── */
  async function conBoton(boton, trabajo) {
    if (!boton) return trabajo();
    boton.disabled = true;
    const previo = boton.textContent;
    boton.textContent = '…';
    try { return await trabajo(); }
    finally { if (boton.isConnected) { boton.disabled = false; boton.textContent = previo; } }
  }

  async function accionMision(accion, id, boton, extra = {}) {
    await conBoton(boton, async () => {
      const rutas = {
        approve: ['aprobar', 'Plan aprobado. OpenCode puede trabajar.'],
        revise: ['revisar', 'Plan enviado a revisión.'],
        cancel: ['cancelar', 'Misión detenida.'],
        pause: ['pausar', 'Turno de OpenCode interrumpido.'],
        retry: ['reintentar', 'Misión relanzada.'],
        // Reanudar: el endpoint existía y funcionaba, pero no estaba en la
        // tabla, asi que el boton no hacia nada.
        resume: ['resume', 'Misión reanudada.'],
        analyze: ['analizar', 'JARVIS está analizando el proyecto.'],
      };
      const [ruta, okMsg] = rutas[accion] || [null, null];
      if (!ruta) return;
      const cuerpo = accion === 'revise' ? { note: extra.note || 'Revisa el plan y ajusta lo que propongas.' }
        : accion === 'cancel' ? { motivo: extra.motivo || '' } : {};
      const d = await post(`/api/dev/tasks/${encodeURIComponent(id)}/${ruta}`, cuerpo);
      avisar(d.message || okMsg);
      await refrescar(true);
    }).catch((e) => avisar(e.message, true));
  }

  async function accionPermiso(decision, id, mision, boton, herramienta) {
    await conBoton(boton, async () => {
      // Dos caminos distintos segun de quien sea el permiso. Los de OpenCode
      // cuelgan de una mision; los del Risk Engine de JARVIS no, y antes no
      // habia manera de resolverlos desde aqui: el panel no podia aprobar
      // nada, y el commit se quedaba esperando al timeout de 30 s.
      if (mision) {
        const d = await post(`/api/dev/tasks/${encodeURIComponent(mision)}/permissions/${encodeURIComponent(id)}`, { decision });
        avisar(d.message || `Permiso enviado: ${decision}.`);
      } else {
        const d = await post(`/api/cmd/permisos/${encodeURIComponent(id)}/decidir`,
                             { decision, herramienta: herramienta || '' });
        avisar(d.ok ? `Permiso ${decision}: hecho.` : (d.error || 'No se pudo resolver el permiso.'));
      }
      await refrescar(true);
    }).catch((e) => avisar(e.message, true));
  }

  async function enviarRespuesta(mision, qid, valor) {
    if (!mision) { avisar('No se sabe a qué misión pertenece la pregunta.', true); return; }
    await post(`/api/dev/tasks/${encodeURIComponent(mision)}/questions/${encodeURIComponent(qid)}`, { value: valor })
      .then((d) => { avisar(d.message || 'Respuesta enviada a OpenCode.'); })
      .then(() => refrescar(true))
      .catch((e) => avisar(e.message, true));
  }

  /* ───────────────────────── eventos de interfaz ───────────────────────── */
  function conectarInterfaz() {
    document.addEventListener('click', async (ev) => {
      const nav = ev.target.closest('.nav-item[data-vista]');
      if (nav) { ir(nav.dataset.vista); return; }
      const irA = ev.target.closest('[data-ir]');
      if (irA) { ir(irA.dataset.ir); return; }

      if (ev.target.closest('#btn-notis')) {
        S.notisAbierto = !S.notisAbierto; pintarNotis(); return;
      }
      const notisAccion = ev.target.closest('[data-notis-accion]');
      if (notisAccion) {
        if (notisAccion.dataset.notisAccion === 'leer-todas') {
          const pend = ((S.estado || {}).notificaciones || []).filter((n) => !n.seen);
          for (const n of pend) { try { await post(`/api/cmd/notificaciones/${encodeURIComponent(n.id)}`, { accion: 'read' }); } catch { /* se reintenta al refrescar */ } }
          avisar('Notificaciones marcadas como leídas.');
        } else {
          await post(`/api/cmd/notificaciones/${encodeURIComponent(notisAccion.dataset.id)}`, { accion: 'dismiss' })
            .then(() => avisar('Notificación descartada.')).catch((e) => avisar(e.message, true));
        }
        await refrescar(true);
        return;
      }

      const tab = ev.target.closest('.pestana[data-plan]');
      if (tab) { S.tab = tab.dataset.plan; pintarPlan(); return; }

      const mision = ev.target.closest('[data-mision]');
      if (mision) {
        const accion = mision.dataset.mision;
        const id = mision.dataset.id || (S.act || {}).id;
        if (accion === 'revise') {
          const nota = window.prompt('¿Qué debe cambiar del plan?', 'Ajusta el plan a lo que he pedido.');
          if (nota === null) return;
          await accionMision('revise', id, mision, { note: nota });
        } else if (accion === 'cancel') {
          if (!window.confirm('¿Detener la misión? OpenCode dejará de trabajar en ella.')) return;
          await accionMision('cancel', id, mision);
        } else await accionMision(accion, id, mision);
        return;
      }

      const permiso = ev.target.closest('[data-permiso]');
      if (permiso) {
        const d = permiso.dataset.permiso;
        if (d === 'always' && !window.confirm('¿Permitir esto siempre, también en futuras misiones?')) return;
        if (d === 'reject' && !window.confirm('¿Denegar? OpenCode no podrá continuar esa acción.')) return;
        await accionPermiso(d, permiso.dataset.id, permiso.dataset.mision, permiso, permiso.dataset.herramienta);
        return;
      }

      const resp = ev.target.closest('[data-respuesta]');
      if (resp) { await enviarRespuesta(resp.dataset.mision, resp.dataset.qid, resp.dataset.respuesta); return; }
      if (ev.target.closest('[data-enviar-respuesta]')) {
        const b = ev.target.closest('[data-enviar-respuesta]');
        const input = document.querySelector(`[data-qid="${CSS.escape(b.dataset.qid)}"]`);
        await enviarRespuesta(b.dataset.mision, b.dataset.qid, input ? input.value : '');
        return;
      }

      const inc = ev.target.closest('[data-incidente]');
      if (inc) {
        await post(`/api/cmd/incidentes/${encodeURIComponent(inc.dataset.id)}`, { accion: 'resolve' })
          .then(() => avisar('Incidente marcado como resuelto.'))
          .catch((e) => avisar(e.message, true));
        await refrescar(true);
        return;
      }

      if (ev.target.closest('#idea-crear')) {
        const idea = $('idea').value.trim();
        if (!idea) { avisar('Escribe qué quieres que haga JARVIS.', true); return; }
        const cuerpo = { idea, analizar: true };
        if ($('idea-workspace').value.trim()) cuerpo.project_path = $('idea-workspace').value.trim();
        if ($('idea-modelo').value.trim()) cuerpo.model = $('idea-modelo').value.trim();
        await conBoton($('idea-crear'), async () => {
          const d = await post('/api/dev/tasks', cuerpo);
          avisar(d.message || 'Tarea creada. JARVIS la analiza antes de tocar nada.');
          $('idea').value = '';
          ir('dashboard');
          await refrescar(true);
        }).catch((e) => avisar(e.message, true));
        return;
      }
      if (ev.target.closest('#idea-limpiar')) { $('idea').value = ''; $('idea-workspace').value = ''; $('idea-modelo').value = ''; return; }
      if (ev.target.closest('#idea-hablar')) {
        $('command-input').focus();
        avisar('Escribe la orden en la barra inferior y pulsa Enviar.');
        return;
      }

      if (ev.target.closest('#ta-crear')) {
        const nombre = $('ta-nombre').value.trim();
        const mensaje = $('ta-mensaje').value.trim();
        const cuando = $('ta-cuando').value.trim();
        if (!nombre || !mensaje || !cuando) { avisar('Nombre, orden y cuándo son obligatorios.', true); return; }
        await post('/api/cmd/automatizaciones', { nombre, mensaje, cuando })
          .then(() => avisar('Tarea programada.'))
          .catch((e) => avisar(e.message, true));
        $('ta-nombre').value = ''; $('ta-mensaje').value = ''; $('ta-cuando').value = '';
        await pintarTareas();
        return;
      }
      const auto = ev.target.closest('[data-auto]');
      if (auto) {
        const { auto: accion, id } = auto.dataset;
        if (accion === 'borrar' && !window.confirm('¿Borrar esta tarea programada?')) return;
        await post(`/api/cmd/automatizaciones/${encodeURIComponent(id)}/${accion}`, {})
          .then(() => avisar(`Tarea ${accion === 'correr' ? 'ejecutada' : accion + 'ada'}.`))
          .catch((e) => avisar(e.message, true));
        await pintarTareas();
        return;
      }

      const abrir = ev.target.closest('[data-agente-start]');
      if (abrir) {
        await conBoton(abrir, () => post(`/api/cmd/agentes/${encodeURIComponent(abrir.dataset.agenteStart)}/start`, {}))
          .then((d) => avisar(d.message || 'Programa abierto.'))
          .catch((e) => avisar(e.message, true));
        await refrescar(true);
        return;
      }

      // Cerrar un programa de agente: pide confirmación porque si tiene
      // cambios sin guardar, se pierden.
      const cerrar = ev.target.closest('[data-agente-stop]');
      if (cerrar) {
        if (!window.confirm('¿Cerrar ese programa? Si tiene cambios sin guardar, se pierden.')) return;
        await conBoton(cerrar, () => post(`/api/cmd/agentes/${encodeURIComponent(cerrar.dataset.agenteStop)}/stop`, {}))
          .then((d) => avisar(d.message || d.detalle || 'Programa cerrado.'))
          .catch((e) => avisar(e.message, true));
        await refrescar(true);
        return;
      }

      const cancelarJob = ev.target.closest('[data-job-cancelar]');
      if (cancelarJob) {
        if (!window.confirm('¿Cancelar ese trabajo?')) return;
        await conBoton(cancelarJob, () => post(`/api/cmd/agentes/trabajo/${encodeURIComponent(cancelarJob.dataset.jobCancelar)}/cancelar`, { motivo: 'cancelado desde el panel' }))
          .then((d) => avisar(d.ok ? 'Trabajo cancelado.' : (d.error || 'No se pudo cancelar.')))
          .catch((e) => avisar(e.message, true));
        await refrescar(true);
        return;
      }

      const diff = ev.target.closest('[data-diff]');
      if (diff) { await pintarDiff(diff.dataset.diff); return; }
      if (ev.target.closest('#cam-refrescar')) { await pintarCambios(true); avisar('Estado de Git actualizado.'); return; }
      if (ev.target.closest('#cam-checkpoint')) {
        await post('/api/cmd/checkpoints', { nota: `Punto de retorno manual · ${new Date().toLocaleString('es-ES')}` })
          .then((d) => avisar(d.message || 'Checkpoint guardado.'))
          .catch((e) => avisar(e.message, true));
        await pintarCambios();
        return;
      }
      if (ev.target.closest('#cam-commit')) {
        const mensaje = $('cam-mensaje').value.trim() || 'Cambios confirmados desde JARVIS Live';
        await post('/api/cmd/cambios/commit', { mensaje })
          .then((d) => avisar(d.message || 'Commit confirmado.'))
          .catch((e) => avisar(e.message, true));
        await refrescar(true);
        await pintarCambios(true);
        return;
      }
      if (ev.target.closest('#btn-commit')) {
        const b = ev.target.closest('#btn-commit');
        if (!window.confirm('¿Confirmar estos cambios en Git con el mensaje propuesto?')) return;
        await post('/api/cmd/cambios/commit', { mensaje: b.dataset.mensaje })
          .then((d) => avisar(d.message || 'Commit confirmado.'))
          .catch((e) => avisar(e.message, true));
        await refrescar(true);
        return;
      }
      const cp = ev.target.closest('[data-cp]');
      if (cp) {
        const { cp: accion, id } = cp.dataset;
        if (accion === 'restaurar' && !window.confirm('¿Restaurar los ficheros de este checkpoint? Los cambios actuales se perderán.')) return;
        if (accion === 'borrar' && !window.confirm('¿Borrar este punto de retorno?')) return;
        try {
          if (accion === 'comparar') {
            const d = await get(`/api/cmd/checkpoints/${encodeURIComponent(id)}`);
            $('cam-diff').innerHTML = (d.parche || d.resumen || 'Sin parche.')
              .split('\n').map((l) => esc(l)
                .replace(/^(\+)/, '<span class="mas">$1</span>')
                .replace(/^(-)/, '<span class="menos">$1</span>')).join('\n');
            avisar('Comparación cargada.');
          } else if (accion === 'restaurar') {
            await post(`/api/cmd/checkpoints/${encodeURIComponent(id)}/restaurar`, {});
            avisar('Checkpoint restaurado.');
            await refrescar(true);
          } else {
            await borrar(`/api/cmd/checkpoints/${encodeURIComponent(id)}`);
            avisar('Checkpoint borrado.');
          }
        } catch (e) { avisar(e.message, true); }
        await pintarCambios();
        return;
      }

      const heal = ev.target.closest('[data-heal]');
      if (heal) {
        const decision = heal.dataset.heal;
        if (decision === 'reject' && !window.confirm('Denegar la autoreparación?')) return;
        await conBoton(heal, () => post(`/api/cmd/autorreparacion/${encodeURIComponent(heal.dataset.id)}/decidir`, { decision }))
          .then(() => { S.heal = {}; avisar(decision === 'reject' ? 'Denegado.' : 'Permitido.'); })
          .then(() => refrescar(true))
          .catch((e) => avisar(e.message, true));
        return;
      }

      if (ev.target.closest('#tes-ejecutar')) {
        await ejecutarTests();
        return;
      }

      const test = ev.target.closest('[data-test-cmd]');
      if (test) {
        await conBoton(test, () => post('/api/cmd/pruebas', { comando: test.dataset.testCmd }))
          .then((d) => { avisar(d.message || 'Tests lanzados.'); return verTrabajo(d.id || d.job_id); })
          .catch((e) => avisar(e.message, true));
        await pintarTests();
        return;
      }
      const ver = ev.target.closest('[data-test-ver]');
      if (ver) { await verTrabajo(ver.dataset.testVer).catch((e) => avisar(e.message, true)); return; }

      if (ev.target.closest('#hist-briefing')) {
        await post('/api/cmd/briefing', { minutos: 60, hablar: false })
          .then((d) => { $('hist-ausente').innerHTML = `<p style="margin:0">${esc(d.texto || '')}</p>`; avisar('Resumen generado.'); })
          .catch((e) => avisar(e.message, true));
        return;
      }
      if (ev.target.closest('#hist-briefing-voz')) {
        await post('/api/cmd/briefing', { minutos: 60, hablar: true })
          .then(() => avisar('Contando el resumen en voz alta.'))
          .catch((e) => avisar(e.message, true));
        return;
      }


      if (ev.target.closest('#ia-guardar')) {
        const cuerpo = { fallback: $('ia-fallback').checked };
        document.querySelectorAll('select[data-modelo-tarea]').forEach((sel) => {
          cuerpo[sel.dataset.modeloTarea] = sel.value;
        });
        await conBoton($('ia-guardar'), async () => {
          await post('/api/cmd/ia/configurar', cuerpo);
          avisar('Modelos guardados. Se nota en la siguiente peticion.');
          await pintarIA();
        }).catch((e) => avisar(e.message, true));
        return;
      }
      if (ev.target.closest('#ia-probar')) {
        const salida = $('ia-salida');
        salida.hidden = false;
        salida.textContent = 'Probando conexion con Groq…';
        await conBoton($('ia-probar'), async () => {
          const d = await post('/api/cmd/ia/probar', { provider: 'groq' });
          salida.textContent = `${d.estado}: ${d.detalle || d.error || ''}${d.modelo ? `\nmodelo: ${d.modelo}` : ''}`;
          avisar(`Groq: ${d.estado}`);
          await pintarIA();
        }).catch((e) => { salida.textContent = e.message; avisar(e.message, true); });
        return;
      }
      if (ev.target.closest('#ia-catalogo')) {
        await conBoton($('ia-catalogo'), async () => {
          const d = await get('/api/cmd/ia/modelos?consultar=1');
          const salida = $('ia-salida');
          salida.hidden = false;
          salida.textContent = (d.modelos || []).map((m) => [
            m.id, `ctx ${m.context_window || 's/d'}`, `tools ${m.supports_tools}`,
            `razonamiento ${m.supports_reasoning}`, `fuente ${m.fuente}`,
          ].join(' · ')).join('\n') || 'El proveedor no devolvio catalogo.';
          avisar(`Catalogo del proveedor: ${(d.modelos || []).length} modelos.`);
        }).catch((e) => avisar(e.message, true));
        return;
      }

      const sis = ev.target.closest('[id^="sis-"]');
      if (sis) {
        const mapa = {
          'sis-diag': ['/api/cmd/diagnostico', { texto: 'Diagnóstico general del sistema' }],
          'sis-optimizar': ['/api/cmd/sistema/optimizar', {}],
          'sis-disco': ['/api/cmd/sistema/disco', {}],
          'sis-oc': ['/api/cmd/opencode/servidor', { texto: 'reiniciar' }],
        };
        const entrada = mapa[sis.id];
        if (!entrada) return;
        if (sis.id !== 'sis-diag' && !window.confirm('Esta acción pide permiso antes de ejecutarse. ¿Continuar?')) return;
        await conBoton(sis, () => post(entrada[0], entrada[1]))
          .then((d) => {
            const caja = $('sis-salida');
            caja.hidden = false;
            caja.textContent = d.texto || d.salida || d.mensaje || JSON.stringify(d, null, 2);
            avisar(d.mensaje || 'Hecho.');
          })
          .catch((e) => avisar(e.message, true));
        await refrescar(true);
      }
    });

    document.querySelectorAll('.pestana[data-plan]').forEach((t) => t.addEventListener('keydown', (e) => {
      if (e.key === 'ArrowRight' || e.key === 'ArrowLeft') { e.preventDefault(); S.tab = e.key === 'ArrowRight' ? 'ejecucion' : 'propuesta'; pintarPlan(); }
    }));

    $('command-bar').addEventListener('submit', async (e) => {
      e.preventDefault();
      const texto = $('command-input').value.trim();
      if (!texto) return;
      $('command-input').value = '';
      try {
        const d = await post('/api/cmd/orden', { texto });
        $('respuesta').textContent = d.respuesta || d.texto || d.mensaje || 'Orden enviada a JARVIS.';
        $('respuesta').hidden = false;
        avisar(d.mensaje || 'Orden enviada.');
      } catch (err) { avisar(err.message, true); }
      await refrescar(true);
    });

    $('command-micro').addEventListener('click', () => {
      avisar('JARVIS ya te escucha por el micrófono: habla con normalidad.');
      try { window.dispatchEvent(new CustomEvent('jarvis:voz')); } catch { /* el bus de voz es opcional */ }
    });

    document.addEventListener('keydown', (e) => {
      if (e.key === '/' && document.activeElement.tagName !== 'INPUT' && document.activeElement.tagName !== 'TEXTAREA') {
        e.preventDefault(); $('command-input').focus();
      }
    });
    window.addEventListener('hashchange', () => { const v = location.hash.slice(1); if (v && v !== S.vista) ir(v, false); });
  }

  /* ───────────────────────── arranque ───────────────────────── */
  async function voz(datos) {
    // Si ya viene en el lote de `refrescar()`, no se pide otra vez: el panel
    // hace siete peticiones en cada pasada y no hacía falta una octava.
    try {
      S.voz = datos || await get('/api/live/overview');
      pintarVoz();
    } catch {
      S.voz = { voice_available: false };
      pintarVoz();
    }
  }

  function arrancar() {
    const v = location.hash.slice(1);
    if (v) ir(v, false); else ir('dashboard', false);
    conectarInterfaz();
    conectar();
    refrescar(true).then(() => {
      voz();
      clearInterval(S.tic);
      S.tic = setInterval(() => { if (!document.hidden) refrescar(); }, 15000);
    });
    S.tic2 = setInterval(ritmo, 15000);
    S.reloj = setInterval(() => { $('reloj').textContent = new Intl.DateTimeFormat('es-ES', { hour: '2-digit', minute: '2-digit' }).format(new Date()); }, 1000);
    $('reloj').textContent = new Intl.DateTimeFormat('es-ES', { hour: '2-digit', minute: '2-digit' }).format(new Date());
  }

  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', arrancar);
  else arrancar();
})();