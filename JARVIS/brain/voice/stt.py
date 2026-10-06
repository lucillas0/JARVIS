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


def _prepare_audio(audio: np.ndarray, rate: int = 16000) -> np.ndarray:
    """Quita DC/silencio y normaliza voz baja sin amplificar demasiado el ruido."""
    samples = np.asarray(audio, dtype=np.float32).reshape(-1)
    if not samples.size:
        return samples
    samples = samples - float(np.mean(samples))
    peak = float(np.max(np.abs(samples)))
    if peak < 1e-4:
        return np.zeros(0, dtype=np.float32)

    # Recorte por ventanas de 20 ms con umbral adaptativo y margen de 180 ms.
    frame = max(1, int(rate * 0.02))
    count = samples.size // frame
    if count > 2:
        framed = samples[:count * frame].reshape(count, frame)
        rms = np.sqrt(np.mean(framed * framed, axis=1))
        noise_floor = float(np.percentile(rms, 20))
        threshold = max(0.003, peak * 0.07, min(peak * 0.5, noise_floor * 2.5))
        active = np.flatnonzero(rms > threshold)
        if active.size:
            margin = max(1, int(0.18 / 0.02))
            first = max(0, int(active[0]) - margin) * frame
            last = min(count, int(active[-1]) + margin + 1) * frame
            samples = samples[first:last]

    peak = float(np.max(np.abs(samples))) if samples.size else 0.0
    if peak > 1e-5:
        gain = min(4.0, 0.88 / peak)
        samples = np.clip(samples * gain, -0.98, 0.98)
    return samples.astype(np.float32, copy=False)


def transcribe(audio: np.ndarray, client=None) -> str:
    """Devuelve la transcripción en español; cadena vacía si no hay voz utilizable."""
    audio = _prepare_audio(audio)
    if client is None:
        client = ModelRouter().groq()
    if client is None or len(audio) < 16000 * 0.25:
        return ""

    wav_bytes = _float32_to_wav_bytes(audio)
    prompt = ("Transcripción literal en español. Nombres y términos: JARVIS, "
              "Raspberry Pi, Nextcloud, Ubuntu, VirtualBox, Windows, "
              "dirección IP, hostname, abre, activa, desactiva, carpeta, archivo.")
    models = [Config.STT_MODEL]
    if Config.STT_MODEL != "whisper-large-v3-turbo":
        models.append("whisper-large-v3-turbo")
    for model in models:
        try:
            transcription = client.audio.transcriptions.create(
                file=("jarvismic.wav", wav_bytes, "audio/wav"),
                model=model,
                language="es",
                prompt=prompt,
                temperature=0.0,
            )
            text = (transcription.text or "").strip()
            if text:
                return text
        except Exception as e:
            log.warning("STT Groq falló (%s): %s", model, e)
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