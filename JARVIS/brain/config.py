"""Configuración central del brain. Lee brain/.env, con valores por defecto."""
import os
from pathlib import Path
from dotenv import load_dotenv

BRAIN_DIR = Path(__file__).resolve().parent
ROOT_DIR = BRAIN_DIR.parent
ASSETS_DIR = ROOT_DIR / "assets"

load_dotenv(BRAIN_DIR / ".env")

# Directorio de datos persistente (venv + db), fuera de la instalación.
# La app lo inyecta vía JARVIS_DATA_DIR; en desarrollo cae en brain/db.
_DATA_DIR = Path(os.environ.get("JARVIS_DATA_DIR", "").strip() or (BRAIN_DIR / "db"))
try:
    _DATA_DIR.mkdir(parents=True, exist_ok=True)
except OSError:
    pass
DATA_DIR = _DATA_DIR

# Fuente única de verdad para las claves: keys.env manda sobre brain/.env, para
# que el launcher y el brain nunca divergan (incidente del token Discord).
#
# Se buscan dos sitios porque son los dos que sobreviven a una actualización:
#   · %APPDATA%/JARVIS/keys.env  -> Windows (instalador y app de escritorio)
#   · DATA_DIR/keys.env          -> Linux, NAS y Docker (el volumen /data)
#
# El segundo es el que faltaba: en un contenedor, APPDATA no existe, asi que las
# claves que se ponian en /setup se guardaban dentro del contenedor y se perdian
# en cuanto se recreaba. Ahora van al volumen y sobreviven a la actualizacion.
def _cargar_claves(ruta: Path) -> None:
    try:
        if not ruta.is_file():
            return
        with open(ruta, "r", encoding="utf-8-sig") as _f:
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


_appd = os.environ.get("APPDATA", "")
if _appd:
    _cargar_claves(Path(_appd) / "JARVIS" / "keys.env")
_cargar_claves(DATA_DIR / "keys.env")
# Por si el volumen se montó en otra parte y alguien lo fio en brain/.
_cargar_claves(BRAIN_DIR / "keys.env")


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
    STT_MODEL = _get("STT_MODEL", "whisper-large-v3-turbo")
    TTS_VOICE = _get("TTS_VOICE", "es_ES-davefx-medium")
    TTS_SPEED = float(_get("TTS_SPEED", "1.12"))
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

    # ------------------------------------------------------------------ #
    # Memoria persistente · Mem0 OSS autoalojado
    # ------------------------------------------------------------------ #
    # La memoria deJARVIS es un servicio aparte (Mem0 OSS + PostgreSQL/pgvector)
    # que corre en el mismo NAS. Aquí solo hay dirección y política; el
    # cliente HTTP vive en memory/mem0_client.py y la politica en
    # memory/manager.py. Ningun modulo habla con Mem0 directamente.
    #
    # MEMORY_ENABLED=0 apaga TODO y JARVIS sigue igual, con su SQLite y su
    # Qdrant de siempre. La memoria nueva es una MAS, no un sustituto: si el
    # servicio se cae, no se rompe la conversacion.
    MEMORY_ENABLED = _get("MEMORY_ENABLED", "1").strip().lower() in (
        "1", "true", "si", "sí", "yes", "on")
    MEM0_URL = _get("MEM0_URL", "http://mem0:8888").rstrip("/")
    # El servidor OSS acepta un X-API-Key por usuario. Si la instancia lleva
    # AUTH_DISABLED=true (que es lo recomendado en una red domestica) esta
    # vacia y no hace falta.
    MEM0_API_KEY = _get("MEM0_API_KEY")
    # Identidad estable. Con uno solo JARVIS es de un usuario y vale "default".
    # El sistema esta preparado para varios sin tocar nada mas.
    MEMORY_USER_ID = _get("MEMORY_USER_ID", "default")
    # Cuantos recuerdos se meten en el prompt. Mas es mas contexto y mas
    # latencia; menos es contexto insuficiente.
    MEMORY_MAX_RESULTS = int(_get("MEMORY_MAX_RESULTS", "5"))
    # Cada cuanto se reintenta si Mem0 no responde. La memoria es
    # degradable: se avisa en el log y se sigue contestando.
    MEMORY_RETRY_SECONDS = int(_get("MEMORY_RETRY_SECONDS", "30"))
    MEMORY_TIMEOUT = float(_get("MEMORY_TIMEOUT", "6"))
    # Si el señor dice "recuerda X", se guarda siempre. Con esto a 0 solo se
    # guarda lo que el analizador vea claro, y las peticiones explicitas se
    # ignorarian.
    MEMORY_RESPETA_PETICION = _get("MEMORY_RESPETA_PETICION", "1").strip().lower() in (
        "1", "true", "si", "sí", "yes", "on")
    # Guardar solo lo que dura. Con esto a 0 se guarda cada conversacion, que
    # es justo lo que NO hay que hacer.
    MEMORY_SOLO_UTILES = _get("MEMORY_SOLO_UTILES", "1").strip().lower() in (
        "1", "true", "si", "sí", "yes", "on")

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

    # ------------------------------------------------------------------ #
    # AI Gateway (brain/ai_gateway)
    # ------------------------------------------------------------------ #
    # La API oficial de Groq es compatible con OpenAI: mismo formato de
    # /chat/completions, mismo streaming por Server-Sent Events. Se llama por
    # HTTP con urllib para poder emitir cada trozo por el bus y cortar a mitad.
    GROQ_BASE_URL = _get("GROQ_BASE_URL", "https://api.groq.com/openai/v1").rstrip("/")
    # Los tres modelos por tipo de tarea. Si GROQ_FAST_MODEL / REASONING /
    # DEFAULT no se ponen, se caen a los nombres antiguos de arriba, para que
    # una instalacion que ya funciona no se quede sin modelos.
    GROQ_DEFAULT_MODEL = _get("GROQ_DEFAULT_MODEL", "")
    GROQ_FAST_MODEL = _get("GROQ_FAST_MODEL", "")
    GROQ_REASONING_MODEL = _get("GROQ_REASONING_MODEL", "")
    GROQ_TEMPERATURE = float(_get("GROQ_TEMPERATURE", "0.7"))
    GROQ_MAX_TOKENS = int(_get("GROQ_MAX_TOKENS", "0") or 0)

    # Tiempos y reintentos del gateway, compartidos por todos los proveedores.
    LLM_TIMEOUT_SECONDS = float(_get("LLM_TIMEOUT_SECONDS", "45"))
    LLM_MAX_RETRIES = int(_get("LLM_MAX_RETRIES", "2"))
    LLM_MAX_CONCURRENT = int(_get("LLM_MAX_CONCURRENT", "4"))
    # Streaming y herramientas se pueden apagar sin tocar codigo.
    LLM_STREAM_ENABLED = _get("LLM_STREAM_ENABLED", "1").strip().lower() in (
        "1", "true", "si", "sí", "yes", "on")
    LLM_TOOLS_ENABLED = _get("LLM_TOOLS_ENABLED", "1").strip().lower() in (
        "1", "true", "si", "sí", "yes", "on")

    # Cuantos caracteres de contexto (memoria, documentos, apuntes, misión,
    # estado) caben como mucho en una peticion. El prompt de sistema y el
    # historial NO cuentan: los pone siempre el camino de conversacion. Con lo
    # que cabe, lo que no se puede guardar se recorta y se avisa, en vez de
    # reventar la ventana del modelo (o el bolsillo del que paga la API).
    CONTEXT_MAX_CHARS = int(_get("CONTEXT_MAX_CHARS", "6000"))

    DB_PATH = str(DATA_DIR / "jarvis.db")
    QDRANT_PATH = str(DATA_DIR / "qdrant")
    AUDIO_THEME_PATH = _get("AUDIO_THEME_PATH", str(ASSETS_DIR / "audio" / "wake-up-theme.mp3"))

    @classmethod
    def has_any_key(cls) -> bool:
        return bool(cls.GROQ_API_KEY or cls.GEMINI_API_KEY or cls.OPENROUTER_API_KEY)

    @classmethod
    def has_key(cls, provider: str) -> bool:
        return bool(getattr(cls, f"{provider.upper()}_API_KEY", ""))