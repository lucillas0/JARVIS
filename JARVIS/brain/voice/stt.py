"""STT: Groq Whisper (nube). Convierte audio 16k mono float32 → texto."""
import logging
import wave
from pathlib import Path

import numpy as np

from config import Config
from model_router import ModelRouter

log = logging.getLogger("voice.stt")


def _float32_to_wav_bytes(audio: np.ndarray, rate: int = 16000) -> bytes:
    pcm = (np.clip(audio, -1.0, 1.0) * 32767).astype(np.int16)
    import io
    buf = io.BytesIO()
    with wave.open(buf, "wb") as wf:
        wf.setnchannels(1)
        wf.setsampwidth(2)
        wf.setframerate(rate)
        wf.writeframes(pcm.tobytes())
    return buf.getvalue()


def transcribe(audio: np.ndarray, client=None) -> str:
    """Devuelve el texto transcrito. Si Groq no está, devuelve '' (el manager
    lo trata como 'no te he oído')."""
    if client is None:
        client = ModelRouter().groq()
    if client is None or len(audio) == 0:
        return ""
    try:
        wav_bytes = _float32_to_wav_bytes(audio)
        transcription = client.audio.transcriptions.create(
            file=("jarvismic.wav", wav_bytes, "audio/wav"),
            model=Config.STT_MODEL,
            language="es",
            # Contexto de nombres propios: afina JARVIS/Raspberry/Nextcloud.
            prompt=("JARVIS, Raspberry Pi, Nextcloud, Ubuntu, VirtualBox, "
                    "dirección IP, hostname, dime, pon, abre, carpeta"),
            temperature=0.0,
        )
        return (transcription.text or "").strip()
    except Exception as e:
        log.warning("STT Groq falló: %s", e)
        return ""


def transcribe_bytes(data: bytes) -> str:
    """Transcribe audio comprimido (webm/opus del navegador, ogg, mp3).
    Decodifica a 16 kHz mono con ffmpeg y usa transcribe()."""
    import subprocess
    import tempfile
    import os as _os
    if not data or len(data) < 500:
        return ""
    try:
        from integrations.voice_call import ffmpeg_exe
        ff = ffmpeg_exe()
    except Exception:
        return ""
    if not ff:
        return ""
    tmp_in, tmp_out = None, None
    try:
        with tempfile.NamedTemporaryFile(suffix=".webm", delete=False) as f:
            f.write(data)
            tmp_in = f.name
        tmp_out = tmp_in + ".wav"
        r = subprocess.run(
            [ff, "-y", "-v", "error", "-i", tmp_in,
             "-ar", "16000", "-ac", "1", "-f", "wav", tmp_out],
            capture_output=True, timeout=60,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if r.returncode != 0:
            return ""
        with wave.open(tmp_out, "rb") as wf:
            n = wf.getnframes()
            if n < 16000 * 0.3:
                return ""
            raw = wf.readframes(n)
        audio = np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0
        return transcribe(audio)
    except Exception as e:
        log.debug("transcribe_bytes: %s", e)
        return ""
    finally:
        for p in (tmp_in, tmp_out):
            try:
                if p:
                    _os.remove(p)
            except Exception:
                pass