// Puente de Coucou talks con Tauri. Aqui se sustituye por el mismo objeto con la
// misma forma, pero hablando con el Cerebro de JARVIS por WebSocket y con el
// proceso principal de Electron por el preload. Todo lo demas sigue siendo un
// no-op, igual que en un navegador normal (main.ts ya tiene la rama !IS_TAURI).
//
// La clave: este fichero conserva los nombres, los argumentos y los tipos de
// return del original, para que island.ts, views/ y core/ compilen sin cambios.
export const IS_TAURI = false;
const oyentes = new Map();
/** JARVIS dispara un evento de Coucou (lo usa el puente de Electron). */
export function emiteEvento(nombre, payload) {
    const set = oyentes.get(nombre);
    if (!set)
        return;
    for (const h of [...set]) {
        try {
            h(payload);
        }
        catch (e) {
            console.error("[coucou] evento " + nombre, e);
        }
    }
}
async function call(cmd, args) {
    // Sin Tauri todas las llamadas son no-op, como en el navegador.
    void cmd;
    void args;
    return null;
}
async function callOrThrow(cmd, args) {
    void cmd;
    void args;
    throw new Error("sin Tauri");
}
// ── JARVIS: el chat y el resto van al Cerebro ───────────────────────────────
const CEREBO = "ws://127.0.0.1:8000/ws";
let socket = null;
const pendientes = new Map();
let contador = 0;
function conecta() {
    if (socket && (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING))
        return;
    try {
        socket = new WebSocket(CEREBO);
    }
    catch (e) {
        socket = null;
        return;
    }
    socket.onopen = () => { emiteEvento("jarvis-abierto"); };
    socket.onclose = () => { socket = null; setTimeout(conecta, 3000); };
    socket.onerror = () => { };
    socket.onmessage = (ev) => {
        let m;
        try {
            m = JSON.parse(ev.data);
        }
        catch (e) {
            return;
        }
        // Respuesta a un chat que pidio el puente.
        const id = m.id;
        if (id && pendientes.has(id)) {
            const res = pendientes.get(id);
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
export function jarvisEstado() {
    if (!socket)
        return "sin";
    return socket.readyState === WebSocket.OPEN ? "conectado" : "conectando";
}
async function pideAlCerebro(mensaje, ms = 90000) {
    conecta();
    if (!socket || socket.readyState !== WebSocket.OPEN) {
        return new Promise((resolve) => setTimeout(() => resolve(null), ms));
    }
    const id = "coucou-" + ++contador;
    return new Promise((resolve) => {
        const t = setTimeout(() => { pendientes.delete(id); resolve(null); }, ms);
        pendientes.set(id, (v) => { clearTimeout(t); resolve(v); });
        try {
            socket.send(JSON.stringify({ ...mensaje, id }));
        }
        catch (e) {
            clearTimeout(t);
            pendientes.delete(id);
            resolve(null);
        }
    });
}
/** Las pastillas que el usuario tiene declaradas, con JARVIS como principal. */
const DECLARADAS = [
    "integration_claude", // Visual Studio Code
    "agent_opencode", // OpenCode
    "freebuff", // Freebuff
    "agent_codex",
    "agent_gemini",
];
const AJUSTES = {
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
};
/** Electron expone jarvisNotchAPI en el renderer. */
function host() {
    return globalThis.jarvisNotchAPI || null;
}
export const Bridge = {
    boot: () => call("boot").then(() => ({
        settings: AJUSTES,
        screen: { x: 0, y: 0, width: screen.width, height: screen.height, scale: 1 },
        version: "jarvis",
        hookPath: "",
        // En Electron el raton lo vigila el proceso principal, no la pagina.
        cursorPoll: true,
    })),
    setSystemLanguages: (languages) => call("set_system_languages", { languages }),
    saveSettings: (settings) => {
        Object.assign(AJUSTES, settings);
        return call("save_settings", { settings });
    },
    setCollapsed: (collapsed) => {
        host()?.setCollapsed?.(collapsed);
        return call("set_collapsed", { collapsed });
    },
    setIslandRect: (x, y, width, height) => {
        host()?.setIslandRect?.(x, y, width, height);
        return call("set_island_rect", { x, y, width, height });
    },
    focusWindow: (focused) => {
        host()?.focusWindow?.(focused);
        return call("focus_window", { focused });
    },
    reposition: () => call("reposition"),
    listMonitors: () => call("list_monitors")
        .then((r) => r ?? [{ key: "primary", label: "Principal" }]),
    openUrl: (url) => {
        host()?.openExternal?.(url);
        return call("open_url", { url });
    },
    customSounds: (names) => call("custom_sounds", { names }),
    customSound: (name) => call("custom_sound", { name }),
    revealSoundsFolder: () => call("reveal_sounds_folder"),
    reloadSounds: () => call("reload_sounds"),
    openInVSCode: (path) => call("open_in_vscode", { path }),
    openSession: (sessionId, path) => call("open_session", { sessionId, path }),
    openClaudeDesktop: () => call("open_claude_desktop"),
    openFileInVSCode: (path) => {
        host()?.openPath?.(path);
        return call("open_file_in_vscode", { path });
    },
    quit: () => { host()?.quit?.(); return call("quit_app"); },
    openSettingsWindow: () => call("open_settings_window"),
    log: (message) => call("log_line", { message }),
    // JARVIS no usa los hooks de Claude Code: se contestan vacios.
    hooksStatus: () => call("hooks_status"),
    agentHooksStatus: () => call("agent_hooks_status"),
    hooksPreview: (install) => callOrThrow("hooks_preview", { install }),
    hooksApply: (install, fingerprint) => callOrThrow("hooks_apply", { install, fingerprint }),
    agentHooksList: () => call("agent_hooks_list").then((r) => r ?? []),
    agentHooksPreview: (agent, install) => callOrThrow("agent_hooks_preview", { agent, install }),
    agentHooksApply: (agent, install, fingerprint) => callOrThrow("agent_hooks_apply", { agent, install, fingerprint }),
    statusLinePreview: (install) => callOrThrow("status_line_preview", { install }),
    statusLineApply: (install, fingerprint) => callOrThrow("status_line_apply", { install, fingerprint }),
    codexPlanUsage: () => call("codex_plan_usage"),
    approvalDecision: (requestId, decision) => {
        pideAlCerebro({ type: "approval", requestId, decision });
        return call("approval_decision", { requestId, decision });
    },
    approvalAck: (requestId) => call("approval_ack", { requestId }),
    approvalAnswer: (requestId, answers) => {
        pideAlCerebro({ type: "approval_answer", requestId, answers });
        return call("approval_answer", { requestId, answers });
    },
    approvalDecline: (requestId) => {
        pideAlCerebro({ type: "approval_decline", requestId });
        return call("approval_decline", { requestId });
    },
    /** Aqui es donde el chat de Coucou pasa a hablar con el Cerebro de JARVIS. */
    chatSend: async (query, context) => {
        const r = await pideAlCerebro({ type: "chat", text: query, context });
        const texto = (r && (r.text || r.reply || r.message)) || "";
        return { text: String(texto) };
    },
    chatReset: () => { pideAlCerebro({ type: "chat_reset" }); return call("chat_reset"); },
    chatModels: (provider) => callOrThrow("chat_models", { provider }),
    localSetKey: (url, key) => call("local_set_key", { url, key }),
    localConnect: (provider, url) => callOrThrow("local_connect", { provider, url }),
    ingestFile: (path) => callOrThrow("ingest_file", { path }),
    secretPresent: (key) => call("secret_present", { key }),
    secretSet: (key, value) => callOrThrow("secret_set", { key, value }),
    secretClear: (key) => call("secret_clear", { key }),
    refreshIntegration: (id) => call("refresh_integration", { id }),
    githubRefresh: (section) => call("github_refresh", { section }),
    openN8n: () => call("open_n8n"),
    setPaused: (paused) => call("set_paused", { paused }),
    shortcutsStatus: () => call("shortcuts_status"),
    shortcutsSuspend: (suspended) => call("shortcuts_suspend", { suspended }),
    recapHistory: (since) => recapHistoryDeJarvis(since),
    recapPrefs: () => call("recap_prefs").then((r) => r ?? { enabled: true, hideProjects: false, lastShownWeek: "" }),
    recapSetEnabled: (enabled) => call("recap_set_enabled", { enabled }),
    recapSetHideProjects: (hide) => call("recap_set_hide_projects", { hide }),
    recapMarkShown: (week) => call("recap_mark_shown", { week }),
    recapClear: () => call("recap_clear"),
    recapSavePng: (data, week) => {
        host()?.savePng?.(data, week);
        return callOrThrow("recap_save_png", { data, week });
    },
    recapRevealSaved: () => call("reveal_saved"),
    // Mochi en el escritorio no existe en JARVIS: se desactiva.
    desktopInfo: () => call("desktop_mochi_info").then(() => ({ mode: "off", supported: false, onDesktop: false })),
    desktopPickUp: (x, y) => call("desktop_mochi_pick_up", { x, y }),
    desktopCarry: (x, y) => call("desktop_mochi_carry", { x, y }),
    desktopCarryEnd: (x, y) => call("desktop_mochi_carry_end", { x, y }),
    desktopDragBegin: () => call("desktop_mochi_drag_begin"),
    desktopDragMove: (x, y) => call("desktop_mochi_drag_move", { x, y }),
    desktopDragEnd: (x, y) => call("desktop_mochi_drag_end", { x, y }),
    desktopFlyOut: (anywhere = false) => call("desktop_mochi_fly_out", { anywhere }),
    desktopFlyHome: (forget) => call("desktop_mochi_fly_home", { forget }),
    desktopSetAsleep: (asleep) => call("desktop_mochi_set_asleep", { asleep }),
    spotifyRefresh: () => call("spotify_refresh"),
    spotifyControl: (action, value) => call("spotify_control", { action, value: value ?? null }),
    spotifyOpen: () => call("spotify_open"),
    spotifyInstalled: () => call("spotify_installed"),
};
/** El resumen de JARVIS convertido al historial que Coucou ya sabe resumir. */
async function recapHistoryDeJarvis(since) {
    const vacio = { turns: [], decisions: [] };
    try {
        const dias = Math.max(1, Math.ceil((Date.now() / 1000 - since) / 86400));
        const r = await fetch("http://127.0.0.1:8000/api/recap?dias=" + dias);
        const d = await r.json();
        if (!d || !d.ok)
            return vacio;
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
                    files: (d.ficheros || []).map((f) => ({ name: f.nombre, added: 0, removed: 0 })),
                    commands: (d.comandos || []).reduce((a, c) => a + (c.veces || 0), 0),
                    questions: 0,
                }],
            decisions: [],
        };
    }
    catch (e) {
        return vacio;
    }
}
export async function emitToWindow(label, event, payload) {
    void label;
    void event;
    void payload;
}
/** En un navegador no hay arrastre de Tauri, pero si los del DOM. */
export async function onDragDrop(handler) {
    const evita = (ev) => { ev.preventDefault(); ev.stopPropagation(); };
    const rutas = (ev) => {
        const out = [];
        if (ev.dataTransfer && ev.dataTransfer.files) {
            for (const f of Array.from(ev.dataTransfer.files))
                out.push(f.path || f.name);
        }
        return out;
    };
    window.addEventListener("dragenter", (ev) => { evita(ev); handler({ type: "enter", paths: rutas(ev) }); });
    window.addEventListener("dragover", (ev) => { evita(ev); handler({ type: "over", paths: rutas(ev) }); });
    window.addEventListener("dragleave", (ev) => { evita(ev); handler({ type: "leave" }); });
    window.addEventListener("drop", (ev) => { evita(ev); handler({ type: "drop", paths: rutas(ev) }); });
    return { dispose() { } };
}
/** Suscripcion local: JARVIS dispara con emiteEvento. */
export async function onEvent(name, handler) {
    let set = oyentes.get(name);
    if (!set) {
        set = new Set();
        oyentes.set(name, set);
    }
    set.add(handler);
    return {
        unregister: async () => { set.delete(handler); },
    };
}
