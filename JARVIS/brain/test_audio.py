"""El worker de voz no debe gastar CPU cuando no hay nada que escuchar.

Por que este fichero existe: `voice/audio.py:_playback_worker()` es un
`while True` que se esperaba `time.sleep(0.02)` las 24 horas del día, aunque no
hubiera micro abierto ni nada sonando. Eso son 50 vueltas por segundo para no
hacer nada: la cadencia original venía del audio, pero el bucle no necesita ese
ritmo cuando está parado.

Lo que se comprueba aquí es exactamente la promesa del cambio:
  - con el micro abierto -> 20 ms, SIEMPRE (no se pierde latencia)
  - mientras suena -> 20 ms
  - con muestras pendientes -> 20 ms
  - sin nada -> 100 ms

Importante: el segundo caso (con el micro abierto) es el que NO se puede
relajar. Si la espera dependiera solo de `_ww_history`, justo después de
vaciarlo el bucle se frenaría y la palabra clave respondería más tarde.

    python brain/test_audio.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import time
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BRAIN))
os.environ.setdefault("JARVIS_DATA_DIR", tempfile.mkdtemp(prefix="jarvis-audio-"))

_fallos: list = []


def comprobar(condicion, texto: str) -> bool:
    print(f"    · {'OK' if condicion else 'FALLA'}  {texto}")
    if not condicion:
        _fallos.append(texto)
    return bool(condicion)


def _motor(istream, playing, muestras):
    from voice.audio import AudioEngine

    e = AudioEngine.__new__(AudioEngine)
    e._istream = istream
    e._playing = threading.Event()
    if playing:
        e._playing.set()
    e._ww_history = [object()] * muestras
    e.on_mic_chunk = None
    e.on_barge_in = None
    e._mic_revision = 0
    e._barge_revision = 0
    e._barge_threshold = 0.9
    e._barge_high_chunks = 0
    e._barge_cooldown_until = 0.0
    e._onsetting_barge_in = lambda: None
    return e


def esperas_del_worker(istream, playing, muestras, vueltas: int = 3) -> list:
    """Las esperas que hace el worker REAL en las primeras `vueltas` vueltas.

    Se sustituye su `time.sleep` por uno que anota y, al llegar al tope, lanza
    una excepción para salir del `while True`. No se abre ningún dispositivo:
    así el test no depende de que haya micrófono ni de que suene nada.
    """
    import voice.audio as va

    e = _motor(istream, playing, muestras)
    anotadas: list = []

    class _Parada(Exception):
        pass

    def sleep_falso(seg):
        anotadas.append(seg)
        if len(anotadas) >= vueltas:
            raise _Parada
        time.sleep(seg)

    real = va.time.sleep
    va.time.sleep = sleep_falso
    try:
        e._playback_worker()
    except _Parada:
        return anotadas          # tope alcanzado: es como se sale del while True
    finally:
        va.time.sleep = real
    return anotadas


def main_() -> int:
    print("AUDIO · el worker de voz se adapta a lo que hay que hacer")
    mic = esperas_del_worker("abierto", False, 0)
    sonando = esperas_del_worker(None, True, 0)
    pendiente = esperas_del_worker(None, False, 3)
    nada = esperas_del_worker(None, False, 0)

    print(f"    esperas -> mic abierto: {mic} | sonando: {sonando} | "
          f"pendientes: {pendiente} | sin nada: {nada}")

    comprobar(all(s == 0.02 for s in mic),
              f"con el micro abierto, 20 ms siempre ({mic})")
    comprobar(all(s == 0.02 for s in sonando),
              f"mientras suena, 20 ms siempre ({sonando})")
    comprobar(all(s == 0.02 for s in pendiente),
              f"con muestras pendientes, 20 ms ({pendiente})")
    comprobar(all(s == 0.1 for s in nada),
              f"sin micro ni sonido, 100 ms ({nada})")
    comprobar(bool(mic) and max(nada) >= 5 * min(mic),
              "y el ahorro es de al menos 5 veces")

    print()
    if _fallos:
        print(f"{len(_fallos)} COMPROBACIONES FALLIDAS:")
        for f in _fallos:
            print(f"  · {f}")
        return 1
    print(f"AUDIO ADAPTATIVO: {5} comprobaciones.")
    return 0


if __name__ == "__main__":
    sys.exit(main_())