"""Motor de audio (micrófono + altavoz) con barge-in.

Usa DOS streams separados de sounddevice a 16 kHz: uno de entrada (micro)
y otro de salida (altavoz). En Windows muchos equipos exponen el micro solo
por WDM-KS y el altavoz por MME/WASAPI, y PortAudio rechaza combinarlos en un
único stream full-duplex ("Illegal combination of I/O devices"). Por eso los
abrimos por separado, lo que además da máxima compatibilidad de hardware.
En reposo el micro está activo (para el wake word); en reproducción, el micro
vigila el barge-in: si el usuario habla, la reproducción se detiene al instante.
"""
import logging
import queue
import threading
import time
from typing import Callable, Optional

import numpy as np

log = logging.getLogger("voice.audio")

RATE = 16000
CHANNELS = 1

try:
    import sounddevice as sd
    _HAS_SD = True
except Exception as e:
    _HAS_SD = False
    log.warning("sounddevice no disponible: %s", e)


def _try_open(device_id: int, kind: str) -> bool:
    """Prueba si el dispositivo se puede abrir realmente (no devuelve False
    porque no existe o WDM-KS lo bloquea)."""
    try:
        kwargs = dict(samplerate=RATE, channels=CHANNELS, dtype="float32",
                       blocksize=1600, device=device_id)
        if kind == "input":
            with sd.InputStream(**kwargs) as s:
                s.start(); s.stop()
        else:
            with sd.OutputStream(**kwargs) as s:
                s.start(); s.stop()
        return True
    except Exception as e:
        log.debug("Device %s %s no abrió: %s", kind, device_id, e)
        return False


def _pick_devices():
    """Elige (idx_in, idx_out) validados realmente. Para cada candidato
    intenta abrirlo; así WDM-KS roto (Blocking API) se descarta. Devuelve
    (-1, out_dev) si solo hay salida válida (modo output-only)."""
    if not _HAS_SD:
        return (-1, -1)
    try:
        devs = sd.query_devices()
    except Exception as e:
        log.warning("query_devices falló: %s", e)
        return (-1, -1)
    ins, outs = [], []
    for i, d in enumerate(devs):
        if d["max_input_channels"] > 0 and d["max_input_channels"] % 2 == 0:
            ins.append(i)
        if d["max_output_channels"] > 0 and d["max_output_channels"] % 2 == 0:
            outs.append(i)
    # Validar cada candidato abriendo y cerrando el stream.
    ok_ins = [i for i in ins if _try_open(i, "input")]
    ok_outs = [o for o in outs if _try_open(o, "output")]
    if not ok_outs:
        return (-1, -1)
    if ok_ins:
        # preferir misma hostapi
        in_host = {i: devs[i]["hostapi"] for i in ok_ins}
        out_host = {o: devs[o]["hostapi"] for o in ok_outs}
        for api in set(in_host.values()):
            if api in out_host.values():
                return (next(i for i in ok_ins if in_host[i] == api),
                        next(o for o in ok_outs if out_host[o] == api))
        return (ok_ins[0], ok_outs[0])
    # solo salida disponible → output-only
    return (-1, ok_outs[0])


class AudioEngine:
    def __init__(self, on_mic_chunk: Optional[Callable[[np.ndarray], None]] = None,
                 on_barge_in: Optional[Callable[[], None]] = None,
                 on_output_level: Optional[Callable[[float], None]] = None,
                 playback_rate: int = RATE):
        """on_mic_chunk(frame16k mono float32) → para wake word / STT.
        on_barge_in() → se llama cuando el usuario interrumpe la voz.
        on_output_level(rms 0..1) → para animar la línea de voz en el HUD."""
        self.on_mic_chunk = on_mic_chunk
        self.on_barge_in = on_barge_in
        self.on_output_level = on_output_level
        self.playback_rate = playback_rate

        self._out = queue.Queue()  # bytes/chunks de audio float32
        self._playing = threading.Event()
        self._istream: Optional[sd.InputStream] = None
        self._ostream: Optional[sd.OutputStream] = None
        self._recording = threading.Event()
        self._record_buf: list[np.ndarray] = []
        self._barrier_thread: Optional[threading.Thread] = None
        self._barge_threshold = 0.012
        self._lock = threading.Lock()
        self._gain = 1.0
        self._music_active = False
        self._music_volume = 0.4
        self._ww_history: list[np.ndarray] = []
        self._rms_buffer: list[float] = []
        self._has_input = False
        self._has_output = False

    # ---------------- stream ----------------
    def start(self) -> bool:
        if not _HAS_SD:
            log.error("No hay sounddevice: el audio no está disponible.")
            return False
        if self._istream is not None or self._ostream is not None:
            return True
        in_dev, out_dev = _pick_devices()
        try:
            import sounddevice as sd
            # micro: entrada (16k, mono)
            if in_dev >= 0:
                self._istream = sd.InputStream(
                    samplerate=RATE, channels=CHANNELS, device=in_dev,
                    callback=self._in_callback, blocksize=1600, dtype="float32",
                )
                self._istream.start()
                self._has_input = True
            # altavoz: salida (16k, mono)
            if out_dev >= 0:
                self._ostream = sd.OutputStream(
                    samplerate=RATE, channels=CHANNELS, device=out_dev,
                    callback=self._out_callback, blocksize=1600, dtype="float32",
                )
                self._ostream.start()
                self._has_output = True
            if not self._has_output:
                log.error("No pude abrir ningún stream de audio.")
                return False
            self._barrier_thread = threading.Thread(target=self._playback_worker, daemon=True)
            self._barrier_thread.start()
            mode = "input+output" if self._has_input else "output-only"
            log.info("Audio activo [%s]: micro=%s altavoz=%s", mode, in_dev, out_dev)
            return True
        except Exception as e:
            log.exception("No pude abrir streams de audio: %s", e)
            self._istream = self._ostream = None
            self._has_input = self._has_output = False
            return False

    def stop(self):
        for s in (self._istream, self._ostream):
            if s is not None:
                try:
                    s.stop()
                    s.close()
                except Exception:
                    pass
        self._istream = self._ostream = None
        self._has_input = self._has_output = False

    # ---------- capability ----------
    @property
    def has_input(self) -> bool:
        return self._has_input

    @property
    def has_output(self) -> bool:
        return self._has_output

    @property
    def is_active(self) -> bool:
        """True si al menos la salida funciona."""
        return self._has_output

    def _in_callback(self, indata, frames, time_info, status):
        mic = indata[:, 0].copy()
        # Wake word siempre escuchando: se acumula y el worker lo procesa fuera
        # del callback de audio (tiempo real) para no bloquear el stream.
        self._ww_history.append(mic)
        if len(self._ww_history) > 40:  # ~4 s de contexto máximo
            self._ww_history.pop(0)
        # para STT se graba aquí:
        if self._recording.is_set():
            self._record_buf.append(mic)

    def _out_callback(self, outdata, frames, time_info, status):
        out = np.zeros(frames, dtype="float32")
        try:
            if not self._out.empty():
                src = self._out.get_nowait()
                n = min(len(src), frames)
                out[:n] = src[:n]
                if len(src) > frames:
                    self._out.put_nowait(src[frames:])
                self._playing.set()
            elif self._playing.is_set() and self._out.empty():
                self._playing.clear()
        except Exception:
            pass
        out *= float(self._gain)
        out = np.clip(out, -1.0, 1.0)
        rms = float((np.abs(out) ** 2).mean() ** 0.5) if len(out) else 0.0
        if self.on_output_level:
            self.on_output_level(min(1.0, float(rms)))
        outdata[:] = out.reshape(-1, 1)

    # ---------------- reproducción ----------------
    def _playback_worker(self):
        while True:
            time.sleep(0.02)
            # Wake word: alimentar el detector con el audio acumulado.
            try:
                if self.on_mic_chunk and self._ww_history:
                    batch = []
                    ww_len = 0
                    while self._ww_history:
                        batch.append(self._ww_history[0])
                        self._ww_history.pop(0)
                        ww_len += len(batch[-1])
                    self.on_mic_chunk(np.concatenate(batch))
            except Exception as e:
                log.debug("ww worker: %s", e)
            # barge-in: mientras haya salida, vigilar mic
            if self._playing.is_set() and self.on_barge_in:
                try:
                    amp = self.last_mic_rms()
                    if amp and amp > self._barge_threshold:
                        self._onsetting_barge_in()
                except Exception:
                    pass

    def _onsetting_barge_in(self):
        log.info("Barge-in: usuario interrumpiendo.")
        self.stop_playback(emit=False)
        if self.on_barge_in:
            try:
                self.on_barge_in()
            except Exception:
                pass

    def play_data(self, data: np.ndarray) -> None:
        """data float32 mono a playback_rate → se convierte a 16k y se encola."""
        arr = np.asarray(data, dtype="float32").reshape(-1)
        if self.playback_rate != RATE:
            arr = _resample_linear(arr, self.playback_rate, RATE)
        self._out.put_nowait(arr.astype("float32"))
        self._playing.set()

    def play_wav_file(self, path: str) -> bool:
        import wave
        try:
            with wave.open(path, "rb") as wf:
                sr = wf.getframerate()
                data = np.frombuffer(wf.readframes(wf.getnframes()), dtype=np.int16).astype("float32") / 32768.0
            self.play_data(data if sr == RATE else _resample_linear(data, sr, RATE))
            return True
        except Exception as e:
            log.warning("No pude reproducir %s: %s", path, e)
            return False

    def play_mp3_file(self, path: str, volume: float = 1.0) -> bool:
        """Decodifica mp3 (miniaudio) y encola con volumen (para ducking)."""
        try:
            import miniaudio
            decoded = miniaudio.decode_file(path)
            if decoded.sample_width == 2:
                data = np.frombuffer(decoded.samples, dtype=np.int16).astype("float32") / 32768.0
            else:
                data = np.frombuffer(decoded.samples, dtype=np.float32)
            if decoded.nchannels > 1:
                data = data.reshape(-1, decoded.nchannels).mean(axis=1)
            data = data * float(volume)
            self.play_data(data)
            return True
        except Exception as e:
            log.warning("mp3 play falló (%s); intento wav", e)
            return False

    def stop_playback(self, emit: bool = True) -> None:
        self._playing.clear()
        while not self._out.empty():
            try:
                self._out.get_nowait()
            except Exception:
                break
        if emit and self.on_output_level:
            self.on_output_level(0.0)

    def set_music_volume(self, volume: float) -> None:
        """Ducking: ajusta el gain del audio en curso cuando hay música de fondo."""
        self._music_volume = max(0.0, min(1.0, volume))
        if self._music_active:
            self._gain = self._music_volume

    def set_gain(self, gain: float) -> None:
        """Gain global aplicado a la salida. Si la música está activa, el gain
        real se compone con el nivel de la música (ducking)."""
        self._gain = float(gain)
        self._apply_music_gain()

    def music_active(self) -> bool:
        return self._music_active

    def set_music_active(self, active: bool) -> None:
        self._music_active = bool(active)
        self._apply_music_gain()

    def _apply_music_gain(self) -> None:
        """Componer gain: cuando hay música de fondo, todo el stream pasa por
        el nivel de la música salvo que JARVIS esté hablando (ducking)."""
        if self._music_active and not self._playing.is_set():
            self._gain = self._music_volume

    def speaking(self) -> bool:
        return self._playing.is_set()

    # ---------------- micrófono ----------------
    def last_mic_rms(self) -> float:
        buf = self._record_buf
        if not buf:
            return 0.0
        chunk = buf[-1]
        return float((np.abs(chunk) ** 2).mean() ** 0.5)

    def begin_recording(self) -> None:
        self._recording.set()
        self._record_buf.clear()

    def end_recording(self) -> np.ndarray:
        self._recording.clear()
        if not self._record_buf:
            return np.zeros(0, dtype="float32")
        return np.concatenate(self._record_buf).astype("float32")

    def is_recording(self) -> bool:
        return self._recording.is_set()


def _resample_linear(data: np.ndarray, from_rate: int, to_rate: int) -> np.ndarray:
    if from_rate == to_rate or len(data) == 0:
        return data.astype("float32")
    n_out = int(len(data) * to_rate / from_rate)
    x = np.linspace(0, len(data) - 1, n_out)
    return np.interp(x, np.arange(len(data)), data).astype("float32")


_engine: Optional[AudioEngine] = None


def get_engine(**kwargs) -> AudioEngine:
    global _engine
    if _engine is None:
        _engine = AudioEngine(**kwargs)
    return _engine