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
_voice_sample_rate = 22050


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
    # Nombre habitual: es_ES-davefx-medium (también se aceptan guiones bajos).
    match = re.fullmatch(r"([a-z]{2}_[A-Z]{2})[-_]([a-zA-Z0-9_-]+)[-_](low|medium|high)", name)
    if match:
        lang, speaker, qual = match.groups()
    else:
        raise RuntimeError(f"Nombre de voz Piper no válido: {name}")
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
        tmp = target.with_name(target.name + ".download")
        try:
            urllib.request.urlretrieve(url, tmp)
            os.replace(tmp, target)
        except Exception as e:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass
            log.warning("No pude descargar %s: %s", name, e)
            raise RuntimeError(f"No pude descargar la voz {name}. Revisa tu conexión.") from e
    return str(onnx)


def _load_voice_locked():
    """Carga Piper una sola vez; quien llama debe mantener _lock."""
    global _voice, _voice_sample_rate
    if _voice is None:
        onnx = ensure_voice()
        from piper import PiperVoice
        _voice = PiperVoice.load(onnx)
        config_rate = getattr(getattr(_voice, "config", None), "sample_rate", None)
        if config_rate:
            _voice_sample_rate = int(config_rate)
        else:
            try:
                import json
                with open(onnx + ".json", "r", encoding="utf-8") as f:
                    _voice_sample_rate = int(json.load(f).get("audio", {}).get("sample_rate", 22050))
            except Exception:
                _voice_sample_rate = 22050
    return _voice, _voice_sample_rate


def preload() -> bool:
    """Carga el modelo local en segundo plano al iniciar, sin sintetizar audio."""
    try:
        with _lock:
            _load_voice_locked()
        log.info("Piper listo (modelo cargado, %s Hz).", _voice_sample_rate)
        return True
    except Exception as e:
        log.warning("No pude precargar Piper; se intentará al hablar: %s", e)
        return False


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


def _synthesize_with_rate(text: str) -> tuple[np.ndarray, int]:
    """Texto → audio float32 y frecuencia nativa declarada por el modelo."""
    try:
        text = clean_for_speech(text)
        if not text:
            return np.zeros(0, dtype=np.float32), _voice_sample_rate
        with _lock:
            voice, sample_rate = _load_voice_locked()
            chunks = list(voice.synthesize(text))
        if not chunks:
            raise RuntimeError("Piper no generó audio.")

        parts = []
        for chunk in chunks:
            # piper-tts expone audio_int16_bytes; algunas versiones/forks
            # ofrecen también audio_int16_array. Admitimos ambas APIs.
            samples = getattr(chunk, "audio_int16_array", None)
            if samples is not None:
                parts.append(np.asarray(samples, dtype=np.int16).reshape(-1))
            else:
                raw = getattr(chunk, "audio_int16_bytes", None)
                if raw is None:
                    raise RuntimeError("Piper devolvió un fragmento sin audio PCM.")
                parts.append(np.frombuffer(raw, dtype=np.int16))
            sample_rate = int(getattr(chunk, "sample_rate", sample_rate) or sample_rate)
        samples = np.concatenate(parts).astype(np.int16, copy=False)
        return samples.astype(np.float32) / 32768.0, sample_rate
    except Exception as e:
        log.exception("Piper falló: %s", e)
        raise


def synthesize(text: str) -> np.ndarray:
    """Texto → audio float32 a la frecuencia nativa del modelo (normalmente 22050 Hz)."""
    return _synthesize_with_rate(text)[0]


def _resample(audio: np.ndarray, source_rate: int, target_rate: int) -> np.ndarray:
    """Remuestrea linealmente mono; evita reproducir 22050 Hz como si fueran 16 kHz."""
    if source_rate == target_rate or not len(audio):
        return audio.astype(np.float32, copy=False)
    count = max(1, int(round(len(audio) * target_rate / source_rate)))
    positions = np.arange(count, dtype=np.float32) * (source_rate / target_rate)
    positions = np.minimum(positions, len(audio) - 1)
    left = positions.astype(np.intp)
    right = np.minimum(left + 1, len(audio) - 1)
    fraction = positions - left
    return (audio[left] * (1.0 - fraction) + audio[right] * fraction).astype(np.float32)


def speak(text: str, engine) -> bool:
    """Sintetiza y encola la reproducción (la vuelta de llamada del AudioEngine
    hace el barge-in). Devuelve True si pudo encolar audio."""
    if not text:
        return False
    try:
        audio, source_rate = _synthesize_with_rate(text)
        if not len(audio):
            return False
        target_rate = int(getattr(engine, "playback_rate", 16000) or 16000)
        audio = _resample(audio, source_rate, target_rate)
        try:
            speak_rate = float(getattr(Config, "TTS_SPEED", 1.0))
        except (TypeError, ValueError):
            speak_rate = 1.0
        speak_rate = min(1.4, max(0.8, speak_rate))
        if abs(speak_rate - 1.0) > 0.01:
            idx = np.arange(0, len(audio), speak_rate).astype(np.intp)
            audio = audio[idx]
        engine.play_data(audio)
        return True
    except Exception as e:
        log.warning("Fallo al hablar: %s", e)
        return False