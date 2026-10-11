// Spotify's reports → the page (src-tauri/src/spotify.rs, Linux). The parts of
// SpotifyController.swift that touch the app: the pill wears the track's
// title, music starting shows the compact island without a sound, and the
// card reads the player again whenever it comes on screen (seeks made in
// Spotify's own window are never signalled).
import { Bridge, onEvent } from "../core/bridge.js";
import { pillDefinition } from "../core/pills.js";
import { SPOTIFY_ID, Spotify, isAd } from "../core/spotify.js";
import { State } from "../core/state.js";
import { t } from "../i18n/i18n.js";
export function registerSpotifyHandlers(island) {
    void onEvent("spotify", (s) => applySpotify(island, s));
    void onEvent("spotify-artwork", (art) => {
        Spotify.artwork = art;
        State.notify();
    });
    State.subscribe(() => {
        syncPillName();
        refreshWhenShown();
    });
}
/** A report from Rust (or the answer to a refresh). */
export function applySpotify(island, next) {
    const wasPlaying = State.spotifyPlaying;
    Spotify.state = next;
    syncPillName();
    // Only on not playing → playing (SpotifyController.setPlaying).
    if (!wasPlaying && State.spotifyPlaying && !State.paused && State.mode === "hidden") {
        island.revealSilently();
    }
    State.notify();
}
/** SpotifyController.syncTaskName: the track's title, else the pill's name. */
export function syncPillName() {
    const task = State.tasks.find((x) => x.id === SPOTIFY_ID);
    if (!task)
        return;
    const track = Spotify.state.track;
    const title = isAd(track) ? t("Advertisement") : (track?.title ?? "");
    const name = title || (pillDefinition(SPOTIFY_ID)?.name ?? "Spotify");
    if (task.name !== name)
        task.name = name;
}
let shown = false;
function refreshWhenShown() {
    const now = State.mode === "expanded" && State.view === "overview" && State.focusTask?.id === SPOTIFY_ID;
    if (now && !shown)
        void Bridge.spotifyRefresh();
    shown = now;
}
