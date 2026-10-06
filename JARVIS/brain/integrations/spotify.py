"""Spotify — control, búsqueda de canciones y playlists (sección 9).

Usa spotipy con flujo de dispositivo (device flow): una vez autorizado, JARVIS
puede controlar reproducción y editar playlists de la cuenta personal.
Requiere SPOTIFY_CLIENT_ID y SPOTIFY_CLIENT_SECRET en .env.
"""
import logging
import os
import time
from pathlib import Path

from config import Config

log = logging.getLogger("integrations.spotify")

_sp = None
_token_path = str(Path(Config.BRAIN_DIR) / "db" / "spotify_token.json")


def _auth_flow():
    try:
        import spotipy
        from spotipy.oauth2 import SpotifyOAuth
        scope = "user-read-playback-state user-modify-playback-state playlist-modify-public playlist-modify-private playlist-read-private"
        return spotipy.Spotify(auth_manager=SpotifyOAuth(
            client_id=Config.SPOTIFY_CLIENT_ID,
            client_secret=Config.SPOTIFY_CLIENT_SECRET,
            redirect_uri="http://localhost:8888/callback",
            scope=scope,
            cache_path=_token_path,
            open_browser=True,
        ))
    except Exception as e:
        log.warning("Spotify auth falló: %s", e)
        return None


def _get():
    global _sp
    if _sp is None and Config.SPOTIFY_CLIENT_ID:
        _sp = _auth_flow()
    return _sp


def available() -> bool:
    return bool(Config.SPOTIFY_CLIENT_ID and Config.SPOTIFY_CLIENT_SECRET)


def play(paused_only: bool = False) -> dict:
    sp = _get()
    if not sp:
        raise RuntimeError("Spotify no configurado.")
    sp.start_playback()
    return {"playing": True}


def pause() -> dict:
    sp = _get()
    if not sp:
        raise RuntimeError("Spotify no configurado.")
    sp.pause_playback()
    return {"paused": True}


def next_track() -> dict:
    sp = _get()
    if not sp:
        raise RuntimeError("Spotify no configurado.")
    sp.next_track()
    return {"next": True}


def previous_track() -> dict:
    sp = _get()
    if not sp:
        raise RuntimeError("Spotify no configurado.")
    sp.previous_track()
    return {"previous": True}


def search_tracks(query: str, limit: int = 5) -> list[dict]:
    sp = _get()
    if not sp:
        raise RuntimeError("Spotify no configurado.")
    res = sp.search(q=query, type="track", limit=limit)
    return [{"name": t["name"], "artist": ", ".join(a["name"] for a in t["artists"]),
             "uri": t["uri"], "duration_ms": t.get("duration_ms")}
            for t in res.get("tracks", {}).get("items", [])]


def create_playlist(name: str, description: str = "") -> dict:
    sp = _get()
    if not sp:
        raise RuntimeError("Spotify no configurado.")
    user = sp.current_user()
    pl = sp.user_playlist_create(user["id"], name, public=True, description=description)
    return {"id": pl["id"], "name": name}


def add_to_playlist(playlist_id: str, track_uris: list[str]) -> dict:
    sp = _get()
    if not sp:
        raise RuntimeError("Spotify no configurado.")
    sp.playlist_add_items(playlist_id, track_uris)
    return {"added": True, "playlist": playlist_id, "tracks": track_uris}


def get_playlists(limit: int = 10) -> list[dict]:
    sp = _get()
    if not sp:
        raise RuntimeError("Spotify no configurado.")
    pl = sp.current_user_playlists(limit=limit)
    return [{"id": p["id"], "name": p["name"]} for p in pl.get("items", [])]


def play_context(playlist_id: str) -> dict:
    sp = _get()
    if not sp:
        raise RuntimeError("Spotify no configurado.")
    sp.start_playback(context_uri=f"spotify:playlist:{playlist_id}")
    return {"playing": True, "context": playlist_id}


def play_track(uri: str) -> dict:
    sp = _get()
    if not sp:
        raise RuntimeError("Spotify no configurado.")
    sp.start_playback(uris=[uri])
    return {"playing": True, "uri": uri}