// Entry point: boot the bridge, wire the island, start the greeting.
import { Bridge, IS_TAURI, onEvent } from "./core/bridge.js";
import { Sound } from "./core/sound.js";
import { State } from "./core/state.js";
import { Island } from "./island/island.js";
import { registerHookHandlers } from "./island/hooks.js";
import { registerIntegrationHandlers, refreshConfigured } from "./island/integrations.js";
import { registerShortcutHandlers } from "./island/shortcuts.js";
import { applySpotify, registerSpotifyHandlers } from "./island/spotify.js";
import { SPOTIFY_ID } from "./core/spotify.js";
import { Recap } from "./recap/recap.js";
import { onLanguageChange, resolveLanguage, setLanguage, systemLanguages } from "./i18n/i18n.js";
/** Shows the language Settings asks for ("" = the system's, when Coucou has it). */
function applyLanguage() {
    setLanguage(resolveLanguage(State.settings.language, systemLanguages()));
}
async function main() {
    const root = document.getElementById("root");
    if (!root)
        return;
    void Sound.preload();
    const island = new Island(root);
    const notchApi = globalThis.jarvisNotchAPI;
    let historyRevision = 0;
    let historyLoad = Promise.resolve();
    const refreshConversation = () => {
        const revision = ++historyRevision;
        historyLoad = Promise.resolve(notchApi?.getConversation?.(100)).then((rows) => {
            if (revision !== historyRevision || !Array.isArray(rows)) return;
            State.chatHistory = rows.map((row, index) => ({ id: row.id || `history-${index}`, role: row.role, content: row.content }))
                .filter((row) => (row.role === "user" || row.role === "assistant") && row.content);
            State.notify();
        }).catch((error) => console.error("[coucou] No se pudo sincronizar la conversación:", error));
        return historyLoad;
    };
    const openView = (view) => {
        State.paused = false;
        void Bridge.setPaused(false);
        island.alert(view);
        if (view === "prompt") island.takeKeyboard();
    };
    const removeSubscriptions = [
        notchApi?.onIslandToggle?.(() => openView(State.defaultView())),
        notchApi?.onIslandTab?.((tab) => {
            if (tab === "chat") openView("prompt");
            else if (tab === "new") {
                State.droppedFile = null;
                State.promptContext = null;
                openView("prompt");
                void refreshConversation();
            } else openView("overview");
        }),
        notchApi?.onIslandSend?.(() => {
            openView("prompt");
            window.setTimeout(() => document.querySelector(".chat-input")?.dispatchEvent(
                new KeyboardEvent("keydown", { key: "Enter", bubbles: true })), 140);
        }),
        notchApi?.onIslandMute?.(() => {
            State.settings.soundEnabled = !State.settings.soundEnabled;
            Sound.setEnabled(State.settings.soundEnabled);
            void Bridge.saveSettings(State.settings);
            State.notify();
        }),
        notchApi?.onConversationChanged?.(() => void refreshConversation()),
        notchApi?.onRestore?.(() => { State.paused = false; island.alert(State.defaultView()); }),
        notchApi?.onReset?.(() => island.fsm.forcePetit()),
        notchApi?.onHide?.(() => island.fsm.forceHidden()),
        notchApi?.onAutoCollapse?.(() => island.collapse()),
    ].filter((remove) => typeof remove === "function");
    const boot = await Bridge.boot();
    if (boot) {
        State.settings = { ...State.settings, ...boot.settings };
    }
    // A language change redraws the island in place: the texts given as tl(…)
    // relabel themselves (views/dom.ts) and the views redraw the rest on this
    // sync. Nothing is rebuilt, so tasks, steps and the chat stay as they are.
    onLanguageChange(() => State.notify());
    applyLanguage();
    // Rust shows the tray and its errors in the same language as the webview.
    void Bridge.setSystemLanguages(systemLanguages());
    island.applySettings();
    State.loadIntegrationTasks();
    if (boot && !boot.cursorPoll)
        island.followPageCursor();
    await refreshConversation();
    await island.desktop.init();
    await onEvent("cursor", ({ x, y }) => island.onCursor(x, y));
    await onEvent("pointer-inside", (inside) => island.setPointerInside(inside));
    await onEvent("jarvis-reply", (reply) => {
        if (reply?.persisted) void refreshConversation();
    });
    await onEvent("jarvis-abierto", () => { void refreshConversation(); });
    /** Pause has to reach Rust too, or the pollers keep calling out. */
    const setPaused = (on) => {
        if (State.paused === on)
            return;
        State.paused = on;
        void Bridge.setPaused(on);
    };
    await onEvent("tray", (what) => {
        switch (what) {
            case "settings":
                setPaused(false);
                island.alert("settings");
                break;
            case "open":
                setPaused(false);
                island.alert(State.defaultView());
                break;
            case "recap":
                setPaused(false);
                void Recap.open(island);
                break;
            case "wardrobe":
                setPaused(false);
                island.alert("wardrobe");
                break;
            case "pause":
                setPaused(!State.paused);
                if (State.paused)
                    island.fsm.forceHidden();
                else
                    island.reveal();
                break;
        }
    });
    await onEvent("screen-changed", () => void Bridge.reposition());
    // Settings → Reload sounds: read the sounds folder again, and let them hear it.
    await onEvent("sounds-changed", () => {
        void Sound.reload().then(() => Sound.play("pop"));
    });
    // The settings window writes preferences; apply them here without a restart.
    await onEvent("settings-changed", (s) => {
        const previousMain = State.mainPillId;
        State.settings = { ...State.settings, ...s };
        applyLanguage();
        island.applySettings();
        State.loadIntegrationTasks();
        // A new main tool comes to the front, as on macOS.
        if (State.mainPillId !== previousMain)
            State.setFocus(State.mainPillId);
        void refreshConfigured();
    });
    registerHookHandlers(island);
    registerIntegrationHandlers(island);
    registerShortcutHandlers(island, () => setPaused(false));
    registerSpotifyHandlers(island);
    // Rust may have read Spotify before this page listened: ask once.
    if (State.settings.activeIntegrations.includes(SPOTIFY_ID)) {
        void Bridge.spotifyRefresh().then((s) => s && applySpotify(island, s));
    }
    // Monday recap: app start (greeting over), an agent starting work, waking up.
    const checkRecap = () => void Recap.check(island);
    island.onGreetingDone = checkRecap;
    island.onWake = checkRecap;
    await onEvent("recap-check", checkRecap);
    island.launch();
    // In a plain browser there is no wake strip behind the cursor: make the whole
    // page wake the island so the visuals can be checked with `npm run dev`.
    if (!IS_TAURI) {
        document.addEventListener("click", () => Sound.resume(), { once: true });
    }        window.addEventListener("unload", () => removeSubscriptions.forEach((remove) => remove()), { once: true });
}
void main();
