// Mochi on the desktop, island side: runs his life cycle (the controller in
// mochi/desktop-logic.ts) against the real app, tells the desktop window what
// to show, and handles the drag out of the island. The window itself lives in
// src-tauri/src/desktop.rs and draws him in src/desktop/main.ts.
import { Bridge, emitToWindow, onEvent } from "../core/bridge.js";
import { Sound } from "../core/sound.js";
import { State } from "../core/state.js";
import { desktopDances } from "../core/spotify.js";
import { DESKTOP_EVENTS, DesktopMochiController, alertActive, } from "../mochi/desktop-logic.js";
import { SeasonCache, parseOutfit } from "../mochi/wardrobe.js";
/** Label of the desktop Mochi's window (desktop.rs LABEL). */
const WINDOW = "mochi";
export class DesktopLink {
    mode = "off";
    controller;
    /** The pointer is dragging Mochi out of the island. */
    carrying = false;
    seasons = new SeasonCache();
    pushed = "";
    pendingCarry = null;
    carryFrame = false;
    host;
    constructor(host) {
        this.host = host;
        this.controller = new DesktopMochiController({
            flyOut: async (anywhere) => (await Bridge.desktopFlyOut(anywhere)) ?? false,
            flyHome: async (forget) => (await Bridge.desktopFlyHome(forget)) ?? true,
            setAway: (away) => {
                State.mochiOnDesktop = away;
                State.notify();
            },
            emote: (emote, duration = 1.8) => void emitToWindow(WINDOW, DESKTOP_EVENTS.emote, { emote, duration }),
            play: (sound) => Sound.play(sound),
            alertActive: () => alertActive(State),
            revealIsland: () => {
                if (State.mode === "hidden")
                    this.host.reveal();
            },
            later: (fn, ms) => void window.setTimeout(fn, ms),
        });
    }
    /** False where windows can't be placed (GNOME on Wayland): he stays in the island. */
    get supported() {
        return this.mode !== "off";
    }
    async init() {
        const info = await Bridge.desktopInfo();
        if (!info || info.mode === "off")
            return;
        this.mode = info.mode;
        this.controller.enabled = info.onDesktop;
        await onEvent(DESKTOP_EVENTS.dropped, (e) => this.onDropped(e.from, e.home));
        await onEvent(DESKTOP_EVENTS.home, () => void this.controller.flyHome());
        await onEvent(DESKTOP_EVENTS.wardrobe, () => this.host.wardrobeFromDesktop());
        await onEvent(DESKTOP_EVENTS.dizzy, () => this.host.dizzyFromDesktop());
        // The window's page (re)loaded: it knows nothing yet.
        await onEvent(DESKTOP_EVENTS.ready, () => {
            this.pushed = "";
            this.push();
        });
        State.subscribe(() => this.sync());
        this.sync();
    }
    /** The launch greeting is over: back to his spot if that is where he lives. */
    launch() {
        if (this.supported)
            void this.controller.launchFlyIfNeeded();
    }
    /**
     * The desktop shortcut: out to his spot, or home. Not in the middle of a
     * drag out of the island — the drop decides that one.
     */
    flyOutOrHome() {
        if (!this.supported || this.carrying)
            return;
        void this.controller.flyOutOrHome();
    }
    // ── Drag out of the island ──────────────────────────────────────────────────
    /** Whether Mochi can be picked up from the island right now. */
    canPickUp() {
        return this.supported && !State.paused && !State.mochiOnDesktop && this.controller.phase === "home";
    }
    /** (x, y): the pointer, island-window coordinates. */
    pickUp(x, y) {
        this.carrying = true;
        State.mochiOnDesktop = true;
        State.notify();
        void Bridge.desktopPickUp(x, y).then((ok) => {
            if (ok)
                return;
            this.carrying = false;
            State.mochiOnDesktop = false;
            State.notify();
        });
    }
    /** The pointer moved during the drag. Windows carries him from Rust. */
    carry(x, y) {
        if (!this.carrying || this.mode === "poll")
            return;
        this.pendingCarry = { x, y };
        if (this.carryFrame)
            return;
        this.carryFrame = true;
        requestAnimationFrame(() => {
            this.carryFrame = false;
            const p = this.pendingCarry;
            this.pendingCarry = null;
            if (p && this.carrying)
                void Bridge.desktopCarry(p.x, p.y);
        });
    }
    /** The button went up. On Windows the cursor poll has seen it already. */
    carryEnd(x, y) {
        if (!this.carrying || this.mode === "poll")
            return;
        this.pendingCarry = null;
        void Bridge.desktopCarryEnd(x, y);
    }
    onDropped(from, home) {
        if (from === "island") {
            this.carrying = false;
            if (home) {
                // Dropped back on the island: he is simply there again.
                State.mochiOnDesktop = false;
                State.notify();
            }
            else {
                this.controller.installed();
            }
        }
        else if (home) {
            void this.controller.flyHome();
        }
    }
    // ── State → desktop window ──────────────────────────────────────────────────
    sync() {
        this.controller.updateAlert(alertActive(State));
        this.controller.updateState(State.effectiveState);
        this.push();
    }
    push() {
        const snapshot = {
            state: State.effectiveState,
            outfit: State.wardrobePreview ?? this.seasons.get(parseOutfit(State.settings.mochiOutfit)),
            soundEnabled: State.settings.soundEnabled,
            soundVolume: State.settings.soundVolume,
            paused: State.paused,
            dancing: desktopDances(State.spotifyPlaying, State.effectiveState),
        };
        const key = JSON.stringify(snapshot);
        if (key === this.pushed)
            return;
        this.pushed = key;
        void emitToWindow(WINDOW, DESKTOP_EVENTS.state, snapshot);
    }
}
