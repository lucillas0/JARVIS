"""Gestor de voz: wake word → escucha continua → STT → brain → TTS (con barge-in).

Modo conversación (sección 3 de la especificación):
- Se activa diciendo "Jarvis" UNA vez.
- Sigue escuchando y respondiendo sin repetir la palabra.
- Vuelve a reposo tras silencio prolongado.
- Barge-in: si el usuario habla mientras JARVIS habla, se calla y escucha.
"""
import logging
import time
from threading import Thread
from typing import Callable, Optional

import numpy as np

from config import Config
from voice import wake_word
from voice.audio import AudioEngine, RATE

log = logging.getLogger("voice.manager")

IDLE, LISTENING, THINKING, SPEAKING = "idle", "listening", "thinking", "speaking"


class VoiceManager:
    def __init__(self, engine: AudioEngine,
                 on_text: Callable[[str], Optional[str]],
                 emit: Callable[[dict], None]):
        """on_text(texto) → respuesta a hablar o None."""
        self.engine = engine
        self.on_text = on_text
        self.emit = emit

        self.state = IDLE
        self._active = True
        self._wake_detected_at = 0.0
        self._last_speech_ts = 0.0
        self._thread: Optional[Thread] = None

        self.engine.on_mic_chunk = self._on_mic_chunk
        self.engine.on_barge_in = self._on_barge_in
        self.engine.on_output_level = self._on_output_level
        self.engine.playback_rate = RATE

    # ---------------- API pública ----------------
    def start(self):
        if not self.engine.start():
            log.error("Sin audio, arranco igualmente pero sin voz.")
        if self.engine.has_input:
            self._thread = Thread(target=self._loop, daemon=True, name="voice-loop")
            self._thread.start()
        else:
            log.warning("Sin micro: modo output-only (hablar sí, escuchar no).")

    def stop(self):
        self._active = False
        self.engine.stop_playback()

    def wake_now(self):
        """Activación forzada (botón del HUD o comando de texto)."""
        self._last_speech_ts = time.time()
        self._wake_detected_at = time.time()

    # ---------------- callbacks ---------------
    def _on_mic_chunk(self, chunk: np.ndarray):
        if self.state == IDLE:
            if wake_word.process_audio(chunk):
                log.info("Wake word detectado.")
                self.wake_now()

    def _on_barge_in(self):
        if self.state == SPEAKING:
            self._last_speech_ts = time.time()
            self._set_state(LISTENING)
            self.engine.begin_recording()
            self.emit({"type": "speech_break", "message": "Interrumpido"})

    def _on_output_level(self, level: float):
        self.emit({"type": "audio_level", "level": level})

    def _set_state(self, st):
        if st != self.state:
            self.state = st
            self.emit({"type": "conversation_state", "state": st})
            log.info("estado voz → %s", st)

    # ---------------- bucle ----------------
    def _loop(self):
        min_active_speaking = Config.CONVERSATION_SILENCE_SECONDS
        while self._active:
            now = time.time()
            if (self.state == IDLE) and (self._wake_detected_at and
                                         (now - self._wake_detected_at) < 8):
                self._start_conversation()

            elif self.state == LISTENING:
                # grabando; medir fin de la frase
                if engine := self.engine:
                    if engine.is_recording() and (now - self._last_speech_ts > 1.0):
                        rms = engine.last_mic_rms()
                        if rms is not None and rms > 0.01:
                            self._last_speech_ts = now
                        elif (now - self._last_speech_ts) > min_active_speaking:
                            self._finish_recording()

            elif self.state == SPEAKING:
                if not self.engine.speaking():
                    # terminó de hablar → si en plena conversación, sigue escuchando
                    self._last_speech_ts = time.time()
                    self._set_state(LISTENING)
                    self.engine.begin_recording()
            time.sleep(0.25)

    def _start_conversation(self):
        self._set_state(LISTENING)
        self.engine.begin_recording()
        self._last_speech_ts = time.time()
        # pequeño settle para no captar el final del wake word
        time.sleep(0.4)
        self.emit({"type": "conversation_active"})

    def _finish_recording(self):
        audio = self.engine.end_recording()
        if len(audio) < RATE * 0.35:  # muy corto → seguir esperando
            self.engine.begin_recording()
            self._last_speech_ts = time.time()
            return
        self._set_state(THINKING)
        import voice.stt as stt
        text = stt.transcribe(audio)
        if not text:
            self.emit({"type": "voice_result", "text": "", "error": "no_heard"})
            self._set_state(LISTENING)
            self.engine.begin_recording()
            self._last_speech_ts = time.time()
            return
        self.emit({"type": "voice_result", "text": text})
        # Comando interno: pausar reproducción de música para el ducking
        reply = self.on_text(text)
        self._speak(reply)
        # tras responder, si siguen hablando vuelve a escuchar; el estado LOOP
        # lo gestiona. Si reply es None → espera estado idle según tiempo.

    def speak_out(self, text: Optional[str]) -> bool:
        """Habla el texto si hay salida de audio real. Devuelve True si sonó.

        Sin salida (p. ej. PC sin altavoces configurados) devuelve False para
        que el llamante lo entregue como texto en su lugar.
        """
        if not text:
            return False
        try:
            if not self.engine.has_output:
                return False
        except Exception:
            return False
        try:
            self._speak(text)
            return True
        except Exception as e:
            log.warning("speak_out: %s", e)
            return False

    def _speak(self, text: Optional[str]):
        if not text:
            self._set_state(LISTENING)
            self.engine.begin_recording()
            self._last_speech_ts = time.time()
            return
        self._set_state(SPEAKING)
        self.emit({"type": "speech_start"})
        # Ducking: si suena música de fondo, la bajamos mientras JARVIS habla.
        self.engine.set_gain(0.12 if self.engine.music_active() else 1.0)
        from voice import tts
        ok = tts.speak(text, self.engine)
        self.emit({"type": "speech_end", "ok": ok})
        self.engine.set_gain(0.4 if self.engine.music_active() else 1.0)
        if not ok:
            self._set_state(LISTENING)
            self.engine.begin_recording()
            self._last_speech_ts = time.time()