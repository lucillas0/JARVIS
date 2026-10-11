# -*- coding: utf-8 -*-
"""Captura de la cámara por OpenCV (DirectShow), sin pasar por Chromium.

En este equipo la cámara no tiene Media Foundation, que es lo único que Chromium
usa desde la versión 116, y por eso el navegador no la abre. OpenCV sí, por
DirectShow: es el mismo camino que usa el proyecto de reconocimiento facial.

La cámara se abre cuando hace falta y se suelta sola tras unos segundos sin
peticiones, para no quitársela a otras apps.
"""
from __future__ import annotations

import atexit
import logging
import threading
import time

import cv2
import numpy as np

log = logging.getLogger("vision.webcam")

# Antes 30 s: la cámara se quedaba cogida medio minuto después de apagar los
# gestos, y mientras la tuviera el cerebro nadie más podía abrirla (las cámaras
# USB de este equipo no se comparten). Con 10 s se suelta antes y el tradeoff es
# invisible: durante el uso real la cámara se usa sin parar.
SOLTAR_TRAS_SEGUNDOS = 10.0
MAX_INDICES = 3

ERROR_NEGRO = (
    "la cámara entrega imágenes completamente negras (todo ceros). Eso casi "
    "siempre es que la lente está tapada, o el cable o el puerto USB no dan "
    "corriente. Desenchúfala y conéctala a OTRO puerto USB trasero; si sigue "
    "igual, prueba otro cable."
)


class Webcam:
    def __init__(self) -> None:
        self._cap = None
        self._lock = threading.RLock()
        self._ultimo_uso = 0.0
        self.ultimo_error = ""
        self.backend = ""
        self.indice = -1

    # ---------------------------------------------------------------- abrir
    def abrir(self) -> bool:
        with self._lock:
            if self._cap is not None and self._cap.isOpened():
                return True
            self._cerrar_locked()
            intentos = ((cv2.CAP_DSHOW, "DirectShow"), (cv2.CAP_MSMF, "Media Foundation"))
            fallos = []
            for backend, nombre in intentos:
                for indice in range(MAX_INDICES):
                    cap = None
                    try:
                        cap = cv2.VideoCapture(indice, backend)
                        if not cap.isOpened():
                            fallos.append(f"{nombre} {indice}: no abrió")
                            if cap is not None:
                                cap.release()
                            continue
                        cap.set(cv2.CAP_PROP_FRAME_WIDTH, 640)
                        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, 480)
                        cap.set(cv2.CAP_PROP_BUFFERSIZE, 1)
                        ok, frame = cap.read()
                        if not ok or frame is None or frame.size == 0:
                            fallos.append(f"{nombre} {indice}: abrió sin imagen")
                            cap.release()
                            continue
                        self._cap = cap
                        self.backend = nombre
                        self.indice = indice
                        self.ultimo_error = ""
                        self._ultimo_uso = time.time()
                        log.info("cámara abierta: %s cámara %d", nombre, indice)
                        return True
                    except Exception as e:
                        fallos.append(f"{nombre} {indice}: {type(e).__name__}")
                        if cap is not None:
                            try:
                                cap.release()
                            except Exception:
                                pass
            self.ultimo_error = ("no se pudo abrir la cámara por OpenCV: "
                                 + "; ".join(fallos[:6]))
            log.warning(self.ultimo_error)
            return False

    def _cerrar_locked(self) -> None:
        if self._cap is not None:
            try:
                self._cap.release()
            except Exception:
                pass
        self._cap = None

    def cerrar(self) -> None:
        with self._lock:
            self._cerrar_locked()

    def soltar_si_no_se_usa(self) -> None:
        with self._lock:
            if self._cap is not None and self._ultimo_uso:
                if time.time() - self._ultimo_uso > SOLTAR_TRAS_SEGUNDOS:
                    self._cerrar_locked()

    # --------------------------------------------------------------- leer
    def frame(self) -> np.ndarray | None:
        with self._lock:
            self._ultimo_uso = time.time()
            recien_abierto = False
            if self._cap is None or not self._cap.isOpened():
                if not self.abrir():
                    return None
                recien_abierto = True
            try:
                ok, frame = self._cap.read()
            except Exception as e:
                self.ultimo_error = f"lectura falló: {type(e).__name__}: {e}"
                self._cerrar_locked()
                return None
            if not ok or frame is None or frame.size == 0:
                self._cerrar_locked()
                self.ultimo_error = "la cámara abrió pero no entrega imagen"
                return None
            if recien_abierto:
                frame = self._calentar(frame)
            if self._es_negro(frame):
                # ANTES aquí se cerraba la cámara, se esperaba 1,5 s y se
                # reintentaba. Ese ciclo de abrir/cerrar dejaba el dispositivo
                # delivering un fotograma y luego ceros para siempre, así que la
                # cámara se quedaba frita. Ahora solo se avisa y se suelta el
                # fotograma; quien reintente es el siguiente.
                self.ultimo_error = ERROR_NEGRO
                log.warning(self.ultimo_error)
                return None
            return frame

    def _calentar(self, frame: np.ndarray) -> np.ndarray:
        """Las cámaras entregan 2-3 fotogramas negros al abrir (exposición).

        Un frame en negro hace que el modelo diga "no se ve nada", así que se
        descartan los primeros y, si sigue oscura, se queda con el mejor.
        """
        for _ in range(3):
            ok, f = self._cap.read()
            if not ok or f is None or f.size == 0:
                break
            frame = f
        mejor = frame
        mejor_luz = float(frame.mean())
        for _ in range(6):
            ok, f = self._cap.read()
            if not ok or f is None or f.size == 0:
                break
            luz = float(f.mean())
            if luz > mejor_luz:
                mejor, mejor_luz = f, luz
            frame = f
            if mejor_luz > 25.0:      # ya se ve algo: no gastes más
                break
        log.info("cámara: fotograma de luminancia %.1f", mejor_luz)
        return mejor

    @staticmethod
    def _es_negro(frame: np.ndarray) -> bool:
        """Un frame TODO a cero no es una escena oscura: es que no hay señal."""
        if frame is None or frame.size == 0:
            return True
        gris = frame if frame.ndim == 2 else frame.mean(axis=2)
        return float(gris.max()) <= 1.0 and float(gris.std()) < 0.5

    def jpeg(self, calidad: int = 65) -> bytes | None:
        frame = self.frame()
        if frame is None:
            return None
        ok, buf = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY),
                                                int(calidad)])
        if not ok:
            return None
        return buf.tobytes()

    def estado(self) -> dict:
        with self._lock:
            abierto = self._cap is not None and self._cap.isOpened()
            return {"abierta": abierto, "backend": self.backend,
                    "indice": self.indice, "ultimo_error": self.ultimo_error,
                    "ultimo_uso_hace": round(time.time() - self._ultimo_uso, 1)
                    if self._ultimo_uso else None}


_camara = Webcam()
atexit.register(_camara.cerrar)
_hilo = threading.Thread(target=lambda: [(_camara.soltar_si_no_se_usa(),
                                            time.sleep(2.0)) for _ in iter(int, 1)],
                          daemon=True, name="webcam-idle")
_hilo.start()


def obtener() -> Webcam:
    return _camara
