"""Un turno de OpenCode que no acaba no puede dejar la tarea colgada.

`send_prompt()` deja la llamada abierta hasta DOS HORAS: OpenCode contesta
cuando la sesión queda libre. El orquestador solo termina cuando ese hilo
muele.

El caso malo se comprobó en el contenedor: OpenCode, sin ningún modelo
configurado, acepta el encargo, no lo ejecuta y nunca cierra el turno. La
tarea se quedó en RUNNING con el último evento siendo «OpenCode está
trabajando.» y sin decir nada más durante horas.

Aquí se usa el `_leer_mensajes` DE VERDAD (solo se tapan las llamadas a la
base de datos y la publicación de eventos). Si se tapara también ése, la
prueba pasaría sin comprobar nada: el detector se apoya en los mensajes que
ese método ve, así que sin él no hay forma de que detecte el movimiento.

    python test_atascamiento.py
"""
from __future__ import annotations

import importlib
import inspect
import os
import sys
import tempfile
import threading
import time
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BRAIN))
DATOS = tempfile.mkdtemp(prefix="jarvis-atasc-")
os.environ["JARVIS_DATA_DIR"] = DATOS

fallos: list = []


def comprobar(cond, texto):
    print(f"    {'· OK' if cond else 'FALLA'}  {texto}")
    if not cond:
        fallos.append(texto)
    return bool(cond)


class _AdaptadorFalso:
    """Imita a OpenCode según se.le pida: mudo, o hablando sin parar.

    Se le pasa un reloj para que los mensajes sean REALMENTE nuevos cada vez:
    si devolviera siempre el mismo id, `_leer_mensajes` lo vería una vez y lo
    daría por ya visto, que es justo lo que pasa cuando el trabajo avanza de
    verdad.
    """

    def __init__(self, vivo: bool = False):
        self.session_id = "ses_falsa"
        self.vivo = vivo
        self._n = 0

    def get_messages(self, limit=25):
        if not self.vivo:
            return []
        self._n += 1
        return [{"id": f"msg_{self._n}", "type": "assistant",
                 "agent": "build",
                 "content": [{"type": "text", "text": f"escribiendo el paso {self._n}…"}]}]

    def get_changes(self):
        if not self.vivo:
            return []
        self._n += 1
        return [{"mark": "+", "path": f"modulo{self._n % 5}.py"}]

    def pending_permissions(self):
        return []

    def pending_questions(self):
        return []


def _gestor_limpio(mod):
    """Un TaskManager con la base de datos y los eventos tapados.

    NO se tapa `_leer_mensajes`: es el que ve el movimiento y el detector de
    estancamiento se apoya en él.
    """
    g = mod.TaskManager()
    g._guardar = lambda t: None
    g._publicar = lambda *a, **k: None
    return g


def main_() -> int:
    print("ATASCAMIENTO · un turno muerto no cuelga la tarea")

    # --- 1. El reloj se puede ajustar, con suelo -------------------------
    os.environ["JARVIS_OPENCODE_SIN_PROGRESO"] = "25"
    mod = importlib.import_module("orchestrator.manager")
    importlib.reload(mod)
    comprobar(abs(mod._SIN_PROGRESO - 25.0) < 0.01,
              f"JARVIS_OPENCODE_SIN_PROGRESO=25 se respeta ({mod._SIN_PROGRESO})")

    os.environ["JARVIS_OPENCODE_SIN_PROGRESO"] = "1"
    importlib.reload(mod)
    comprobar(mod._SIN_PROGRESO == 600.0,
              "un valor de 1 s se rechaza y se queda el de 10 minutos "
              f"({mod._SIN_PROGRESO})")

    os.environ["JARVIS_OPENCODE_SIN_PROGRESO"] = "no-es-un-numero"
    importlib.reload(mod)
    comprobar(mod._SIN_PROGRESO == 600.0,
              "un valor que no es número no rompe nada")

    os.environ["JARVIS_OPENCODE_SIN_PROGRESO"] = "25"
    importlib.reload(mod)

    poll_original = mod._POLL
    mod._POLL = 0.2          # para que la prueba no dure media hora

    # --- 2. Turno muerto: se ata y avisa --------------------------------
    tarea = {"id": "dev_test", "project_path": DATOS, "payload": {}}
    gestor = _gestor_limpio(mod)
    nunca = threading.Event()
    hilo = threading.Thread(target=nunca.wait, daemon=True)
    hilo.start()

    t0 = time.time()
    cancelada = gestor._vigilar(tarea, _AdaptadorFalso(vivo=False),
                                hilo, threading.Event())
    dt = time.time() - t0

    comprobar(not cancelada, "no se dice que el señor la paró")
    comprobar(tarea["payload"].get("atascada") is True,
              "marca la tarea como ATASCADA")
    comprobar(20 <= dt <= 60,
              f"lo detecta en un rato ({dt:.0f} s), no en dos horas")
    comprobar(gestor._vistos.get("dev_test") is not None,
              "y el ciclo de mensajes se usó de verdad, no de mentira")

    # --- 3. Turno que falla al LEERSE también se ata ---------------------
    # Este es el caso que destapó el fallo: con la comprobación dentro del
    # `try`, una excepción en la lectura saltaba al `except` y el reloj de
    # estancamiento no llegaba a mirarse nunca. En el contenedor es justo lo
    # que pasa, porque la lectura falla cuando OpenCode no va bien.
    class _AdaptadorRoto(_AdaptadorFalso):
        def get_messages(self, limit=25):
            raise RuntimeError("no se puede leer la sesión")

        def pending_permissions(self):
            raise RuntimeError("no se pueden leer los permisos")

    tarea3 = {"id": "dev_rota", "project_path": DATOS, "payload": {}}
    gestor3 = _gestor_limpio(mod)
    roto = threading.Event()
    hilo3 = threading.Thread(target=roto.wait, daemon=True)
    hilo3.start()

    t0 = time.time()
    gestor3._vigilar(tarea3, _AdaptadorRoto(), hilo3, threading.Event())
    dt = time.time() - t0
    roto.set()

    comprobar(tarea3["payload"].get("atascada") is True,
              "una lectura que falla NO desactiva la detección")
    comprobar(20 <= dt <= 60,
              f"y se atasca igualmente en un rato ({dt:.0f} s)")

    # --- 4. Turno vivo: NO se dispara -----------------------------------
    # Aquí el vigilante NO tiene por qué terminar nunca: eso es lo que se
    # quiere comprobar. Se le da una orden de parada por el mismo sitio que
    # usaría el señor, y se mira que al acabar NO se haya declarado atascado
    # pese a haber pasado más tiempo que el umbral.
    tarea2 = {"id": "dev_viva", "project_path": DATOS, "payload": {}}
    gestor2 = _gestor_limpio(mod)
    vivo = threading.Event()
    hilo2 = threading.Thread(target=vivo.wait, daemon=True)
    hilo2.start()

    parar = threading.Event()
    # Se vigila más tiempo del umbral (25 s): si el reloj no se moviera con la
    # actividad, a los 25 s ya se habría declarado atascada.
    threading.Timer(mod._SIN_PROGRESO + 6.0, parar.set).start()

    t0 = time.time()
    cancelada2 = gestor2._vigilar(tarea2, _AdaptadorFalso(vivo=True),
                                   hilo2, parar)
    dt = time.time() - t0
    vivo.set()

    comprobar(cancelada2, "el vigilante sale cuando el señor lo para")
    comprobar(dt > mod._SIN_PROGRESO,
              f"ha vigilado más que el umbral ({dt:.0f} s > "
              f"{mod._SIN_PROGRESO:.0f} s)")
    comprobar(len(gestor2._vistos.get("dev_viva", ())) > 20,
              f"el turno vivo va soltando mensajes "
              f"({len(gestor2._vistos.get('dev_viva', ()))})")
    comprobar(not tarea2["payload"].get("atascada"),
              "un turno que sigue hablando NO se da por muerto")

    # --- 5. Y el cierre distingue los dos casos -------------------------
    os.environ["JARVIS_OPENCODE_SIN_PROGRESO"] = "600"
    importlib.reload(mod)
    fuente = inspect.getsource(mod.TaskManager._finalizar)
    comprobar("atascada" in fuente,
              "el cierre mira la marca de atascamiento")
    # El mensaje del atasco tiene que ganar al de «no tiene modelo»: una tarea
    # atascada SÍ estaba sin hacer nada, y si saliera el otro texto culparía
    # a la configuración cuando el problema es que OpenCode se quedó parado.
    i_atasco = fuente.find("if atascada:")
    i_modelo = fuente.find("elif sin_hacer:")
    comprobar(0 <= i_atasco < i_modelo,
              "y el mensaje del atasco va ANTES que el de «no tiene modelo»")

    # --- 6. `_resumen` aguanta comprobaciones mal formadas -------------
    # Se añadió una comprobación con la clave `detalle` en vez de `detail`, y
    # `_resumen` la leía con corchetes. El KeyError mató el hilo de la tarea y
    # el trabajo se quedó en RUNNING para siempre, sin cerrar ni con error.
    tarea4 = {"id": "dev_resumen", "idea": "algo", "created_at": time.time(),
              "project_path": DATOS, "payload": {}}
    gestor4 = mod.TaskManager()
    for nombre, comprobaciones in (
            ("sin ninguna", []),
            ("con la clave puesta", [{"ok": False, "grave": True,
                                      "detail": "se rompió"}]),
            ("con la clave QUE NO es la de _resumen",
             [{"ok": False, "grave": True, "detalle": "se rompió"}]),
            ("sin la clave de ok", [{"grave": True, "detail": "raro"}]),
            ("con campos que no son dict", ["no soy un dict"])):
        try:
            r = gestor4._resumen(tarea4, comprobaciones)
            comprobar(isinstance(r, dict) and "errores" in r,
                      f"el resumen sale aunque las comprobaciones traigan: "
                      f"{nombre}")
        except Exception as exc:  # noqa: BLE001
            comprobar(False, f"el resumen revienta con «{nombre}»: "
                             f"{type(exc).__name__}: {exc}")

    mod._POLL = poll_original
    nunca.set()
    vivo.set()

    print()
    if fallos:
        print(f"{len(fallos)} COMPROBACIONES FALLIDAS:")
        for f in fallos:
            print(f"  · {f}")
        return 1
    print("UN TURNO MUERTO NO CUELGA LA TAREA.")
    return 0


if __name__ == "__main__":
    sys.exit(main_())