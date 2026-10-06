"""Wake word local (openWakeWord) con la palabra "jarvis" y sin distinción de
hablante.

El modelo 'hey_jarvis' viene con la librería openWakeWord (lo descarga en el
primer uso desde los releases de GitHub). La palabra exacta es configurable vía
WAKE_WORD en .env. También se aceptan modelos propios en brain/models/wakeword/*.tflite.
"""
import logging
import pathlib
import threading

import numpy as np

from config import Config

log = logging.getLogger("voice.wakeword")

_detector = None
_detector_lock = threading.Lock()
_feed_lock = threading.Lock()
_frame_duration = 1280  # muestras a 16k (80ms) que espera openWakeWord
_THRESHOLD = 0.45

_ZOO_ALIASES = {
    "jarvis": "hey_jarvis",
    "hey jarvis": "hey_jarvis",
    "alexa": "alexa",
    "ok google": "hey_google",
    "hey google": "hey_google",
}

_RELEASE_URL = "https://github.com/dscripka/openWakeWord/releases/download/v0.5.1/"


def _ensure_models():
    """Descarga los feature models onnx (melspectrogram, embedding) si no existen."""
    import openwakeword.__init__ as ow_pkg
    pkg = pathlib.Path(ow_pkg.__file__).parent
    res = pkg / "resources" / "models"
    res.mkdir(parents=True, exist_ok=True)
    targets = {
        "melspectrogram.onnx": None,
        "embedding_model.onnx": None,
        "silero_vad.onnx": None,
        "hey_jarvis_v0.1.onnx": None,
    }
    for name in targets:
        dest = res / name
        if not dest.exists():
            log.info("Descargando %s para openWakeWord...", name)
            import urllib.request
            urllib.request.urlretrieve(_RELEASE_URL + name, dest)


def _load_detector():
    global _detector
    with _detector_lock:
        if _detector is not None:
            return _detector
        try:
            _ensure_models()
            from openwakeword import Model
            onnx_path = Config.BRAIN_DIR / "models" / "wakeword"
            custom = sorted(onnx_path.glob("*.onnx"))
            if custom:
                model = Model(wakeword_models=[str(c) for c in custom],
                              inference_framework="onnx")
                _detector = {"model": model, "names": [c.stem for c in custom]}
            else:
                _ensure_models()
                import openwakeword
                pkg = pathlib.Path(openwakeword.__file__).parent
                model_path = str(pkg / "resources" / "models" / "hey_jarvis_v0.1.onnx")
                model = Model(wakeword_models=[model_path],
                              inference_framework="onnx")
                _detector = {"model": model, "names": ["hey_jarvis"]}
            log.info("Wake word detector listo para '%s'", Config.WAKE_WORD)
        except Exception as e:
            log.warning("openWakeWord no disponible (%s). El wake word quedará "
                        "desactivado; se activará por botón del HUD.", e)
            _detector = False
        return _detector


_feed_buf = np.zeros(0, dtype=np.float32)


def feed(audio: np.ndarray) -> bool:
    """Alimenta el detector con un chunk de micrófono (streaming continuo).

    Acumula muestras y procesa tramas completas de 80 ms SIN reset() entre
    llamadas, porque openWakeWord mantiene contexto melspectrogram/embedding
    entre predicciones (reset() por chunk destruía ese contexto y dejaba la
    detección huérfana). Tras detectar, se reinicia para no reintentar la
    misma palabra. Devuelve True si el wake word fue detectado.
    """
    global _feed_buf
    det = _load_detector()
    if not det or not det.get("model"):
        return False
    arr = np.asarray(audio, dtype=np.float32).reshape(-1)
    if len(arr) == 0:
        return False
    try:
        with _feed_lock:
            with _detector_lock:
                _feed_buf = np.concatenate([_feed_buf, arr])
                n = (_feed_buf.size // _frame_duration) * _frame_duration
                if n == 0:
                    return False
                pcm = (np.clip(_feed_buf[:n], -1, 1) * 32767).astype(np.int16)
                _feed_buf = _feed_buf[n:]
                for i in range(0, n, _frame_duration):
                    frame = pcm[i:i + _frame_duration]
                    pred = det["model"].predict(frame)
                    for score in pred.values():
                        if isinstance(score, (list, tuple)):
                            score = max(score) if score else 0
                        if score and float(score) > _THRESHOLD:
                            det["model"].reset()
                            _feed_buf = np.zeros(0, dtype=np.float32)
                            return True
    except Exception as e:
        log.debug("wake word predict error: %s", e)
    return False


def process_audio(audio: np.ndarray) -> bool:
    """Compat: procesa el chunk como disponible (con contexto previo)."""
    return feed(audio)


def reset_stream() -> None:
    """Limpia el buffer interno del feed (p. ej. al acabar una conversación)."""
    global _feed_buf
    det = _load_detector()
    with _feed_lock:
        _feed_buf = np.zeros(0, dtype=np.float32)
        if det:
            with _detector_lock:
                try:
                    det["model"].reset()
                except Exception:
                    pass