// The Spotify pill on the page: what src-tauri/src/spotify.rs reports (Linux,
// through MPRIS), and the pure rules the views and Mochi follow — ports of
// SpotifyController.swift, NowPlayingViews.swift and the Mac's dance rules
// (BotCanvasView, DesktopMochi.swift).
//
// Windows has no music source yet: nothing ever reports a track there, so
// nothing plays and Mochi never dances, through the same code.
export const SPOTIFY_ID = "integration_spotify";
/** SpotifyController.green. */
export const SPOTIFY_GREEN = "#1DB954";
export const IDLE_SPOTIFY = {
    running: false, installed: false, track: null, playing: false,
    position: 0, positionAt: 0, shuffle: false, repeat: false, volume: 50,
};
/** The page's copy of the player, and the cover of the track that has one. */
export const Spotify = {
    state: { ...IDLE_SPOTIFY },
    artwork: null,
};
/** The cover to show for the current track, if it has arrived. */
export function currentArtwork(s = Spotify.state) {
    const art = Spotify.artwork;
    return art && s.track?.artUrl && art.artUrl === s.track.artUrl ? art.dataUrl : null;
}
/** SpotifyController.position(at:): extrapolated while playing, capped at the end. */
export function spotifyPosition(s, nowMs) {
    const elapsed = s.playing ? Math.max(0, (nowMs - s.positionAt) / 1000) : 0;
    const p = s.position + elapsed;
    const d = s.track?.duration ?? 0;
    return d > 0 ? Math.min(p, d) : p;
}
/**
 * The clock frozen or restarted where it is now — what a play/pause click
 * does at once, before Spotify confirms it (SpotifyController.setPlaying).
 */
export function withPlaying(s, playing, nowMs) {
    if (s.playing === playing)
        return s;
    return { ...s, position: spotifyPosition(s, nowMs), positionAt: nowMs, playing };
}
export function isAd(track) {
    return track?.id.startsWith("spotify:ad:") ?? false;
}
/** NowPlayingProgress.format: m:ss, or h:mm:ss past an hour. */
export function formatTime(seconds) {
    const s = Math.max(0, Math.floor(Number.isFinite(seconds) ? seconds : 0));
    const pad = (n) => String(n).padStart(2, "0");
    return s >= 3600
        ? `${Math.floor(s / 3600)}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}`
        : `${Math.floor(s / 60)}:${pad(s % 60)}`;
}
/** NowPlayingVolume.icon: 0 = muted, then one, two or three waves. */
export function volumeLevel(volume) {
    if (volume <= 0)
        return 0;
    if (volume < 34)
        return 1;
    if (volume < 67)
        return 2;
    return 3;
}
/** Music is playing on a declared Spotify pill. */
export function musicPlaying(s, activeIntegrations) {
    return s.playing && s.track != null && activeIntegrations.includes(SPOTIFY_ID);
}
/** The states Mochi dances in; the rest (an alert, an error, sleep) win. */
const DANCE_STATES = new Set(["idle", "working", "thinking", "searching", "finished"]);
/**
 * The island's Mochi (BotCanvasView, macOS): in the compact island whenever
 * music plays, expanded only on the overview with the music pill in front.
 */
export function islandDances(o) {
    if (!o.music || !DANCE_STATES.has(o.state))
        return false;
    if (o.mode === "compact")
        return true;
    return o.mode === "expanded" && o.view === "overview" && o.focusId === SPOTIFY_ID;
}
/** Mochi on the desktop: the compact island's rules (DesktopMochi.swift). */
export function desktopDances(music, state) {
    return music && DANCE_STATES.has(state);
}
