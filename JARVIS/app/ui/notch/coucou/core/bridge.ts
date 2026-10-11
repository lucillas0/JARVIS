// Puente de Coucou talks con Tauri. Aqui se sustituye por el mismo objeto con la
// misma forma, pero hablando con el Cerebro de JARVIS por WebSocket y con el
// proceso principal de Electron por el preload. Todo lo demas sigue siendo un
// no-op, igual que en un navegador normal (main.ts ya tiene la rama !IS_TAURI).
//
// La clave: este fichero conserva los nombres, los argumentos y los tipos de
// return del original, para que island.ts, views/ y core/ compilen sin cambios.

import type { Settings } from "./state";
import type { RecapHistory, RecapPrefs } from "../recap/summary";
import type { SpotifyState } from "./spotify";

export const IS_TAURI = false;

type Handler = (payload: unknown) => void;
const oyentes = new Map<string, Set<Handler>>();

/** JARVIS dispara un evento de Coucou (lo usa el puente de Electron). */
export function emiteEvento(nombre: string, payload?: unknown): void {
  const set = oyentes.get(nombre);
  if (!set) return;
  for (const h of [...set]) {
    try { h(payload); } catch (e) { console.error("[coucou] evento " + nombre, e); }
  }
}

async function call<T>(cmd: string, args?: Record<string, unknown>): Promise<T | null> {
  // Sin Tauri todas las llamadas son no-op, como en el navegador.
  void cmd; void args;
  return null;
}

async function callOrThrow<T>(cmd: string, args?: Record<string, unknown>): Promise<T> {
  void cmd; void args;
  throw new Error("sin Tauri");
}

// ── JARVIS: el chat y el resto van al Cerebro ───────────────────────────────
const CEREBO = "ws://127.0.0.1:8000/ws";
let socket: WebSocket | null = null;
const pendientes = new Map<string, (v: unknown) => void>();
let contador = 0;

function conecta(): void {
  if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING)) return;
  try { socket = new WebSocket(CEREBO); } catch (e) { socket = null; return; }
  socket.onopen = () => { emiteEvento("jarvis-abierto"); };
  socket.onclose = () => { socket = null; setTimeout(conecta, 3000); };
  socket.onerror = () => { /* el onclose reintenta */ };
  socket.onmessage = (ev) => {
    let m: Record<string, unknown>;
    try { m = JSON.parse(ev.data as string); } catch (e) { return; }
    // Respuesta a un chat que pidio el puente.
    const id = m.id as string | undefined;
    if (id && pendientes.has(id)) {
      const res = pendientes.get(id)!;
      pendientes.delete(id);
      res(m);
      return;
    }
    // Un evento del cerebro: la isla lo ve como el resto.
    if (m.type === "event" || m.type === "tool" || m.type === "status") {
      emiteEvento("jarvis", m);
    }
  };
}

export function jarvisEstado(): "conectado" | "conectando" | "sin" {
  if (!socket) return "sin";
  return socket.readyState === WebSocket.OPEN ? "conectado" : "conectando";
}

async function pideAlCerebro(mensaje: Record<string, unknown>, ms = 90000): Promise<any> {
  conecta();
  if (!socket || socket.readyState !== WebSocket.OPEN) {
    return new Promise((resolve) => setTimeout(() => resolve(null), ms));
  }
  const id = "coucou-" + ++contador;
  return new Promise((resolve) => {
    const t = setTimeout(() => { pendientes.delete(id); resolve(null); }, ms);
    pendientes.set(id, (v) => { clearTimeout(t); resolve(v); });
    try { socket!.send(JSON.stringify({ ...mensaje, id })); }
    catch (e) { clearTimeout(t); pendientes.delete(id); resolve(null); }
  });
}

export interface BootInfo {
  settings: Settings;
  screen: { x: number; y: number; width: number; height: number; scale: number };
  version: string;
  hookPath: string;
  cursorPoll: boolean;
}

/** Las pastillas que el usuario tiene declaradas, con JARVIS como principal. */
const DECLARADAS = [
  "integration_claude",   // Visual Studio Code
  "agent_opencode",       // OpenCode
  "freebuff",             // Freebuff
  "agent_codex",
  "agent_gemini",
];

const AJUSTES: Settings = {
  soundEnabled: true,
  soundVolume: 0.12,
  autoCloseInterval: 15,
  openOnHover: true,
  absenceInterval: 180,
  activeIntegrations: DECLARADAS,
  mainPill: "jarvis",
  screen: "primary",
  autostart: true,
  hooksInstalled: false,
  model: "jarvis",
  showPlanInNotch: false,
  planRelayInstalled: false,
  showCodexPlanInNotch: false,
  chatProvider: "anthropic",
  chatModels: {},
  ollamaUrl: "",
  lmstudioUrl: "",
  customUrl: "",
  shortcuts: {},
  mochiOutfit: "auto",
  pillColors: {},
  language: "es",
} as Settings;

/** Electron expone jarvisNotchAPI en el renderer. */
function host(): any {
  return (globalThis as any).jarvisNotchAPI || null;
}

export const Bridge = {
  boot: () => call<BootInfo>("boot").then(() => ({
    settings: AJUSTES,
    screen: { x: 0, y: 0, width: screen.width, height: screen.height, scale: 1 },
    version: "jarvis",
    hookPath: "",
    // En Electron el raton lo vigila el proceso principal, no la pagina.
    cursorPoll: true,
  })),

  setSystemLanguages: (languages: string[]) => call<void>("set_system_languages", { languages }),

  saveSettings: (settings: Settings) => {
    Object.assign(AJUSTES, settings);
    return call<void>("save_settings", { settings });
  },

  setCollapsed: (collapsed: boolean) => {
    host()?.setCollapsed?.(collapsed);
    return call<void>("set_collapsed", { collapsed });
  },

  setIslandRect: (x: number, y: number, width: number, height: number) => {
    host()?.setIslandRect?.(x, y, width, height);
    return call<void>("set_island_rect", { x, y, width, height });
  },

  focusWindow: (focused: boolean) => {
    host()?.focusWindow?.(focused);
    return call<void>("focus_window", { focused });
  },

  reposition: () => call<void>("reposition"),

  listMonitors: () => call<{ key: string; label: string }[]>("list_monitors")
    .then((r) => r ?? [{ key: "primary", label: "Principal" }]),

  openUrl: (url: string) => {
    host()?.openExternal?.(url);
    return call<void>("open_url", { url });
  },

  customSounds: (names: readonly string[]) => call<string[]>("custom_sounds", { names }),
  customSound: (name: string) => call<ArrayBuffer>("custom_sound", { name }),
  revealSoundsFolder: () => call<void>("reveal_sounds_folder"),
  reloadSounds: () => call<void>("reload_sounds"),

  openInVSCode: (path: string | null) => call<boolean>("open_in_vscode", { path }),
  openSession: (sessionId: string | null, path: string | null) =>
    call<boolean>("open_session", { sessionId, path }),
  openClaudeDesktop: () => call<boolean>("open_claude_desktop"),
  openFileInVSCode: (path: string) => {
    host()?.openPath?.(path);
    return call<boolean>("open_file_in_vscode", { path });
  },

  quit: () => { host()?.quit?.(); return call<void>("quit_app"); },
  openSettingsWindow: () => call<void>("open_settings_window"),
  log: (message: string) => call<void>("log_line", { message }),

  // JARVIS no usa los hooks de Claude Code: se contestan vacios.
  hooksStatus: () => call<any>("hooks_status"),
  agentHooksStatus: () => call<Record<string, boolean>>("agent_hooks_status"),
  hooksPreview: (install: boolean) => callOrThrow<any>("hooks_preview", { install }),
  hooksApply: (install: boolean, fingerprint: string) =>
    callOrThrow<string>("hooks_apply", { install, fingerprint }),
  agentHooksList: () => call<any[]>("agent_hooks_list").then((r) => r ?? []),
  agentHooksPreview: (agent: string, install: boolean) =>
    callOrThrow<any>("agent_hooks_preview", { agent, install }),
  agentHooksApply: (agent: string, install: boolean, fingerprint: string) =>
    callOrThrow<string>("agent_hooks_apply", { agent, install, fingerprint }),
  statusLinePreview: (install: boolean) => callOrThrow<any>("status_line_preview", { install }),
  statusLineApply: (install: boolean, fingerprint: string) =>
    callOrThrow<string>("status_line_apply", { install, fingerprint }),
  codexPlanUsage: () => call<unknown>("codex_plan_usage"),

  approvalDecision: (requestId: string, decision: "allow" | "deny") => {
    pideAlCerebro({ type: "approval", requestId, decision });
    return call<void>("approval_decision", { requestId, decision });
  },
  approvalAck: (requestId: string) => call<void>("approval_ack", { requestId }),
  approvalAnswer: (requestId: string, answers: Record<string, string | string[]>) => {
    pideAlCerebro({ type: "approval_answer", requestId, answers });
    return call<void>("approval_answer", { requestId, answers });
  },
  approvalDecline: (requestId: string) => {
    pideAlCerebro({ type: "approval_decline", requestId });
    return call<void>("approval_decline", { requestId });
  },

  /** Aqui es donde el chat de Coucou pasa a hablar con el Cerebro de JARVIS. */
  chatSend: async (query: string, context: any): Promise<{ text: string }> => {
    const r = await pideAlCerebro({ type: "chat", text: query, context });
    const texto = (r && (r.text || r.reply || r.message)) || "";
    return { text: String(texto) };
  },
  chatReset: () => { pideAlCerebro({ type: "chat_reset" }); return call<void>("chat_reset"); },
  chatModels: (provider: string) => callOrThrow<any[]>("chat_models", { provider }),

  localSetKey: (url: string, key: string) => call<void>("local_set_key", { url, key }),
  localConnect: (provider: string, url: string) => callOrThrow<any>("local_connect", { provider, url }),
  ingestFile: (path: string) => callOrThrow<any>("ingest_file", { path }),
  secretPresent: (key: string) => call<boolean>("secret_present", { key }),
  secretSet: (key: string, value: string) => callOrThrow<void>("secret_set", { key, value }),
  secretClear: (key: string) => call<void>("secret_clear", { key }),

  refreshIntegration: (id: string) => call<void>("refresh_integration", { id }),
  githubRefresh: (section: string) => call<void>("github_refresh", { section }),
  openN8n: () => call<void>("open_n8n"),

  setPaused: (paused: boolean) => call<void>("set_paused", { paused }),

  shortcutsStatus: () => call<any>("shortcuts_status"),
  shortcutsSuspend: (suspended: boolean) => call<void>("shortcuts_suspend", { suspended }),

  recapHistory: (since: number) => recapHistoryDeJarvis(since),
  recapPrefs: () => call<RecapPrefs>("recap_prefs").then((r) => r ?? { enabled: true, hideProjects: false, lastShownWeek: "" }),
  recapSetEnabled: (enabled: boolean) => call<void>("recap_set_enabled", { enabled }),
  recapSetHideProjects: (hide: boolean) => call<void>("recap_set_hide_projects", { hide }),
  recapMarkShown: (week: string) => call<void>("recap_mark_shown", { week }),
  recapClear: () => call<void>("recap_clear"),
  recapSavePng: (data: string, week: string) => {
    host()?.savePng?.(data, week);
    return callOrThrow<string>("recap_save_png", { data, week });
  },
  recapRevealSaved: () => call<void>("reveal_saved"),

  // Mochi en el escritorio no existe en JARVIS: se desactiva.
  desktopInfo: () => call<any>("desktop_mochi_info").then(() => ({ mode: "off", supported: false, onDesktop: false })),
  desktopPickUp: (x: number, y: number) => call<boolean>("desktop_mochi_pick_up", { x, y }),
  desktopCarry: (x: number, y: number) => call<void>("desktop_mochi_carry", { x, y }),
  desktopCarryEnd: (x: number, y: number) => call<void>("desktop_mochi_carry_end", { x, y }),
  desktopDragBegin: () => call<[number, number] | null>("desktop_mochi_drag_begin"),
  desktopDragMove: (x: number, y: number) => call<void>("desktop_mochi_drag_move", { x, y }),
  desktopDragEnd: (x: number, y: number) => call<void>("desktop_mochi_drag_end", { x, y }),
  desktopFlyOut: (anywhere = false) => call<boolean>("desktop_mochi_fly_out", { anywhere }),
  desktopFlyHome: (forget: boolean) => call<boolean>("desktop_mochi_fly_home", { forget }),
  desktopSetAsleep: (asleep: boolean) => call<void>("desktop_mochi_set_asleep", { asleep }),

  spotifyRefresh: () => call<SpotifyState | null>("spotify_refresh"),
  spotifyControl: (action: string, value?: number) =>
    call<boolean>("spotify_control", { action, value: value ?? null }),
  spotifyOpen: () => call<boolean>("spotify_open"),
  spotifyInstalled: () => call<boolean>("spotify_installed"),
};

/** El resumen de JARVIS convertido al historial que Coucou ya sabe resumir. */
async function recapHistoryDeJarvis(since: number): Promise<RecapHistory> {
  const vacio: RecapHistory = { turns: [], decisions: [] } as RecapHistory;
  try {
    const dias = Math.max(1, Math.ceil((Date.now() / 1000 - since) / 86400));
    const r = await fetch("http://127.0.0.1:8000/api/recap?dias=" + dias);
    const d: any = await r.json();
    if (!d || !d.ok) return vacio;
    const ahora = Math.floor(Date.now() / 1000);
    const segundosPorSesion = (d.sesiones || 1) ? (d.segundos || 0) / d.sesiones : 0;
    return {
      turns: (d.accionesFichero || []).length ? [] : [{
        sessionId: "jarvis",
        agent: "JARVIS",
        cwd: null,
        startedAt: ahora - Math.floor(segundosPorSesion),
        lastActivity: ahora,
        seconds: Math.floor(segundosPorSesion),
        files: (d.ficheros || []).map((f: any) => ({ name: f.nombre, added: 0, removed: 0 })),
        commands: (d.comandos || []).reduce((a: number, c: any) => a + (c.veces || 0), 0),
        questions: 0,
      }],
      decisions: [],
    } as unknown as RecapHistory;
  } catch (e) {
    return vacio;
  }
}

export type SpotifyAction = "playPause" | "next" | "previous" | "seek" | "shuffle" | "repeat" | "volume";
export type ShortcutStatus = "active" | "off" | "inUse" | "duplicate" | "invalid" | "typesCharacter" | "unsupported" | "notPorted";
export type DesktopMode = "poll" | "window" | "layer" | "off";
export interface DesktopInfo { mode: DesktopMode; supported: boolean; onDesktop: boolean }
export interface ShortcutsReport { [k: string]: ShortcutStatus }

export async function emitToWindow(label: string, event: string, payload?: unknown) {
  void label; void event; void payload;
}

export interface IntegrationUpdate { id: string; data: Record<string, unknown>; error: string | null; event: { success: boolean; label: string; detail: string | null } | null }
export type ChatContext =
  | { kind: "window"; appName: string; title: string; url?: string }
  | { kind: "file"; name: string; path?: string }
  | null;
export interface ModelInfo { id: string; label: string; accent: string }
export interface LocalServer { ok: boolean; models: ModelInfo[]; error?: string }
export interface DroppedFile { name: string; path: string; bytes: number }
export interface HookStatus { installed: boolean; planRelayInstalled: boolean; settingsPath: string; hookPath: string; hookReady: boolean }
export interface AgentHookStatus { id: string; name: string; installed: boolean; path: string; hookReady: boolean }
export interface AgentHookPlan { diff: string; backup: string; path: string; fingerprint: string }
export interface HookPreview { diff: string; backup: string; settingsPath: string; fingerprint: string }
export type BridgeEvent =
  | "cursor" | "pointer-inside" | "tray" | "screen-changed" | "sounds-changed"
  | "settings-changed" | "recap-check" | "file-drag" | string;
export interface DragDropPayload { type: "enter" | "over" | "drop" | "leave"; paths?: string[] }

/** En un navegador no hay arrastre de Tauri, pero si los del DOM. */
export async function onDragDrop(handler: (e: DragDropPayload) => void) {
  const evita = (ev: Event) => { ev.preventDefault(); ev.stopPropagation(); };
  const rutas = (ev: DragEvent): string[] => {
    const out: string[] = [];
    if (ev.dataTransfer && ev.dataTransfer.files) {
      for (const f of Array.from(ev.dataTransfer.files)) out.push((f as any).path || f.name);
    }
    return out;
  };
  window.addEventListener("dragenter", (ev) => { evita(ev); handler({ type: "enter", paths: rutas(ev as DragEvent) }); });
  window.addEventListener("dragover", (ev) => { evita(ev); handler({ type: "over", paths: rutas(ev as DragEvent) }); });
  window.addEventListener("dragleave", (ev) => { evita(ev); handler({ type: "leave" }); });
  window.addEventListener("drop", (ev) => { evita(ev); handler({ type: "drop", paths: rutas(ev as DragEvent) }); });
  return { dispose() { /* nada que liberar */ } };
}

/** Suscripcion local: JARVIS dispara con emiteEvento. */
export async function onEvent<T>(name: string, handler: (payload: T) => void) {
  let set = oyentes.get(name);
  if (!set) { set = new Set(); oyentes.set(name, set); }
  set.add(handler as Handler);
  return {
    unregister: async () => { set!.delete(handler as Handler); },
  };
}