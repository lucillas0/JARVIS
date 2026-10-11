// App state — mirror of AppState.swift (the parts the island needs).
import { DEFAULT_MAIN_PILL, HOST_OS, availablePills, orderPills, pillDefinition, sanitizeDeclared, toggleDeclared, } from "./pills.js";
import { DEFAULT_OUTFIT } from "../mochi/wardrobe.js";
import { pillColor } from "./pill-colors.js";
import { Spotify, musicPlaying } from "./spotify.js";
/** A fresh, idle task for a catalog pill, in the colour the user gave it if any. */
function taskFor(def, colors, name = def.name) {
    return {
        id: def.id, name, color: pillColor(def.id, def.color, colors), state: "idle", stepIndex: 0, steps: [],
        source: def.source, isIntegration: true,
    };
}
export const DEFAULT_SETTINGS = {
    soundEnabled: true,
    soundVolume: 0.12,
    autoCloseInterval: 15,
    openOnHover: false,
    absenceInterval: 180,
    activeIntegrations: [
        "integration_resend", "integration_n8n", "integration_vercel", "integration_github",
    ],
    mainPill: DEFAULT_MAIN_PILL,
    screen: "primary",
    autostart: false,
    hooksInstalled: false,
    model: "claude-opus-5",
    showPlanInNotch: false,
    planRelayInstalled: false,
    showCodexPlanInNotch: false,
    chatProvider: "anthropic",
    chatModels: {},
    ollamaUrl: "",
    lmstudioUrl: "",
    customUrl: "",
    shortcuts: {},
    mochiOutfit: DEFAULT_OUTFIT,
    pillColors: {},
    language: "",
};
/** Live diffs kept per pill (oldest dropped first) — same cap as macOS. */
export const MAX_DIFFS_PER_PILL = 50;
/** A pill's diffs are forgotten after an hour without a new one, as on macOS. */
export const DIFF_TTL_MS = 3_600_000;
class AppState {
    mode = "hidden";
    view = "overview";
    tasks = [];
    focusId = null;
    stateOverride = null;
    /** Cursor in logical screen pixels, origin top-left (like AppState.mousePosition). */
    mouse = { x: 0, y: 0 };
    /** Cursor relative to the island's top-left corner. */
    mouseInIsland = { x: 0, y: 0 };
    isPinned = false;
    paused = false;
    uploadProgress = 0;
    uploadDuration = 2.4;
    fileDragOver = false;
    promptContext = null;
    droppedFile = null;
    noteMessage = null;
    searchResult = null;
    chatHistory = [];
    pendingApproval = null;
    /** The pill that was in front when the card came up; it comes back after. */
    focusBeforeApproval = null;
    integrations = {};
    /** Claude's 5 h / weekly limits, from the status line (null until the first call). */
    planUsage = null;
    /** Codex's limits, from `codex app-server` (null until it has answered). */
    codexPlanUsage = null;
    /** A plan card is open in place of the overview's left card. */
    showingPlanDetail = false;
    /** Which one: the Codex card rather than Claude's. */
    planDetailIsCodex = false;
    /** Per-pill file diffs, in order of reception. Steps carry their ids. */
    sessionDiffs = new Map();
    sessionDiffTimers = new Map();
    /** Never reset, so an id can never point at a newer diff than the one tapped. */
    nextDiffId = 0;
    /** Ctrl+↑ / Ctrl+↓: the highlighted row of the card's list (AppState.cardSelection). */
    cardSelection = null;
    /** Rows in the list on screen, 0 when there is none (AppState.cardItemCount). */
    cardItemCount = 0;
    /**
     * Mochi is out of the island — on the desktop, flying, or being dragged
     * there — so the island's own Mochi is hidden (AppState.mochiOnDesktop).
     */
    mochiOnDesktop = false;
    /** Outfit shown on Mochi while the pointer rests on a wardrobe button. */
    wardrobePreview = null;
    lastActivity = performance.now();
    settings = { ...DEFAULT_SETTINGS };
    /** Which pills this build offers depends on it (Claude Desktop is Windows only). */
    os = HOST_OS;
    listeners = new Set();
    subscribe(fn) {
        this.listeners.add(fn);
        return () => this.listeners.delete(fn);
    }
    /** Marks the UI dirty; the island re-renders on the next frame. */
    notify() {
        for (const fn of this.listeners)
            fn();
    }
    get focusTask() {
        return this.tasks.find((t) => t.id === this.focusId) ?? this.tasks[0] ?? null;
    }
    get effectiveState() {
        return this.stateOverride ?? this.focusTask?.state ?? "idle";
    }
    /** Spotify plays on a declared pill: Mochi dances (Linux; never on Windows yet). */
    get spotifyPlaying() {
        return musicPlaying(Spotify.state, sanitizeDeclared(this.settings, this.os).activeIntegrations);
    }
    get otherTasks() {
        return this.tasks.filter((t) => t.id !== this.focusId);
    }
    setFocus(id) {
        const t = this.tasks.find((x) => x.id === id);
        if (!t)
            return;
        this.focusId = id;
        this.showingPlanDetail = false;
        t.pillBadge = null;
        this.notify();
    }
    /**
     * A permission card or a question comes up: its pill comes to the front, and
     * the pill that was there is remembered (HookServer.focusBeforeApproval).
     */
    beginApproval(info) {
        this.pendingApproval = info;
        this.isPinned = true;
        if (this.focusBeforeApproval == null)
            this.focusBeforeApproval = this.focusId;
        this.setFocus(info.pillId);
    }
    /**
     * The card has its answer, or is withdrawn: the session carries on, and the
     * pill you were on comes back — unless you moved to another one meanwhile.
     */
    endApproval() {
        const req = this.pendingApproval;
        if (!req)
            return;
        this.pendingApproval = null;
        this.isPinned = false;
        this.updateTask(req.pillId, "working");
        this.setPillBadge(req.pillId, null);
        const previous = this.focusBeforeApproval;
        this.focusBeforeApproval = null;
        if (previous && this.focusId === req.pillId && this.tasks.some((t) => t.id === previous)) {
            this.focusId = previous;
        }
        this.notify();
    }
    updateTask(id, state) {
        const t = this.tasks.find((x) => x.id === id);
        if (!t)
            return;
        t.state = state;
        this.notify();
    }
    appendStep(id, step) {
        const t = this.tasks.find((x) => x.id === id);
        if (!t)
            return;
        const newest = t.stepSeq ?? t.steps.length - 1;
        t.steps.push(step);
        if (t.steps.length > 20)
            t.steps.shift();
        t.stepIndex = t.steps.length - 1;
        t.stepSeq = newest + 1;
        this.notify();
    }
    setPillBadge(id, badge) {
        const t = this.tasks.find((x) => x.id === id);
        if (!t)
            return;
        t.pillBadge = badge;
        this.notify();
    }
    /** Stores a diff for a pill and returns its id (for the ticker step). */
    appendSessionDiff(pillId, diff) {
        const id = this.nextDiffId++;
        const list = this.sessionDiffs.get(pillId) ?? [];
        list.push({ ...diff, id });
        while (list.length > MAX_DIFFS_PER_PILL)
            list.shift();
        this.sessionDiffs.set(pillId, list);
        // One timer per pill, re-armed on every diff — nothing polls.
        const prev = this.sessionDiffTimers.get(pillId);
        if (prev != null)
            window.clearTimeout(prev);
        this.sessionDiffTimers.set(pillId, window.setTimeout(() => this.clearSessionDiffs(pillId), DIFF_TTL_MS));
        return id;
    }
    findDiff(pillId, id) {
        return this.sessionDiffs.get(pillId)?.find((d) => d.id === id) ?? null;
    }
    clearSessionDiffs(pillId) {
        const timer = this.sessionDiffTimers.get(pillId);
        if (timer != null)
            window.clearTimeout(timer);
        this.sessionDiffTimers.delete(pillId);
        this.sessionDiffs.delete(pillId);
    }
    /** The always-on workspace pill, once the setting has been checked. */
    get mainPillId() {
        return sanitizeDeclared(this.settings, this.os).mainPill;
    }
    /**
     * True for a pill that stays when its session ends: the main pill and the
     * declared ones go back to idle instead of going away.
     */
    isKept(id) {
        const d = sanitizeDeclared(this.settings, this.os);
        return id === d.mainPill || d.activeIntegrations.includes(id);
    }
    /**
     * Loads the catalog pills: the main pill always, the declared ones, and none
     * of the others — a pill that is mid-session stays until its session ends.
     * Safe to call any number of times. AppState.loadIntegrationTasks on macOS.
     */
    loadIntegrationTasks() {
        const d = sanitizeDeclared(this.settings, this.os);
        this.settings.mainPill = d.mainPill;
        this.settings.activeIntegrations = d.activeIntegrations;
        for (const def of availablePills(this.os)) {
            const shouldLoad = def.id === d.mainPill || d.activeIntegrations.includes(def.id);
            const idx = this.tasks.findIndex((t) => t.id === def.id);
            if (shouldLoad && idx < 0)
                this.tasks.push(taskFor(def, this.settings.pillColors));
            const busy = idx >= 0 && (this.tasks[idx].state !== "idle" || this.tasks[idx].steps.length > 0);
            if (!shouldLoad && idx >= 0 && !busy)
                this.tasks.splice(idx, 1);
        }
        // A colour picked in Settings reaches the pills that are already up.
        for (const t of this.tasks) {
            const def = pillDefinition(t.id);
            if (def)
                t.color = pillColor(def.id, def.color, this.settings.pillColors);
        }
        this.tasks = orderPills(this.tasks, d.mainPill);
        if (!this.focusId || !this.tasks.some((t) => t.id === this.focusId))
            this.focusId = d.mainPill;
        this.notify();
    }
    /**
     * A session is over. The main and declared pills are put back as they were;
     * any other pill goes away (AppState.removeTask on macOS).
     */
    removeTask(id) {
        const idx = this.tasks.findIndex((t) => t.id === id);
        if (idx < 0)
            return;
        if (this.isKept(id)) {
            const t = this.tasks[idx];
            t.state = "idle";
            t.steps = [];
            t.stepIndex = 0;
            delete t.stepSeq;
            t.pillBadge = null;
            t.finalLine = null;
            const def = pillDefinition(id);
            if (def)
                t.name = def.name;
            this.clearSessionDiffs(id);
            this.notify();
            return;
        }
        this.tasks.splice(idx, 1);
        this.clearSessionDiffs(id);
        if (this.focusId === id)
            this.focusId = this.tasks[0]?.id ?? this.mainPillId;
        this.notify();
    }
    /**
     * Creates the pill of a tagged agent on its first event; no-op if it exists.
     * Inserted right after the main pill so it is in the visible slice(0,4). A
     * catalog agent wears its catalog colour, as on macOS.
     */
    upsertExternalAgent(id, name, color) {
        if (this.tasks.some((t) => t.id === id))
            return;
        const def = pillDefinition(id);
        this.insertAfterMain({
            id, name, color: def ? pillColor(def.id, def.color, this.settings.pillColors) : color,
            state: "idle", stepIndex: 0, steps: [],
            source: "agent", isIntegration: false,
        });
    }
    /**
     * The pill a Claude Code session belongs to (VS Code or Cursor). It is made
     * for the session when it is neither the main pill nor declared, as
     * upsertWorkspaceTask does on macOS.
     */
    upsertWorkspacePill(id, name, cwd) {
        let t = this.tasks.find((x) => x.id === id);
        if (!t) {
            const def = pillDefinition(id);
            if (!def)
                return null;
            t = taskFor(def, this.settings.pillColors, name);
            this.insertAfterMain(t);
        }
        t.name = name;
        if (cwd)
            t.sessionCwd = cwd;
        return t;
    }
    insertAfterMain(t) {
        const at = this.tasks.findIndex((x) => x.id === this.mainPillId) + 1;
        this.tasks.splice(at, 0, t);
        if (!this.focusId)
            this.focusId = t.id;
        this.notify();
    }
    /** Declares or undeclares a pill (max 4 next to the main one). */
    toggleIntegration(id) {
        const next = toggleDeclared(sanitizeDeclared(this.settings, this.os), id, this.os);
        if (!next)
            return;
        this.settings.activeIntegrations = next;
        if (!next.includes(id) && this.focusId === id)
            this.focusId = this.mainPillId;
        this.loadIntegrationTasks();
    }
    /**
     * What the island opens on. A card waiting for an answer comes first, so
     * reopening a folded island shows it again (Mac #117, #290).
     */
    defaultView() {
        if (this.pendingApproval)
            return this.pendingApproval.questions ? "question" : "approval";
        return this.tasks.length === 0 ? "empty" : "overview";
    }
}
export const State = new AppState();
