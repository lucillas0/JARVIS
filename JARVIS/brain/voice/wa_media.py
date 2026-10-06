"""Medios para el puente de WhatsApp: opus/ogg <-> wav/float32 (ffmpeg estático).

- Notas de voz entrantes (ogg/opus) → float32 16k mono → STT (Whisper).
- Respuestas de voz: TTS (Piper) → ogg/opus apto para nota de voz.
"""
import base64
import logging
import shutil
import subprocess

import numpy as np

log = logging.getLogger("voice.wa_media")

_IN_RATE = 16000
_TTS_RATE = 22050


def _ffmpeg() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except Exception:
        exe = shutil.which("ffmpeg")
        if not exe:
            raise RuntimeError("Sin ffmpeg (ni imageio-ffmpeg ni ffmpeg en PATH).")
        return exe


def _run_ffmpeg(args: list[str], stdin_data: bytes) -> bytes:
    exe = _ffmpeg()
    p = subprocess.run(
        [exe, "-hide_banner", "-loglevel", "error", *args],
        input=stdin_data,
        capture_output=True,
        timeout=120,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if p.returncode != 0:
        raise RuntimeError(f"ffmpeg falló: {p.stderr.decode(errors='ignore')[:300]}")
    return p.stdout


def ogg_to_float32(data: bytes, rate: int = _IN_RATE) -> np.ndarray:
    """ogg/opus (nota de voz) → float32 mono a `rate` Hz."""
    raw = _run_ffmpeg(
        ["-i", "pipe:0", "-ar", str(rate), "-ac", "1", "-f", "f32le", "pipe:1"],
        data,
    )
    if not raw:
        raise RuntimeError("ffmpeg no extrajo audio.")
    return np.frombuffer(raw, dtype=np.float32).copy()


def float32_to_ogg(audio: np.ndarray, rate: int = _TTS_RATE) -> bytes:
    """float32 mono → ogg/opus (nota de voz de WhatsApp)."""
    pcm16 = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16).tobytes()
    return _run_ffmpeg(
        ["-f", "s16le", "-ar", str(rate), "-ac", "1", "-i", "pipe:0",
         "-ar", "16000", "-ac", "1", "-c:a", "libopus", "-b:a", "32k",
         "-f", "ogg", "pipe:1"],
        pcm16,
    )


def transcribe_ogg(data: bytes) -> str:
    """Nota de voz (ogg/opus) → texto con Whisper. '' si no se puede."""
    try:
        from voice.stt import transcribe
        audio = ogg_to_float32(data)
        if len(audio) < _IN_RATE // 4:  # <0.25s: ruido
            return ""
        return transcribe(audio) or ""
    except Exception as e:
        log.warning("transcribe_ogg: %s", e)
        return ""


def synthesize_ogg(text: str, max_chars: int = 1200) -> bytes:
    """Texto → ogg/opus con la voz de JARVIS (Piper)."""
    from voice.tts import synthesize
    t = (text or "").strip()[:max_chars]
    if not t:
        raise RuntimeError("Nada que sintetizar.")
    return float32_to_ogg(synthesize(t))


def b64_to_bytes(b64: str) -> bytes:
    return base64.b64decode(b64.encode() if isinstance(b64, str) else b64)
