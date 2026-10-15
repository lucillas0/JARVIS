'use strict';
(() => {
  const $ = (id) => document.getElementById(id);
  const token = typeof window.JARVIS_TOKEN === 'string' ? window.JARVIS_TOKEN : '';
  const API = '/api/live';
  const state = { connected: false, snapshot: null, ws: null, reconnect: null,
    failureTimer: null, refreshTimer: null, pollTimer: null, durationTimer: null,
    latestEvents: [], activeFilter: 'todos', lastPoll: 0, errors: 0 };
  let uidSinId = 0;
  const categories = new Set(['tareas', 'cambios', 'permisos', 'errores', 'agentes', 'sistema', 'conversación']);

  function announce(message) {
    const notice = $('notice');
    notice.textContent = String(message || '');
    notice.hidden = false;
    clearTimeout(announce.timer);
    announce.timer = setTimeout(() => { notice.hidden = true; }, 4400);
  }
  function status(text, mode) {
    $('connection-label').textContent = text;
    $('connection').dataset.state = mode;
  }
  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    })[c]);
  }
  function dateTime(value, fallback = '—') {
    if (!value) return fallback;
    let date = new Date(value);
    if (typeof value === 'number') date = new Date(value * 1000);
    return Number.isNaN(date.getTime()) ? fallback : new Intl.DateTimeFormat('es-ES', {
      hour: '2-digit', minute: '2-digit', second: '2-digit'
    }).format(date);
  }
  function metric(value) {
    return typeof value === 'number' && Number.isFinite(value) ? value : null;
  }
  function progress(id, value) {
    const valid = metric(value);
    $(id + '-value').textContent = valid === null ? '—' : `${valid}%`;
    $(id + '-bar').style.width = valid === null ? '0%' : `${Math.max(0, Math.min(100, valid))}%`;
  }
  async function api(path, options = {}) {
    if (!token) throw new Error('No se recibió la sesión de JARVIS. Recarga la página.');
    const response = await fetch(path, {
      ...options,
      headers: { ...(options.body ? { 'Content-Type': 'application/json' } : {}),
        'X-Jarvis-Token': token, ...(options.headers || {}) },
      cache: 'no-store', credentials: 'same-origin',
    });
    let data;
    try { data = await response.json(); } catch { throw new Error(`El cerebro respondió con HTTP ${response.status}.`); }
    if (!response.ok) throw new Error(data.error || `El cerebro respondió con HTTP ${response.status}.`);
    return data;
  }
  function updateHeading(data) {
    $('system-status').dataset.state = data.online ? 'online' : 'offline';
    $('system-status').firstElementChild.nextElementSibling.textContent = data.online ? 'OPERATIVO' : 'DESCONECTADO';
    $('jarvis-state').textContent = data.online ? 'Operativo' : 'Desconectado';
    const pending = data.approvals.length;
    const work = data.active_tasks.filter(task => task.state === 'ejecutando' || task.state === 'requiere aprobación').length;
    $('capacity-label').textContent = pending ? 'ATENCIÓN REQUERIDA' : work ? 'TRABAJANDO' : 'DISPONIBLE';
    $('system-summary').textContent = !data.online ? 'No se recibe respuesta del cerebro.' : pending
      ? `Hay ${pending} ${pending === 1 ? 'acción pendiente de autorización' : 'acciones pendientes de autorización'}.`
      : work ? `JARVIS está atendiendo ${work} ${work === 1 ? 'trabajo activo' : 'trabajos activos'}.`
      : 'Cerebro en línea. No hay trabajos de conversación en curso.';
    const metrics = data.resources || {};
    progress('cpu', metrics.cpu);
    progress('ram', metrics.ram);
    const ramDetail = metrics.ram_used != null && metrics.ram_total != null
      ? ` · ${metrics.ram_used} de ${metrics.ram_total} GB` : '';
    $('ram-value').textContent = metric(metrics.ram) == null ? '—' : `${metrics.ram}%`;
    $('resource-note').textContent = metric(metrics.cpu) == null
      ? 'CPU y memoria no disponibles en este equipo.'
      : `Memoria en uso${ramDetail}. La GPU y temperatura no están expuestas por el gestor actual.`;
    $('active-count').textContent = String(data.active_tasks.length);
    const active = (data.agents || []).filter(agent => agent.status === 'activo');
    $('agent-count').textContent = String(active.length);
    $('voice-state').textContent = data.voice_available ? labelState(data.voice) : 'No disponible';
    $('discord-state').textContent = data.discord ? 'Configurado' : 'No conectado';
    $('client-count').textContent = String(data.active_clients);
    $('brain-context').textContent = [data.voice_available ? 'Voz disponible' : 'Sin voz', data.discord ? 'Discord configurado' : 'Discord desconectado'].join(' · ');
    const visibleTasks = data.active_tasks.filter(task => task.state !== 'completada');
    if (visibleTasks.length) {
      const task = visibleTasks[visibleTasks.length - 1];
      $('activity-title').textContent = task.name || 'Trabajo de JARVIS';
      $('activity-description').textContent = task.description || 'Trabajo en ejecución. El porcentaje de avance no está disponible en el gestor actual.';
      $('current-activity').textContent = `JARVIS está trabajando: ${task.name || 'trabajo activo'}`;
      $('current-task').hidden = false;
      const esBuild = String(task.id || '').startsWith('build-');
      $('current-task').innerHTML = '';
      const info = document.createElement('span');
      info.textContent = `${task.id} · ${labelState(task.state)} · Agente: ${task.agent || 'JARVIS'} · Tiempo transcurrido: ${duration(task.started_at)}`;
      $('current-task').appendChild(info);
      if (esBuild) {
        const parar = document.createElement('button');
        parar.type = 'button';
        parar.className = 'task-mini';
        parar.dataset.stopBuild = task.id;
        parar.textContent = 'Detener';
        $('current-task').appendChild(parar);
      }
    } else {
      $('activity-title').textContent = pending ? 'JARVIS está esperando autorización.' : 'El cerebro está disponible.';
      $('activity-description').textContent = pending
        ? 'Una acción protegida está bloqueada hasta que decidas. Los detalles aparecen a continuación.'
        : 'El panel no inventa tareas activas. Aquí aparecerán las consultas cuando JARVIS las esté procesando.';
      $('current-activity').textContent = pending ? 'Esperando tu autorización.' : 'Esperando actividad del cerebro.';
      $('current-task').hidden = true;
    }
    $('last-updated').textContent = `Última lectura: ${dateTime(Date.now() / 1000)}`;
    $('fecha').textContent = new Intl.DateTimeFormat('es-ES', { weekday: 'long', day: 'numeric', month: 'long' }).format(new Date()).toUpperCase();
  }
  function labelState(value) {
    return ({ idle: 'En espera', listening: 'Escuchando', thinking: 'Pensando', speaking: 'Hablando',
      disconnected: 'Desconectada', offline: 'Desconectado', active: 'Activo', ejecutando: 'En ejecución',
      completada: 'Completada', failed: 'Error', paused: 'En pausa', pending: 'Pendiente' })[value] || value || 'No disponible';
  }
  function duration(start) {
    const seconds = Math.max(0, Math.floor(Date.now() / 1000 - Number(start || Date.now() / 1000)));
    return seconds < 60 ? `${seconds} s` : `${Math.floor(seconds / 60)} min`;
  }
  function escAttr(value) { return esc(value); }
  function approvalRisk(value) {
    return ({ critical: 'Crítico', high: 'Alto', sensitive: 'Sensible', medium: 'Medio', low: 'Bajo' })[String(value).toLowerCase()] || 'Pendiente';
  }
  function renderApprovals(list) {
    const section = $('approval-section');
    section.hidden = list.length === 0;
    $('approval-count').textContent = String(list.length);
    $('approvals-list').innerHTML = list.map(item => `<article class="aprobacion-item" aria-labelledby="approval-${escAttr(item.id)}">
      <h3 id="approval-${escAttr(item.id)}">${esc(item.action)}</h3>
      <p>${esc(item.description)}</p>
      <div class="riesgo-linea"><span class="riesgo-chip">Riesgo: ${esc(approvalRisk(item.risk))}</span><span class="riesgo-chip">Solicita: ${esc(item.requester || 'JARVIS')}</span><span class="riesgo-chip">Motivo: ${esc(item.task || 'Confirmación requerida')}</span></div>
      <p>La operación está detenida hasta que elijas. JARVIS aplica la decisión mediante su Permission Manager actual.</p>
      <div class="approval-actions"><button type="button" data-approval="${escAttr(item.id)}" data-kind="${escAttr(item.kind)}" data-allow="yes">Aprobar</button><button class="denegar" type="button" data-approval="${escAttr(item.id)}" data-kind="${escAttr(item.kind)}" data-allow="no">Denegar</button></div>
    </article>`).join('');
  }
  function normalizeEvent(event) {
    if (!event || typeof event !== 'object') return null;
    const title = String(event.title || event.label || event.text || event.task || event.detail || '').trim();
    if (!title) return null;
    const type = String(event.type || '').toLowerCase();
    const mapped = type.includes('approval') || type.includes('confirmation') ? 'permisos'
      : type.includes('error') || type.includes('failed') ? 'errores'
      : type.includes('agent') ? 'agentes'
      : type === 'assistant_reply' || type === 'user_heard' ? 'conversación'
      : type === 'status' || type === 'hello' ? 'sistema' : 'tareas';
    const category = categories.has(event.category) ? event.category : mapped;
    return { id: String(event.id || event.event_id || `local-${type}-${++uidSinId}`),
      at: event.at || Date.now() / 1000, category, title: title.slice(0,160),
      detail: String(event.detail || event.label || title).slice(0,350) };
  }
  function renderEvents() {
    const filtered = state.latestEvents.filter(event => state.activeFilter === 'todos' || event.category === state.activeFilter);
    $('event-count').textContent = `${filtered.length} ${filtered.length === 1 ? 'evento' : 'eventos'}`;
    if (!filtered.length) {
      $('event-list').innerHTML = `<div class="vacio"><span class="vacio-icono" aria-hidden="true">◷</span><strong>${state.latestEvents.length ? 'No hay eventos en este filtro.' : 'La actividad del cerebro aparecerá aquí.'}</strong><p>${state.latestEvents.length ? 'Selecciona «Todo» para consultar la actividad reciente.' : 'Los cambios, consultas y autorizaciones usarán el WebSocket actual de JARVIS.'}</p></div>`;
      return;
    }
    $('event-list').innerHTML = filtered.map(event => `<article class="fila-evento" data-category="${escAttr(event.category)}"><time class="hora-evento">${esc(dateTime(event.at))}</time><span class="marca-evento" aria-hidden="true">${event.category === 'errores' ? '!' : event.category === 'permisos' ? '•' : '✓'}</span><div><strong>${esc(event.title)}</strong><p>${esc(event.detail)}</p></div></article>`).join('');
  }
  function addEvent(raw) {
    const item = normalizeEvent(raw);
    if (!item || state.latestEvents.some(event => event.id === item.id)) return;
    state.latestEvents.push(item);
    state.latestEvents.sort((a,b) => eventTimestamp(b.at) - eventTimestamp(a.at));
    if (state.latestEvents.length > 80) state.latestEvents.length = 80;
    renderEvents();
  }
  function renderTasks(list) {
    $('scheduled-count').textContent = String(list.length);
    if (!list.length) {
      $('task-list').innerHTML = '<div class="vacio-pequeno">No hay tareas programadas.</div>';
      return;
    }
    $('task-list').innerHTML = list.slice(0, 8).map(task => {
      const pausa = task.paused
        ? `<button type="button" class="task-mini" data-resume="${escAttr(task.id)}">Reanudar</button>`
        : `<button type="button" class="task-mini" data-pause="${escAttr(task.id)}">Pausar</button>`;
      const repite = task.repeat ? ` · se repite cada ${Math.round(task.repeat / 60)} min` : '';
      return `<article class="tarea-item"><div class="tarea-head"><strong>${esc(task.name)}</strong><span class="estado-chip" data-state="pending">${esc(labelState(task.state))}</span></div><p>${esc(task.description || 'Sin descripción.')}</p><div class="tarea-meta"><span>${esc(dateTime(task.run_at))}${esc(repite)}</span></div><div class="tarea-botones"><button type="button" class="task-mini" data-run="${escAttr(task.id)}">Ejecutar ahora</button>${pausa}<button type="button" class="task-cancel" data-cancel="${escAttr(task.id)}">Cancelar</button></div></article>`;
    }).join('');
  }
  function renderRecent(list) {
    const items = (list || []).slice(0, 8);
    $('recent-count').textContent = String((list || []).length);
    state.recent = {};
    if (!items.length) {
      $('recent-list').innerHTML = '<div class="vacio-pequeno">Aquí aparecerán los trabajos terminados, para reintentarlos.</div>';
      return;
    }
    $('recent-list').innerHTML = items.map(task => {
      state.recent[task.id] = task.message || '';
      const marca = task.failed ? ' · falló' : '';
      return `<article class="tarea-item"><div class="tarea-head"><strong>${esc(task.name)}</strong><span class="estado-chip" data-state="pending">${esc(labelState(task.failed ? 'failed' : 'completada'))}${esc(marca)}</span></div><div class="tarea-botones"><button type="button" class="task-mini" data-retry="${escAttr(task.id)}">Reintentar</button></div></article>`;
    }).join('');
  }
  function renderAgents(list) {
    const active = list.filter(agent => agent.status === 'activo');
    $('agents-total').textContent = `${active.length}/${list.length}`;
    $('agent-list').innerHTML = list.filter(agent => agent.status === 'activo').length
      ? active.slice(0,7).map(agent => `<article class="agente-item"><div class="agente-head"><span class="agente-info"><span class="indicador-agente activo" aria-hidden="true"></span><strong>${esc(agent.name)}</strong></span><small>${esc(labelState(agent.status))}</small></div><p class="tarea-meta">${esc((agent.processes || []).slice(0,2).join(', ') || agent.description)}</p></article>`).join('')
      : '<div class="vacio-pequeno">No se han detectado agentes de escritorio activos.</div>';
  }
  function renderChanges(list) {
    $('changes-total').textContent = String(list.length);
    $('change-list').innerHTML = list.length ? list.slice(0,7).map(item => `<article class="cambio-item"><strong class="archivo-cambio">${esc(item.path)}</strong><div class="cambio-meta"><span>Git · ${esc(item.action)}</span><span>Cambio local</span></div></article>`).join('')
      : '<div class="sin-cambios">Git no detecta cambios locales en este proyecto o el estado no está disponible.</div>';
  }
  function eventTimestamp(value) {
    if (typeof value === 'number' && Number.isFinite(value)) return value;
    const numeric = Number(value);
    if (value && Number.isFinite(numeric) && String(value).trim() !== '') return numeric;
    const parsed = Date.parse(value);
    return Number.isNaN(parsed) ? 0 : parsed / 1000;
  }
  function setSnapshot(data) {
    state.snapshot = data;
    updateHeading(data);
    renderApprovals(data.approvals || []);
    renderTasks(data.scheduled_tasks || []);
    renderRecent(data.recent_tasks || []);
    renderAgents(data.agents || []);
    renderChanges(data.changes || []);
    const events = [...(data.audit_events || []), ...(data.events || [])].map(event => {
      if (event.type === 'error_report') event.category = 'errores';
      return normalizeEvent(event);
    }).filter(Boolean);
    const seen = new Set();
    state.latestEvents = events.sort((a,b) => eventTimestamp(b.at) - eventTimestamp(a.at)).filter(event => {
      if (seen.has(event.id)) return false;
      seen.add(event.id); return true;
    }).slice(0,80);
    renderEvents();
  }
  async function refresh(quiet = false) {
    const now = Date.now();
    if (state.refreshing) { state.refreshAgain = true; return; }
    if (now - state.lastPoll < 1600) {
      if (!state.refreshTimer) {
        state.refreshTimer = setTimeout(() => {
          state.refreshTimer = null;
          void refresh(quiet);
        }, 1600 - (now - state.lastPoll));
      }
      return;
    }
    state.lastPoll = now;
    state.refreshing = true;
    try {
      const result = await api(`${API}/overview`);
      if (!state.connected) { state.errors = 0; status('Cerebro conectado', 'online'); }
      state.connected = true;
      clearTimeout(state.failureTimer);
      setSnapshot(result);
      state.failureTimer = setTimeout(() => {
        if (Date.now() - state.lastPoll > 10000) status('Sin confirmación reciente del cerebro', 'offline');
      }, 9000);
    } catch (error) {
      state.connected = false;
      state.errors += 1;
      status(error.message.includes('autorizada') ? 'Sesión no válida' : 'Cerebro desconectado; reintentando', 'offline');
      $('system-status').dataset.state = 'offline';
      $('system-status').lastElementChild.textContent = 'SIN CONEXIÓN';
      $('jarvis-state').textContent = 'Desconectado';
      if (!quiet && state.errors === 1) announce(`${error.message} La interfaz seguirá intentando conectar.`);
    } finally {
      state.refreshing = false;
      if (state.refreshAgain) {
        state.refreshAgain = false;
        setTimeout(() => { state.lastPoll = 0; void refresh(true); }, 160);
      }
    }
  }
  function websocketUrl() {
    const scheme = location.protocol === 'https:' ? 'wss:' : 'ws:';
    return `${scheme}//${location.host}/ws?token=${encodeURIComponent(token)}`;
  }
  function connect() {
    clearTimeout(state.reconnect);
    if (!token) { status('Falta la sesión del cerebro', 'offline'); return; }
    try { state.ws = new WebSocket(websocketUrl()); }
    catch { status('No se pudo abrir el enlace en tiempo real', 'offline'); retry(); return; }
    state.ws.addEventListener('open', () => {
      state.connected = true;
      state.errors = 0;
      status('Cerebro conectado · eventos en directo', 'online');
      refresh(true);
    });
    let overviewTimer = null;
    state.ws.addEventListener('message', event => {
      try {
        const message = JSON.parse(event.data);
        if (message.type === 'live_event' && message.event) addEvent(message.event);
        else if (message.live_event) addEvent(message.live_event);
        if (['live_task_start', 'live_task_end', 'task_step', 'confirmation_request',
          'confirmation_resolved', 'heal_approval_request', 'action_status', 'agent_state',
          'agent_update', 'selfheal_update'].includes(message.type)) {
          clearTimeout(overviewTimer);
          overviewTimer = setTimeout(() => void refresh(true), 200);
        }
        else if (['confirmation_request','heal_approval_request','action_status','task_step','assistant_reply','user_heard','selfheal_update'].includes(message.type)) {
          if (message.type === 'confirmation_request') refresh(true);
          const generic = message.type === 'task_step'
            ? { ...message, title: message.label, detail: message.task || message.label, category: 'tareas' }
            : message.type === 'confirmation_request'
              ? { ...message, title: message.description, detail: 'La acción necesita autorización.', category: 'permisos' }
              : message.type === 'heal_approval_request'
                ? { ...message, title: message.error, detail: message.plan || 'Aprobación solicitada.', category: 'permisos' }
                : message;
          if (!message.live_event) addEvent(generic);
        }
      } catch { /* Mensaje de otro canal: el dashboard no altera su protocolo. */ }
    });
    state.ws.addEventListener('close', () => {
      state.connected = false;
      status('Enlace en tiempo real interrumpido; reintentando', 'offline');
      void refresh(true);
      retry();
    });
    state.ws.addEventListener('error', () => {
      state.connected = false;
      status('Sin enlace en tiempo real; reintentando', 'offline');
      try { state.ws.close(); } catch { /* Cerrado ya */ }
    });
  }
  function retry() {
    clearTimeout(state.reconnect);
    state.reconnect = setTimeout(connect, 2800);
  }
  function sendChat(text) {
    if (!state.ws || state.ws.readyState !== 1) {
      announce('Sin enlace en tiempo real. Espera a que conecte e inténtalo de nuevo.');
      return false;
    }
    state.ws.send(JSON.stringify({ type: 'user_message', message: text, input_mode: 'text' }));
    return true;
  }
  async function postAction(action) {
    const button = action.button;
    const oldText = button.textContent;
    button.disabled = true;
    button.textContent = 'Aplicando decisión…';
    try {
      const result = await api(`${API}/actions`, { method: 'POST', body: JSON.stringify(action.body) });
      announce(result.message || (action.body.approved ? 'Autorización registrada en JARVIS.' : 'Denegación registrada en JARVIS.'));
      await refresh(true);
    } catch (error) {
      announce(`${error.message} Recarga el panel si la acción dejó de estar pendiente.`);
      await refresh(true);
    } finally {
      if (button.isConnected) { button.disabled = false; button.textContent = oldText; }
    }
  }
  document.addEventListener('click', event => {
    const approval = event.target.closest('[data-approval]');
    if (approval) {
      const id = approval.dataset.approval;
      const kind = approval.dataset.kind;
      const allowed = approval.dataset.allow === 'yes';
      if (!window.confirm(`${allowed ? '¿Aprobar' : 'Denegar'} la acción solicitada por JARVIS?\n\n${allowed ? 'El Permission Manager registrará tu decisión.' : 'La acción quedará denegada y no continuará.'}`)) return;
      const button = event.target;
      void postAction({ button: approval, body: { kind: kind === 'selfheal' ? 'approve_selfheal' : 'approve_confirmation', target_id: id, approved: allowed } });
      return;
    }
    const cancel = event.target.closest('[data-cancel]');
    if (cancel) {
      if (!window.confirm('¿Cancelar esta tarea programada? No se podrán deshacer los cambios si el planificador ya la inició.')) return;
      void postAction({ button: cancel, body: { kind: 'cancel_scheduled_task', target_id: cancel.dataset.cancel } });
      return;
    }
    const run = event.target.closest('[data-run]');
    if (run) {
      void postAction({ button: run, body: { kind: 'run_scheduled_task', target_id: run.dataset.run } });
      return;
    }
    const pause = event.target.closest('[data-pause]');
    if (pause) {
      void postAction({ button: pause, body: { kind: 'pause_scheduled_task', target_id: pause.dataset.pause } });
      return;
    }
    const resume = event.target.closest('[data-resume]');
    if (resume) {
      void postAction({ button: resume, body: { kind: 'resume_scheduled_task', target_id: resume.dataset.resume } });
      return;
    }
    const stop = event.target.closest('[data-stop-build]');
    if (stop) {
      if (!window.confirm('¿Detener este proyecto? Lo ya escrito se queda como está.')) return;
      void postAction({ button: stop, body: { kind: 'cancel_build', target_id: stop.dataset.stopBuild } });
      return;
    }
    const retryBtn = event.target.closest('[data-retry]');
    if (retryBtn) {
      const texto = (state.recent || {})[retryBtn.dataset.retry] || '';
      if (!texto) { announce('Ese trabajo ya no guarda su mensaje para reintentarlo.'); return; }
      if (sendChat(texto)) announce('Reintento enviado a JARVIS.');
      return;
    }
    const filter = event.target.closest('[data-filter]');
    if (filter) {
      document.querySelectorAll('.filtro').forEach(button => {
        const active = button === filter;
        button.classList.toggle('seleccionado', active);
        button.setAttribute('aria-pressed', String(active));
      });
      state.activeFilter = filter.dataset.filter;
      renderEvents();
    }
  });
  window.addEventListener('online', () => { status('Red disponible; verificando el cerebro…', 'pending'); void refresh(true); });
  window.addEventListener('offline', () => status('Sin conexión de red; JARVIS continúa trabajando', 'offline'));
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') void refresh(true); });
  $('task-clear').addEventListener('click', () => {
    $('task-name').value = ''; $('task-message').value = ''; $('task-when').value = '';
  });
  $('task-form').addEventListener('submit', event => {
    event.preventDefault();
    const btn = $('task-submit');
    const body = { kind: 'schedule_task', name: $('task-name').value.trim(),
      message: $('task-message').value.trim(), schedule: $('task-when').value.trim() };
    if (!body.message || !body.schedule) {
      announce('Escribe la orden y el cuándo (18:30, en 30m o cada 1h).');
      return;
    }
    void postAction({ button: btn, body });
  });
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && document.activeElement instanceof HTMLElement) document.activeElement.blur();
  });
  refresh();
  connect();
  state.pollTimer = setInterval(() => { void refresh(true); }, 15000);
  state.durationTimer = setInterval(() => {
    if (state.snapshot) {
      const task = state.snapshot.active_tasks?.filter(item => item.state !== 'completada').at(-1);
      if (task) $('current-task').textContent = `${task.id} · ${labelState(task.state)} · Agente: ${task.agent || 'JARVIS'} · Tiempo transcurrido: ${duration(task.started_at)}`;
    }
  }, 15000);
  window.addEventListener('beforeunload', () => {
    clearTimeout(state.reconnect);
    clearTimeout(state.failureTimer);
    clearTimeout(state.refreshTimer);
    clearInterval(state.pollTimer);
    clearInterval(state.durationTimer);
    clearInterval(announce.timer);
    try { state.ws?.close(); } catch { /* Sin conexión viva */ }
  }, { once: true });
})();
