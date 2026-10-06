"""TTS: Piper TTS (local, gratis, MIT). Voz masculina española grave.

- Descarga automática del modelo de voz elegido en TTS_VOICE (= es_ES-davefx-medium
  por defecto, voz grave y calmada) a brain/models/piper/ si no existe.
- Sintetiza a un .wav temporal en RAM (bytes) y lo reproduce el AudioEngine.
- clean_for_speech(): el texto visible conserva Markdown, pero lo HABLADO va
  limpio (sin asteriscos, almohadillas, URLs ni emojis).
"""
import io
import logging
import os
import re
import threading
from pathlib import Path

import numpy as np

from config import Config

log = logging.getLogger("voice.tts")

_MODELS_DIR = Config.BRAIN_DIR / "models" / "piper"
_lock = threading.Lock()
_voice = None


def _voice_name() -> str:
    base = Config.TTS_VOICE
    return base + ("_medium" if "medium" not in base and not base.endswith("_medium") else "")


def ensure_voice() -> str:
    """Devuelve la ruta .onnx garantizando que el modelo esté descargado."""
    name = _voice_name()
    onnx = _MODELS_DIR / f"{name}.onnx"
    jsn = _MODELS_DIR / f"{name}.onnx.json"
    if onnx.exists() and jsn.exists():
        return str(onnx)

    _MODELS_DIR.mkdir(parents=True, exist_ok=True)
    parts = name.rsplit("_", 2)  # ej. es_ES / davefx / medium
    if len(parts) == 3:
        lang, speaker, qual = parts
    else:
        # fallback: asumir estructura lang-speaker-medium
        x = name.split("-")
        lang, speaker, qual = x[0] if len(x) > 0 else "es_ES", x[1] if len(x) > 1 else "davefx", "medium"
    base = f"https://huggingface.co/rhasspy/piper-voices/resolve/v1.0.0/es/{lang}/{speaker}/{qual}"
    urls = {
        onnx: f"{base}/{name}.onnx",
        jsn: f"{base}/{name}.onnx.json",
    }
    import urllib.request
    for target, url in urls.items():
        if target.exists():
            continue
        log.info("Descargando voz Piper %s desde HuggingFace…", name)
        try:
            urllib.request.urlretrieve(url, target)
        except Exception as e:
            log.warning("No pude descargar %s: %s", name, e)
            raise RuntimeError(f"No pude descargar la voz {name}. Revisa tu conexión.")
    return str(onnx)


# Rango amplio de emoji/símbolos que Piper deletrearía en voz alta.
_EMOJI_RE = re.compile(
    "[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF\uFE0F"
    "\u200D\u2190-\u21FF\u2300-\u23FF\u25A0-\u25FF\U0001F1E6-\U0001F1FF]",
    flags=re.UNICODE)


def clean_for_speech(text: str) -> str:
    """Quita Markdown y ruido para hablar: el texto visible no se toca."""
    s = str(text or "")
    # Bloques de código cercados: fuera las cercas, dentro el contenido.
    s = re.sub(r"```+\w*\n?", " ", s)
    # Negrita/cursiva/tachado/subrayado: queda el contenido.
    s = re.sub(r"\*\*(.+?)\*\*", r"\1", s)
    s = re.sub(r"__(.+?)__", r"\1", s)
    s = re.sub(r"~~(.+?)~~", r"\1", s)
    s = re.sub(r"(?<!\w)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"\1", s)
    s = re.sub(r"(?<!\w)_(?!_)(.+?)(?<!_)_(?!_)", r"\1", s)
    # Código inline: sin backticks.
    s = s.replace("`", "")
    # Enlaces [texto](url) → texto; URLs sueltas fuera.
    s = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", s)
    s = re.sub(r"https?://\S+|www\.\S+", " ", s)
    # Cabeceras, citas, listas y tablas → texto plano con pausas.
    s = re.sub(r"(?m)^\s*#{1,6}\s*", "", s)
    s = re.sub(r"(?m)^\s*>\s?", "", s)
    s = re.sub(r"(?m)^\s*([*+-]|\d+[.)])\s+", "", s)
    s = s.replace("|", ", ")
    s = re.sub(r"(?m)^\s*[-*_]{3,}\s*$", " ", s)
    # HTML residual y emojis.
    s = re.sub(r"<[^>]{1,60}>", " ", s)
    s = _EMOJI_RE.sub("", s)
    # Asteriscos/almohadillas sueltos que queden + espacios.
    s = re.sub(r"(\d)\s*\*\s*(\d)", r"\1 por \2", s)
    s = s.replace("*", " ").replace("#", " ")
    s = re.sub(r"[ \t]{2,}", " ", s)
    s = re.sub(r"\n{3,}", "\n\n", s)
    return s.strip()


def synthesize(text: str) -> np.ndarray:
    """Texto → audio float32 a 22050 Hz (limpio de Markdown para hablar)."""
    global _voice
    try:
        text = clean_for_speech(text)
        onnx = ensure_voice()
        import numpy as _np
        if not text:
            return _np.zeros(0, dtype=_np.float32)
        with _lock:
            if _voice is None:
                from piper import PiperVoice
                _voice = PiperVoice.load(onnx)
            chunks = list(_voice.synthesize(text))
        if not chunks:
            raise RuntimeError("Piper no generó audio.")

        # Piper entrega normalmente un fragmento por frase. Una pausa breve
        # ayuda a separar ideas y hace más inteligible la locución en español.
        silence = _np.zeros(int(22050 * 0.12), dtype=_np.int16)
        pieces = []
        for i, chunk in enumerate(chunks):
            piece = _np.asarray(chunk.audio_int16_array, dtype=_np.int16).reshape(-1)
            if piece.size:
                pieces.append(piece)
                if i < len(chunks) - 1:
                    pieces.append(silence)
        if not pieces:
            raise RuntimeError("Piper generó audio vacío.")
        samples = _np.concatenate(pieces).astype(_np.float32) / 32768.0
        peak = float(_np.max(_np.abs(samples)))
        if peak > 1e-5:
            samples *= min(1.12, 0.88 / peak)
        return _np.clip(samples, -0.98, 0.98)
    except Exception as e:
        log.exception("Piper falló: %s", e)
        raise


def speak(text: str, engine) -> bool:
    """Sintetiza y encola la reproducción (la vuelta de llamada del AudioEngine
    hace el barge-in). Devuelve True si pudo encolar audio."""
    if not text:
        return False
    try:
        audio = synthesize(text)
        if not len(audio):
            return False
        # El modelo es 22.05 kHz pero AudioEngine reproduce a 16 kHz. Si se
        # etiqueta como 16 kHz sin convertir, la voz sale lenta y grave.
        from voice.audio import RATE, _resample_linear
        sample_rate = int(getattr(getattr(_voice, "config", None), "sample_rate", 22050))
        if sample_rate != RATE:
            audio = _resample_linear(audio, sample_rate, RATE)
        # Sin cambios grandes de pitch/tempo: la configuración antigua tenía
        # 1.12 y hacía que el habla sonara demasiado acelerada.
        speak_rate = min(1.04, max(0.96, float(getattr(Config, "TTS_SPEED", 1.0))))
        if abs(speak_rate - 1.0) > 0.01:
            idx = np.minimum((np.arange(0, len(audio), speak_rate)).astype(int), len(audio) - 1)
            audio = audio[idx]
        engine.play_data(audio)
        return True
    except Exception as e:
        log.warning("Fallo al hablar: %s", e)
        return False