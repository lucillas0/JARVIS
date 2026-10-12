// Conecta el island real de Coucou con el Cerebro de JARVIS.
//
// Coucou lee la actividad de los hooks de Claude Code por Tauri. Aqui se
// alimenta de los eventos que JARVIS ya recibe por el WebSocket del cerebro:
// cada 'tool' entra como paso del ticker, cada estado cambia el de Mochi, y el
// chat es el del cerebro (Bridge.chatSend ya lo hace).
//
// Se carga despues de main.js del repo, que es quien crea la isla.

import { Bridge, emiteEvento } from "./coucou/_js/core/bridge.js";
import { State } from "./coucou/_js/core/state.js";
import { montaMascotas, muestra, sincroniza, montaAvatares, activa, fijaFoco } from "./jarvis-mascotas.js";

const CEREBRO = "ws://127.0.0.1:8000/ws";
const MAIN = "jarvis";

// El raton lo lleva followPageCursor de Coucou, leyendo los mousemove de la
// propia pagina (Bridge.boot devuelve cursorPoll:false para que lo haga).
//
// Ojo con 'pointer-inside': ese flag solo existe para Linux, donde el compositor
// dice si el puntero esta sobre la capa. Si aqui se le pasa false, onCursor
// descarta toda posicion y la isla no vuelve a abrirse nunca.
function enganchaAtajos() {
  const api = globalThis.jarvisNotchAPI;
  if (!api) return;
  // Win+Shift+J y el menu: sacar la isla de la pausa y abrirla.
  api.onIslandToggle?.(() => emiteEvento("tray", "open"));
}

// Los verbos son los de Coucou (views/hooks.ts), no traducciones inventadas.
const VERBO = {
  Bash: "Runs", PowerShell: "Runs", run_command: "Runs",
  Read: "Reads", view_file: "Reads", read_file: "Reads",
  Write: "Writes", write_to_file: "Writes",
  Edit: "Edits", replace_file_content: "Edits", str_replace: "Edits",
  Glob: "Searches", Grep: "Searches", search: "Searches",
  WebSearch: "Searches the web", search_web: "Searches the web",
  WebFetch: "Fetches", read_url_content: "Fetches",
  TodoWrite: "Tasks", Task: "Agent", LS: "Lists",
  NotebookEdit: "Notebook",
  view: "Reads", read: "Reads", write: "Writes", edit: "Edits",
  glob: "Searches", grep: "Searches", bash: "Runs",
};

const ESTADO = {
  idle: "idle", trabajando: "working", working: "working",
  pensando: "thinking", thinking: "thinking",
  buscando: "searching", searching: "searching",
  pregunta: "question", question: "question",
  permiso: "approval", approval: "approval",
  error: "error", fallo: "error",
  terminado: "finished", finished: "finished", done: "finished",
  dormido: "sleeping", sleeping: "sleeping",
  mareado: "dizzy", dizzy: "dizzy",
  limite: "ratelimit", ratelimit: "ratelimit",
};

function normaliza(s) {
  return String(s == null ? "" : s).trim().toLowerCase();
}

function conecta() {
  let ws;
  try { ws = new WebSocket(CEREBRO); } catch (e) { return; }

  ws.onclose = () => setTimeout(conecta, 4000);
  ws.onerror = () => { /* onclose reintenta */ };
  ws.onopen = () => {
    State.setFocus(MAIN);
    State.updateTask(MAIN, "idle");
  };

  ws.onmessage = (ev) => {
    let m;
    try { m = JSON.parse(ev.data); } catch (e) { return; }
    if (!m || typeof m !== "object") return;

    // Herramienta: un paso mas del ticker.
    if (m.type === "tool" && m.tool) {
      ensure();
      const label = VERBO[m.tool] || "Agent";
      const detail = m.detail || m.file || m.path || m.command || "";
      State.appendStep(MAIN, paso(label, detail));
      State.updateTask(MAIN, ESTADO[m.state] || "working");
      Bridge.setCollapsed(false);
      return;
    }

    // Evento generico del cerebro.
    const ev2 = m.event || m.name;
    const datos = (m.data && typeof m.data === "object") ? m.data : m;
    if (!ev2) return;

    if (ev2 === "action_status") {
      ensure();
      const estado = ESTADO[normaliza(datos.estado || datos.status)] || null;
      if (estado) State.updateTask(MAIN, estado);
      const herramienta = datos.herramienta || datos.tool;
      if (herramienta && !datos.terminado) {
        State.appendStep(MAIN, paso(VERBO[herramienta] || "Agent", datos.detalle || datos.detail || ""));
      }
      if (datos.terminado) {
        State.updateTask(MAIN, "finished");
        State.setPillBadge(MAIN, "finished");
        State.notify();
      }
    } else if (ev2 === "action_approval" || ev2 === "approval" || ev2 === "permission") {
      // Coucou espera una estructura propia; se le pasa la que el repo usa.
      try {
        State.beginApproval({
          requestId: String(datos.request_id || datos.id || ""),
          sessionId: MAIN,
          pillId: MAIN,
          tool: datos.herramienta || datos.tool || "",
          command: datos.comando || datos.command || datos.detalle || "",
          questions: datos.preguntas || datos.questions,
        });
        Bridge.setCollapsed(false);
      } catch (e) { /* si no encaja, el aviso se pierde */ }
    } else if (ev2 === "action_result" || ev2 === "result") {
      State.updateTask(MAIN, ESTADO[normaliza(datos.estado)] || "finished");
    }
  };
}

/** JARVIS entra por el catalogo de pastillas, no se inserta a mano: si se
 *  insertara aqui se veria duplicado. Solo se crea si aun no esta. */
function ensure() {
  if (State.tasks.some((t) => t.id === MAIN)) return;
  State.upsertWorkspacePill(MAIN, "JARVIS", "");
  if (!State.focusId) State.setFocus(MAIN);
}

function paso(verb, detail) {
  return detail ? verb + " · " + detail : verb;
}

// ── Pastillas con su estado real ───────────────────────────────────────────
// Coucou pregunta a sus hooks que agente esta vivo. JARVIS pregunta al cerebro
// que procesos estan corriendo (brain/apps.py) y pone cada mascota a trabajar o
// en reposo segun corresponda.
const consulta = { ultima: {}, tarea: null };

async function miraApps() {
  try {
    const r = await fetch("http://127.0.0.1:8000/api/apps", { cache: "no-store" });
    const d = await r.json();
    if (!d || !d.ok || !d.apps) return;
    ensure();
    for (const [pastilla, info] of Object.entries(d.apps)) {
      const tarea = State.tasks.find((t) => t.id === pastilla);
      if (!tarea) continue;
      const antes = consulta.ultima[pastilla];
      const ahora = !!info.activa;
      if (antes === ahora) continue;
      consulta.ultima[pastilla] = ahora;
      State.updateTask(pastilla, ahora ? "working" : "idle");
      // Un paso solo cuando cambia: si no, el ticker se llenaria de ruido.
      State.appendStep(pastilla, paso("Runs", ahora ? (info.procesos && info.procesos[0]) || tarea.name : "closed"));
    }
    State.notify();
  } catch (e) {
    /* el cerebro no esta: las pastillas se quedan como estaban */
  }
}

function empiezaMirada() {
  if (consulta.tarea) return;
  miraApps();
  consulta.tarea = setInterval(miraApps, 4000);
}

// ── Mascotas ─────────────────────────────────────────────────────────────────
// Las mascotas de JARVIS (Jarvi, Vee, Opi y Buffi) son las de siempre: cada
// pastilla lleva la suya y la isla pinta la de la pastilla con el foco. Van
// ENCENDIDAS por defecto, que es lo que se quiere ver; el interruptor sigue
// estando para volver a Mochi en caliente:
//   __mascotas.activa(false)  -> vuelve Mochi, el de Coucou
//   __mascotas.activa(true)   -> vuelven las nuestras
//
// El trabajo de estado (trabajando, compilando, pensando, pidiendo permiso) y
// las florituras en reposo solo hacen falta si estan encendidas.
let QUIERO_MASCOTAS = true;

// Espera a que Coucou haya montado la isla: el contenedor de la mascota no
// existe hasta entonces.
function esperaIsla() {
  const ancla = document.querySelector("#bot-canvas") && document.querySelector("#bot-canvas").parentElement;
  if (!ancla) { setTimeout(esperaIsla, 300); return; }
  // ponMascotas enciende de verdad (activa + monta): montaMascotas a secas
  // solo crea el lienzo y se queda con Mochi delante.
  ponMascotas(QUIERO_MASCOTAS);
  // Los avatares van en las pastillas, que Coucou vuelve a pintar cuando
  // cambian los agentes: se revisa cada poco por si aparece alguna nueva.
  montaAvatares();
  setInterval(montaAvatares, 2000);
}

function sigueElFoco() {
  // Cada vez que Coucou cambia de pastilla, la mascota tambien.
  let ultimoFoco = null;
  let ultimoEstado = null;
  setInterval(() => {
    const foco = State.focusId || MAIN;
    if (foco !== ultimoFoco) { ultimoFoco = foco; muestra(foco); }
    const tarea = State.tasks.find((t) => t.id === foco);
    const estado = (tarea && tarea.status) || "idle";
    if (estado !== ultimoEstado) { ultimoEstado = estado; sincroniza(estado); }
  }, 500);
}

// El interruptor. La fuente de verdad es esta variable; __mascotas.activa solo
// aplica el cambio. Antes el puente reconfirmaba el valor apagado cada medio
// segundo y por eso no se podia encender a mano.
setInterval(() => {
  if (!window.__mascotas) return;
  fijaFoco(State.focusId || MAIN);
  // Los avatares de las pastillas van siempre; la mascota grande solo si esta
  // encendida, porque si no manda Mochi.
  montaAvatares();
  // Se reconfirma por ponMascotas y no por montaMascotas: Coucou vuelve a
  // pintar su boton cuando cambia algo, y si solo se montara, el interruptor
  // se quedaria apagado sin querer aunque aqui siga puesto.
  ponMascotas(QUIERO_MASCOTAS);
}, 900);

function ponMascotas(on) {
  QUIERO_MASCOTAS = !!on;
  if (!window.__mascotas) return QUIERO_MASCOTAS;
  window.__mascotas.activa(QUIERO_MASCOTAS);
  if (QUIERO_MASCOTAS) {
    const ancla = document.querySelector("#bot-canvas");
    if (ancla) { montaMascotas(ancla.parentElement); montaAvatares(); }
  }
  return QUIERO_MASCOTAS;
}

conecta();
empiezaMirada();
enganchaAtajos();
sigueElFoco();

// Enganche de pruebas: deja ver que pastilla tiene el foco y cuales existen.
globalThis.__isla = {
  foco: () => State.focusId,
  pastillas: () => State.tasks.map((t) => ({ id: t.id, nombre: t.name, estado: t.status })),
  mascota: () => (globalThis.__mascotas ? globalThis.__mascotas.activo() : null),
  mascotas: (on) => (on === undefined ? QUIERO_MASCOTAS : ponMascotas(on)),
};

if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", esperaIsla);
} else {
  esperaIsla();
}