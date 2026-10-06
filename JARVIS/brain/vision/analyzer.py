"""Análisis de pantalla: usa el modelo de visión del Model Router (nivel Gemini)
para describir lo que ve el usuario. El texto descriptivo puede quedar en el
contexto; la imagen jamás se persiste.
"""
import logging

import numpy as np

from model_router import ModelRouter
from vision.capture import capture_recent

log = logging.getLogger("vision.analyzer")

PROMPT_SCREEN = (
    "Lee la pantalla y transmite su INFORMACIÓN, no describas la interfaz. "
    "Si hay noticias o titulares, dalos con su resumen. Si hay un documento, "
    "resume su contenido. Si hay un error o aviso, transcríbelo exacto. "
    "Solo si no hay nada legible, describe brevemente lo que se ve. En español."
)

PROMPT_WINDOW = (
    "Lee esta ventana y transmite su INFORMACIÓN, no describas la interfaz "
    "(nada de 'se ve un navegador con…'). Si es una web de noticias, da los "
    "titulares con su resumen. Si es un documento o artículo, resume su "
    "contenido. Si hay un error o aviso, transcríbelo exacto. Solo describe "
    "botones o menús si te lo piden o si no hay contenido legible. En español."
)

PROMPT_CONTEXT = (
    "Basándote solo en esta captura, da UNA frase corta que describa en qué "
    "está trabajando el usuario (ej. 'escribiendo en un documento Word' o "
    "'depurando código en VSCode'). Si no es claro, di 'actividad indeterminada'."
)


def analyze_screen(prompt: str = PROMPT_SCREEN, router: ModelRouter = None,
                   force_capture: bool = True) -> str:
    """Analiza la pantalla actual (bajo pedido)."""
    router = router or ModelRouter()
    b64 = capture_recent(force=force_capture)
    if not b64:
        return "No puedo ver la pantalla en estos momentos, señor."
    ctx = _foreground_context()
    result = router.respond([{"role": "user", "content": ctx + prompt}],
                            heavy=True, vision=b64)
    return result.get("reply", "He visto la pantalla, señor, pero aún proceso lo observado.")


def analyze_window(target: str = "", prompt: str = PROMPT_WINDOW,
                   router: ModelRouter = None) -> str:
    """Analiza una ventana concreta (aunque esté en segundo plano)."""
    from vision.capture import capture_window, is_mostly_black
    router = router or ModelRouter()
    b64, info = capture_window(target or "")
    if not b64:
        return f"No pude mirar esa ventana, señor: {info}"
    if is_mostly_black(b64):
        return (f"La ventana «{info}» sale en negro, señor: debe estar en "
                "pantalla exclusiva (típico de juegos). Póngala en modo ventana "
                "y vuelvo a mirar.")
    result = router.respond(
        [{"role": "user",
          "content": f"Ventana: «{info}».\n{prompt}"}],
        heavy=True, vision=b64)
    return result.get("reply", f"He visto «{info}», señor, pero aún lo proceso.")


def _foreground_context() -> str:
    """'Ventana activa: X (proceso)' para enriquecer el análisis. Enano y gratis."""
    try:
        from proactive import foreground_window_info
        info = foreground_window_info()
        title = (info.title or "").strip()[:80]
        proc = (info.proc_name or "").strip()[:40]
        if title or proc:
            return f"[Ventana activa: {title or '?'} ({proc or '?'})]\n"
    except Exception as e:
        log.debug("fg context: %s", e)
    return ""


def analyze_webcam_frame(b64: str, prompt: str = "Describe qué ves en esta imagen de cámara web.",
                         router: ModelRouter = None) -> str:
    """Analiza un frame de webcam recibido desde el overlay (base64, en memoria)."""
    from model_router import ModelRouter as _MR
    router = router or _MR()
    if not b64:
        return "No he recibido imagen de la cámara, señor."
    try:
        result = router.respond([{"role": "user", "content": prompt}],
                                heavy=True, vision=b64)
        return result.get("reply", "He visto algo en la cámara, señor, pero aún lo estoy procesando.")
    except Exception as e:
        log.debug("analyze_webcam_frame: %s", e)
        return f"No he podido analizar la cámara, señor: {e}"


def describe_recent_screenshot(router: ModelRouter = None) -> str:
    """Uso interno del Proactive Engine (contexto continuo)."""
    try:
        b64 = capture_recent(force=False)
        if not b64:
            return ""
        router = router or ModelRouter()
        result = router.respond([{"role": "user", "content": PROMPT_CONTEXT}],
                                heavy=True, vision=b64)
        return (result.get("reply", "") or "").strip()
    except Exception as e:
        log.debug("describe_recent_screenshot: %s", e)
        return ""