"""Configuración central del brain. Lee brain/.env, con valores por defecto."""
import os
from pathlib import Path
from dotenv import load_dotenv

BRAIN_DIR = Path(__file__).resolve().parent
ROOT_DIR = BRAIN_DIR.parent
ASSETS_DIR = ROOT_DIR / "assets"

load_dotenv(BRAIN_DIR / ".env")

# Fuente única de verdad: keys.env (%APPDATA%/JARVIS) manda sobre brain/.env.
# Así el launcher y el brain nunca divergen (incidente del token Discord).
try:
    _appd = os.environ.get("APPDATA", "")
    _keys = os.path.join(_appd, "JARVIS", "keys.env") if _appd else ""
    if _keys and os.path.isfile(_keys):
        with open(_keys, "r", encoding="utf-8-sig") as _f:
            for _line in _f:
                _line = _line.strip()
                if not _line or _line.startswith("#") or "=" not in _line:
                    continue
                _k, _, _v = _line.partition("=")
                _k, _v = _k.strip(), _v.strip().strip('"')
                if _k and _v:
                    os.environ[_k] = _v
except Exception:
    pass

# Directorio de datos persistente (venv + db), fuera de la instalación.
# La app lo inyecta vía JARVIS_DATA_DIR; en desarrollo cae en brain/db.
_DATA_DIR = Path(os.environ.get("JARVIS_DATA_DIR", "").strip() or (BRAIN_DIR / "db"))
try:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass
DATA_DIR = _DATA_DIR


def _get(key: str, default: str = "") -> str:
    return os.environ.get(key, default).strip()


class Config:
    BRAIN_DIR = BRAIN_DIR
    ROOT_DIR = ROOT_DIR
    ASSETS_DIR = ASSETS_DIR

    # API keys
    GROQ_API_KEY = _get("GROQ_API_KEY")
    GEMINI_API_KEY = _get("GEMINI_API_KEY")
    OPENROUTER_API_KEY = _get("OPENROUTER_API_KEY")

    # Integraciones
    GOOGLE_CALENDAR_CREDENTIALS_PATH = _get("GOOGLE_CALENDAR_CREDENTIALS_PATH", str(BRAIN_DIR / "credentials" / "google_calendar.json"))
    SPOTIFY_CLIENT_ID = _get("SPOTIFY_CLIENT_ID")
    SPOTIFY_CLIENT_SECRET = _get("SPOTIFY_CLIENT_SECRET")
    WEATHER_API_KEY = _get("WEATHER_API_KEY")
    WEATHER_UNIT = _get("WEATHER_UNIT", "metric")

    # Fallback local
    OLLAMA_HOST = _get("OLLAMA_HOST", "http://localhost:11434")
    FALLBACK_MODEL = _get("FALLBACK_MODEL", "llama3.2:1b")

    # Riesgo / auditoría
    AUDIT_LOG_RETENTION_DAYS = int(_get("AUDIT_LOG_RETENTION_DAYS", "30"))
    CONFIRMATION_TIMEOUT_SECONDS = int(_get("CONFIRMATION_TIMEOUT_SECONDS", "30"))

    # Contexto
    CONTEXT_WINDOW_MESSAGES = int(_get("CONTEXT_WINDOW_MESSAGES", "15"))

    # Voz
    WAKE_WORD = _get("WAKE_WORD", "jarvis").lower()
    STT_MODEL = _get("STT_MODEL", "whisper-large-v3")
    TTS_VOICE = _get("TTS_VOICE", "es_ES-davefx-medium")
    TTS_SPEED = float(_get("TTS_SPEED", "1.0"))
    RECALL_ENABLED = True
    CONVERSATION_SILENCE_SECONDS = float(_get("CONVERSATION_SILENCE_SECONDS", "2.5"))
    MIC_SAMPLE_RATE = int(_get("MIC_SAMPLE_RATE", "16000"))
    MIC_DEVICE = int(_get("MIC_DEVICE", "0") or 0)

    # Puente Discord (DM privado con el propietario)
    DISCORD_BOT_TOKEN = _get("DISCORD_BOT_TOKEN")
    DISCORD_OWNER_ID = _get("DISCORD_OWNER_ID")
    DISCORD_PREFIX = _get("DISCORD_PREFIX", "")

    # Invitado VirtualBox (Guest Control dentro de las VMs)
    VBOX_GUEST_USER = _get("VBOX_GUEST_USER")
    VBOX_GUEST_PASSWORD = _get("VBOX_GUEST_PASSWORD")

    # Campus AVIESAU (Moodle del ciclo)
    CAMPUS_USER = _get("CAMPUS_USER")
    CAMPUS_PASSWORD = _get("CAMPUS_PASSWORD")
    CAMPUS_URL = _get("CAMPUS_URL", "https://av.ciclosallerulloa.gal")

    # Raspberry Pi "lucillas" (SSH en 22) + Nextcloud en el mismo servidor
    RASPBERRY_HOST = _get("RASPBERRY_HOST", "lucillas.ddns.net")
    RASPBERRY_PORT = _get("RASPBERRY_PORT", "22")
    RASPBERRY_USER = _get("RASPBERRY_USER")
    RASPBERRY_PASSWORD = _get("RASPBERRY_PASSWORD")
    NEXTCLOUD_URL = _get("NEXTCLOUD_URL", "https://lucillas.ddns.net/nextcloud")
    NEXTCLOUD_USER = _get("NEXTCLOUD_USER")
    NEXTCLOUD_PASSWORD = _get("NEXTCLOUD_PASSWORD")

    # VirusTotal (análisis de ficheros/URLs)
    VIRUSTOTAL_API_KEY = _get("VIRUSTOTAL_API_KEY")

    # Avisos push al móvil (ntfy.sh; tema propio opcional)
    NTIFY_TOPIC = _get("NTIFY_TOPIC", "")

    # Visión
    SCREEN_ANALYSIS_MODE = _get("SCREEN_ANALYSIS_MODE", "continuous")
    SCREEN_CAPTURE_INTERVAL_SECONDS = int(_get("SCREEN_CAPTURE_INTERVAL_SECONDS", "5"))

    # Puertos
    BRAIN_HOST = _get("BRAIN_HOST", "127.0.0.1")
    BRAIN_PORT = int(_get("BRAIN_PORT", "8000"))

    # Model Router
    GROQ_MODEL_FAST = _get("GROQ_MODEL_FAST", "openai/gpt-oss-120b")
    GROQ_MODEL_LIGHT = _get("GROQ_MODEL_LIGHT", "openai/gpt-oss-20b")
    GROQ_MODEL_VISION = _get("GROQ_MODEL_VISION", "openai/gpt-oss-120b")
    GEMINI_MODEL_HEAVY = _get("GEMINI_MODEL_HEAVY", "gemini-3.6-flash")
    OPENROUTER_MODEL = _get("OPENROUTER_MODEL", "openai/gpt-4o-mini")
    OPENROUTER_VISION_MODEL = _get("OPENROUTER_VISION_MODEL", "openai/gpt-4o-mini")
    GEMINI_VISION_MODEL = _get("GEMINI_VISION_MODEL", "gemini-3.6-flash")

    DB_PATH = str(DATA_DIR / "jarvis.db")
    QDRANT_PATH = str(DATA_DIR / "qdrant")
    AUDIO_THEME_PATH = _get("AUDIO_THEME_PATH", str(ASSETS_DIR / "audio" / "wake-up-theme.mp3"))

    @classmethod
    def has_any_key(cls) -> bool:
        return bool(cls.GROQ_API_KEY or cls.GEMINI_API_KEY or cls.OPENROUTER_API_KEY)

    @classmethod
    def has_key(cls, provider: str) -> bool:
        return bool(getattr(cls, f"{provider.upper()}_API_KEY", ""))