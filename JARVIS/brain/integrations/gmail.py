"""Gmail — lectura de correos (resumen de remitente y contenido) (sección 9).

Sin capacidad de enviar. Requiere credenciales OAuth en la misma carpeta que
Calendar (GOOGLE_CALENDAR_CREDENTIALS_PATH) con scope gmail.readonly. Si no hay
credenciales, devuelve vacío sin romper.
"""
import base64
import logging
import os
from pathlib import Path

from config import Config

log = logging.getLogger("integrations.gmail")

_service = None


def _load_service():
    global _service
    if _service is not None:
        return _service
    creds_path = Config.GOOGLE_CALENDAR_CREDENTIALS_PATH
    if not creds_path or not os.path.exists(creds_path):
        log.info("Sin credenciales de Google para Gmail.")
        return None
    try:
        from google.auth.transport.requests import Request
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from googleapiclient.discovery import build
        scopes = ["https://www.googleapis.com/auth/gmail.readonly"]
        token_path = str(Path(creds_path).with_suffix(".gmail.token.json"))
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
        _service = build("gmail", "v1", credentials=creds)
        return _service
    except Exception as e:
        log.warning("Gmail auth falló: %s", e)
        return None


def _snippet(message: dict) -> str:
    try:
        payload = message.get("payload", {})
        body = payload.get("body", {})
        if body.get("data"):
            return base64.urlsafe_b64decode(body["data"]).decode("utf-8", "ignore")
        parts = payload.get("parts", [])
        for p in parts:
            m = p.get("mimeType", "")
            if m in ("text/plain", "text/html") and p.get("body", {}).get("data"):
                text = base64.urlsafe_b64decode(p["body"]["data"]).decode("utf-8", "ignore")
                return text[:800]
    except Exception as e:
        log.debug("snippet falló: %s", e)
    return message.get("snippet", "") or ""


def unread_summary(limit: int = 10) -> list[dict]:
    srv = _load_service()
    if not srv:
        return []
    res = srv.users().messages().list(userId="me", q="is:unread in:inbox", maxResults=limit).execute()
    out = []
    from email import utils
    for m in res.get("messages", []):
        meta = srv.users().messages().get(userId="me", id=m["id"],
                                          format="metadata", metadataHeaders=["From", "Subject", "Date"]).execute()
        headers = {h["name"].lower(): h["value"] for h in meta.get("payload", {}).get("headers", [])}
        from_hdr = headers.get("from", "?")
        parsed = utils.parseaddr(from_hdr) if from_hdr else ("", from_hdr)
        out.append({"from": parsed[1] or from_hdr, "name": parsed[0],
                    "subject": headers.get("subject", ""),
                    "date": headers.get("date", ""),
                    "snippet": meta.get("snippet", "")})
    return out