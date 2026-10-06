"""Google Calendar — consulta y crear/editar/eliminar eventos (sección 9).

Requiere credenciales OAuth descargadas de Google Cloud (archivo JSON apuntado en
GOOGLE_CALENDAR_CREDENTIALS_PATH). Si no hay credenciales, las funciones devuelven
una señal clara sin romper nada.
"""
import json
import logging
import os
from datetime import datetime, timedelta
from pathlib import Path

from config import Config

log = logging.getLogger("integrations.calendar")

_service = None
_service_loaded = False


def _load_service():
    global _service, _service_loaded
    if _service_loaded:
        return _service
    _service_loaded = True
    creds_path = Config.GOOGLE_CALENDAR_CREDENTIALS_PATH
    if not creds_path or not os.path.exists(creds_path):
        log.info("No hay credenciales de Google Calendar en %s", creds_path)
        return None
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        token_path = str(Path(creds_path).with_suffix(".token.json"))
        scopes = ["https://www.googleapis.com/auth/calendar"]
        creds = None
        if os.path.exists(token_path):
            creds = Credentials.from_authorized_user_file(token_path, scopes)
        if not creds or not creds.valid:
            if creds and creds.expired and creds.refresh_token:
                creds.refresh(Request())
            else:
                flow = InstalledAppFlow.from_client_secrets_file(creds_path, scopes)
                creds = flow.run_local_server(port=0)
            with open(token_path, "w") as f:
                f.write(creds.to_json())
        _service = build("calendar", "v3", credentials=creds)
        return _service
    except Exception as e:
        log.warning("Calendar auth falló: %s", e)
        return None


def available() -> bool:
    return _load_service() is not None


def list_today(limit: int = 10) -> list[dict]:
    srv = _load_service()
    if not srv:
        return []
    start = datetime.now().isoformat() + "Z"
    end = (datetime.now() + timedelta(days=1)).isoformat() + "Z"
    events = srv.events().list(
        calendarId="primary", timeMin=start, timeMax=end,
        singleEvents=True, orderBy="startTime", maxResults=limit
    ).execute().get("items", [])
    return [{"summary": e.get("summary", ""),
             "start": e.get("start", {}).get("dateTime") or e.get("start", {}).get("date", ""),
             "url": e.get("htmlLink", "")} for e in events]


def create_event(summary: str, start_dt: str, end_dt: str = "", description: str = "") -> dict:
    srv = _load_service()
    if not srv:
        raise RuntimeError("No configurado Google Calendar.")
    if not end_dt:
        start = datetime.fromisoformat(start_dt)
        end_dt = (start + timedelta(hours=1)).isoformat()
    body = {"summary": summary, "start": {"dateTime": start_dt},
            "end": {"dateTime": end_dt}, "description": description}
    ev = srv.events().insert(calendarId="primary", body=body).execute()
    return {"id": ev.get("id"), "summary": summary, "start": start_dt, "url": ev.get("htmlLink")}


def update_event(event_id: str, **changes) -> dict:
    srv = _load_service()
    if not srv:
        raise RuntimeError("No configurado Google Calendar.")
    ev = srv.events().get(calendarId="primary", eventId=event_id).execute()
    for k, v in changes.items():
        ev[k] = v
    ev = srv.events().update(calendarId="primary", eventId=event_id, body=ev).execute()
    return {"id": event_id, "summary": ev.get("summary")}


def delete_event(event_id: str) -> dict:
    srv = _load_service()
    if not srv:
        raise RuntimeError("No configurado Google Calendar.")
    srv.events().delete(calendarId="primary", eventId=event_id).execute()
    return {"deleted": event_id}