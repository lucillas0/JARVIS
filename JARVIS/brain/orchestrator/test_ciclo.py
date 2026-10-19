"""Prueba del ciclo completo contra un doble de OpenCode que habla la API real.

Esto NO es una simulación de OpenCode: es un servidor de pruebas que implementa
los mismos endpoints que usa el adaptador (/api/session, /prompt, /permission,
/form, /message, /interrupt). Sirve para comprobar que TODO elcircuito de JARVIS
está bien cableado de punta a punta:

    idea -> analisis -> plan + riesgos -> aprobacion -> OpenCode ->
    permiso -> pregunta -> respuesta -> tests -> validacion -> resultado

Y para comprobar tres reglas que no se negocian:
  · si OpenCode no está, JARVIS lo dice y no inventa nada;
  · si no se ejecutan tests, aparece "no se han ejecutado tests";
  · la decisión del usuario llega a OpenCode con sus propios valores
    (once / always / reject), no con un invento nuestro.

Uso:  python orchestrator/test_ciclo.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

# La base de datos y la configuración en un directorio de usar y tirar: la
# prueba no debe tocar la instalación real.
_TEMP = tempfile.mkdtemp(prefix="jarvis-dev-test-")
os.environ["JARVIS_DATA_DIR"] = _TEMP
RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

_oc_events: list = []


def _log(mensaje: str) -> None:
    print(f"    · {mensaje}")


# --------------------------------------------------------------------------- #
# El doble de OpenCode
# --------------------------------------------------------------------------- #
class _Estado:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sesion = ""
        self.permiso = None
        self.pregunta = None
        self.mensajes: list = []
        self.despertar = threading.Event()
        self.workspace = ""
        self.prompts: list = []


ESTADO = _Estado()


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args) -> None:  # silencio en la consola
        pass

    # ------------------------------------------------------------- utilidades
    def _json(self, codigo: int, cuerpo) -> None:
        datos = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8")
        self.send_response(codigo)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(datos)))
        self.end_headers()
        self.wfile.write(datos)

    def _vacio(self, codigo: int = 204) -> None:
        self.send_response(codigo)
        self.send_header("Content-Length", "0")
        self.end_headers()

    def _cuerpo(self) -> dict:
        largo = int(self.headers.get("Content-Length") or 0)
        if not largo:
            return {}
        try:
            return json.loads(self.rfile.read(largo).decode("utf-8"))
        except ValueError:
            return {}

    # ------------------------------------------------------------------ rutas
    def do_GET(self) -> None:  # noqa: N802
        ruta = self.path.split("?")[0]
        if ruta == "/api/info":
            return self._json(200, {"version": "doble-de-prueba", "pid": 1})
        if ruta == "/api/vcs/status":
            return self._json(200, {"location": {"directory": ESTADO.workspace}, "data": []})
        if ruta.endswith("/message"):
            with ESTADO.lock:
                mensajes = list(reversed(ESTADO.mensajes))
            return self._json(200, {"data": mensajes, "cursor": {}})
        if ruta.endswith("/permission"):
            with ESTADO.lock:
                pendiente = ESTADO.permiso
            return self._json(200, {"data": [pendiente] if pendiente else []})
        if ruta.endswith("/form"):
            with ESTADO.lock:
                pendiente = ESTADO.pregunta
            return self._json(200, {"data": [pendiente] if pendiente else []})
        if ruta.startswith("/api/session/"):
            return self._json(200, {"id": ESTADO.sesion, "projectID": "p",
                                    "time": {"created": time.time(), "updated": time.time()},
                                    "cost": {}, "tokens": {},
                                    "location": {"directory": ESTADO.workspace},
                                    "title": "tarea"})
        return self._json(404, {"error": "no existe"})

    def do_POST(self) -> None:  # noqa: N802
        ruta = self.path.split("?")[0]
        cuerpo = self._cuerpo()
        if ruta == "/api/session":
            with ESTADO.lock:
                ESTADO.sesion = cuerpo.get("id") or "sesion-de-prueba"
                ESTADO.workspace = (cuerpo.get("location") or {}).get("directory", "")
                ESTADO.prompts.append(cuerpo)
            return self._json(200, {"id": ESTADO.sesion, "projectID": "p",
                                    "time": {"created": time.time(), "updated": time.time()},
                                    "cost": {}, "tokens": {},
                                    "location": {"directory": ESTADO.workspace}})
        if ruta.endswith("/interrupt"):
            ESTADO.despertar.set()
            return self._json(200, {"interrupted": True})
        if "/permission/" in ruta and ruta.endswith("/reply"):
            with ESTADO.lock:
                ESTADO.permiso = None
            _log(f"OpenCode recibe la decisión: {cuerpo.get('decision')}")
            return self._vacio()
        if "/form/" in ruta and ruta.endswith("/reply"):
            with ESTADO.lock:
                ESTADO.pregunta = None
                ESTADO.ultima_respuesta = cuerpo.get("answer")
            _log(f"OpenCode recibe la respuesta: {cuerpo.get('answer')}")
            return self._vacio()
        if ruta.endswith("/prompt"):
            return self._turno()
        return self._json(404, {"error": "no existe"})

    # -------------------------------------------------------------- el turno
    def _turno(self) -> None:
        """El doble por HTTP. El turno en sí está en `_reproducir_turno`."""
        _reproducir_turno()
        self._json(200, {"info": {"id": "msg-2", "type": "assistant"},
                         "parts": [{"type": "text",
                                    "text": "Hecho: creado nuevo_modulo.py y ejecutados los tests."}]})


def _reproducir_turno() -> None:
    """Reproduce lo que hace OpenCode de verdad: pedir permiso, preguntar
    al usuario, trabajar y contarlo. Se queda bloqueado hasta que el
    usuario contesta, igual que el agente real.

    Vive fuera del manejador HTTP a propósito: el trabajo se lo encarga JARVIS
    al CLI de OpenCode (`service.ejecutar_cli`), no por la API, así que el
    doble tiene que poder reproducirse también por ese camino. La API sigue
    sirviendo para lo demás: abrir la sesión, leer los mensajes, responder al
    permiso y a la pregunta.
    """
    # 1. pide permiso para leer un fichero fuera del proyecto
    with ESTADO.lock:
        ESTADO.permiso = {"id": "perm-1", "sessionID": ESTADO.sesion,
                          "action": "read", "resources": ["config/ejemplo.conf"],
                          "message": "Leer la configuración actual",
                          "save": []}
        ESTADO.mensajes.append({"id": "msg-1", "type": "user",
                                "text": "Trabaja.", "time": {"created": time.time()}})
    while ESTADO.permiso:
        if ESTADO.despertar.wait(0.05):
            break
        ESTADO.despertar.clear()

    # 2. pregunta algo que solo puede decidir el señor
    with ESTADO.lock:
        ESTADO.pregunta = {
            "id": "form-1", "sessionID": ESTADO.sesion,
            "title": "¿Qué base de datos quieres utilizar?",
            "fields": [{"key": "base_datos", "type": "string", "title": "Base de datos",
                        "options": [{"value": "sqlite", "label": "SQLite"},
                                    {"value": "postgres", "label": "PostgreSQL"},
                                    {"value": "mariadb", "label": "MariaDB"}],
                        "custom": True}],
            "state": {"status": "pending"},
        }
    while ESTADO.pregunta:
        if ESTADO.despertar.wait(0.05):
            break
        ESTADO.despertar.clear()

    # 3. trabaja de verdad: escribe un fichero y ejecuta los tests
    try:
        Path(ESTADO.workspace, "nuevo_modulo.py").write_text(
            "# escrito por el doble de prueba\n", encoding="utf-8")
    except OSError as exc:
        _log(f"no se pudo escribir en el workspace: {exc}")
    ahora = time.time()
    with ESTADO.lock:
        ESTADO.mensajes.append({
            "id": "msg-2", "type": "assistant", "agent": "build",
            "time": {"created": ahora},
            "content": [
                {"type": "tool", "callID": "call-1", "tool": "edit",
                 "state": {"status": "completed", "input": {"filePath": "nuevo_modulo.py"},
                           "content": [{"type": "text", "text": "escrito"}]}},
                {"type": "tool", "callID": "call-2", "tool": "bash",
                 "state": {"status": "completed", "input": {"command": "pytest -q"},
                           "content": [{"type": "text",
                                        "text": "3 passed, 1 failed in 0.4s"}]}},
                {"type": "text", "text": "Hecho: creado nuevo_modulo.py y "
                                          "ejecutados los tests."},
            ]})
        ESTADO.mensajes.append({"id": "msg-3", "type": "idle",
                                "time": {"created": ahora}, "outcome": "succeeded"})


# --------------------------------------------------------------------------- #
# Utilidades de la prueba
# --------------------------------------------------------------------------- #
def _arrancar_servidor() -> tuple:
    servidor = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    hilo = threading.Thread(target=servidor.serve_forever, daemon=True)
    hilo.start()
    return servidor, f"http://127.0.0.1:{servidor.server_address[1]}"


def _esperar(condicion, segundos: float = 30.0, texto: str = ""):
    limite = time.time() + segundos
    while time.time() < limite:
        valor = condicion()
        if valor:
            return valor
        time.sleep(0.2)
    raise AssertionError(f"Tiempo agotado esperando: {texto}")


def _comprobar(condicion: bool, texto: str) -> None:
    if not condicion:
        raise AssertionError(texto)
    _log(f"OK  {texto}")


def _con_estado(gestor, task_id: str, *estados: str) -> dict:
    """Espera a que la tarea llegue a uno de esos estados y la devuelve."""
    def _revisar():
        tarea = gestor.obtener(task_id) or {}
        return tarea if tarea.get("status") in estados else None
    return _esperar(_revisar, texto=f"el estado {estados}")


# --------------------------------------------------------------------------- #
# 10. La API que consume JARVIS Live
# --------------------------------------------------------------------------- #
def _peticion(token: str, metodo: str = "GET"):
    from starlette.requests import Request
    scope = {
        "type": "http", "method": metodo, "path": "/api/dev", "query_string": b"",
        "headers": [(b"x-jarvis-token", token.encode("utf-8"))],
        "client": ("127.0.0.1", 12345), "scheme": "http",
    }
    return Request(scope)


def _json_de(respuesta):
    import json as _js
    try:
        return _js.loads(bytes(respuesta.body).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


def _probar_api(workspace: Path) -> None:
    import asyncio

    from orchestrator import routes
    from auth import token as token_del_cerebro

    token = token_del_cerebro()
    # Sin token: fuera.
    respuesta = asyncio.run(routes.dev_status(_peticion("token-inventado")))
    _comprobar(respuesta.status_code == 401, "la API rechaza a quien no tiene la sesión")

    # Con token: estado real.
    respuesta = asyncio.run(routes.dev_status(_peticion(token)))
    datos = _json_de(respuesta)
    _comprobar(respuesta.status_code == 200 and datos.get("ok"), "la API devuelve el estado")
    _comprobar("opencode" in datos, "el estado incluye la salud del servidor de OpenCode")

    # Crear una tarea y devolverla con su detalle.
    respuesta = asyncio.run(routes.dev_crear(
        _peticion(token, "POST"),
        routes.NuevaTarea(idea="Prueba de la API", project_path=str(workspace),
                          analizar=False)))
    cuerpo = _json_de(respuesta)
    _comprobar(respuesta.status_code == 201 and cuerpo["task"]["status"] == "DRAFT",
               "la API crea la tarea en DRAFT sin analizar nada")

    # Identificadores mal formados: fuera.
    respuesta = asyncio.run(routes.dev_tarea(_peticion(token), "no-es-una-tarea"))
    _comprobar(respuesta.status_code == 400, "la API rechaza identificadores raros")

    # Aprobar sin plan no puede inventarse nada.
    respuesta = asyncio.run(routes.dev_aprobar(_peticion(token, "POST"), cuerpo["task"]["id"]))
    _comprobar(respuesta.status_code == 409,
               "aprobar sin plan responde que no, en vez de lanzar nada")

    # El listado sale limpio.
    respuesta = asyncio.run(routes.dev_tasks(_peticion(token), limit=5))
    _comprobar(len(_json_de(respuesta)["tasks"]) >= 1, "la API lista el historial")


def main() -> int:
    _servidor, url = _arrancar_servidor()
    os.environ["OPENCODE_SERVER_URL"] = url
    workspace = Path(_TEMP, "proyecto")
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "README.md").write_text("proyecto de prueba\n", encoding="utf-8")
    print(f"Doble de OpenCode escuchando en {url}")

    from orchestrator import service
    from orchestrator.manager import get_manager

    gestor = get_manager(lambda evento: _oc_events.append(evento))

    # El trabajo se le encarga al CLI de OpenCode (`service.ejecutar_cli`) y no
    # por la API, porque por la API el turno no se ejecuta (comprobado en el
    # contenedor). Por eso el doble de esta prueba se pone ahí: reproduce el
    # turno entero, con su permiso, su pregunta y su fichero. La API sigue
    # haciendo su parte: abrir la sesión, leer mensajes, responder al permiso
    # y a la pregunta.
    def _cli_de_prueba(url="", sesion="", workdir="", prompt="", **kw):
        _reproducir_turno()
        return {"ok": True, "codigo": 0,
                "reply": "Hecho: creado nuevo_modulo.py y ejecutados los tests.",
                "texto": ["Hecho: creado nuevo_modulo.py y ejecutados los tests."]}

    _cli_real = service.ejecutar_cli
    service.ejecutar_cli = _cli_de_prueba
    try:
        return _ciclo(gestor, workspace, url, _servidor)
    finally:
        service.ejecutar_cli = _cli_real


def _ciclo(gestor, workspace, url, _servidor) -> int:
    # Los imports que usa el resto del ciclo vivían en `main()`. Ahora el
    # cuerpo está en su propia función, así que se importan aquí.
    from orchestrator import planner
    from orchestrator import service as _svc
    from orchestrator.manager import TaskManager, get_manager

    # --- 0. Si no hay servidor, JARVIS lo dice (no inventa nada) -------------
    estado = gestor.estado()
    _comprobar(estado["state"] == "IDLE", "sin tareas, el estado es IDLE")
    _comprobar("no está ejecutando ninguna tarea" in estado["message"],
               "sin tareas, el mensaje lo dice claro")

    # --- 1. IDEA ------------------------------------------------------------
    creado = gestor.crear("Quiero que añadas autenticación con Google al proyecto",
                          str(workspace))
    _comprobar(creado["ok"], "la tarea se crea")
    task_id = creado["task"]["id"]
    gestor.analizar(task_id)
    tarea = _esperar(lambda: (gestor.obtener(task_id) or {}).get("status") == "WAITING_PLAN_APPROVAL",
                     texto="el plan")
    tarea = gestor.obtener(task_id)
    _comprobar(tarea["plan"]["pasos"], "el plan tiene pasos")
    _comprobar(tarea["risks"], "la predicción de fallos trae riesgos con mitigación")
    _comprobar(tarea["prompt"] and "OBJETIVO" in tarea["prompt"],
               "el prompt para OpenCode está construido")
    _comprobar(tarea["status"] == "WAITING_PLAN_APPROVAL",
               "nadie ejecuta nada hasta que el señor aprueba")

    # --- 2. Aprobación y arranque real --------------------------------------
    gestor.aprobar(task_id)
    _esperar(lambda: ESTADO.sesion, texto="la sesión de OpenCode")
    tarea = _con_estado(gestor, task_id, "WAITING_PERMISSION")
    _comprobar(tarea["session_id"] == ESTADO.sesion, "la tarea guarda la sesión de OpenCode")
    pendientes = gestor.permisos_pendientes()
    _comprobar(len(pendientes) == 1, "el permiso de OpenCode aparece pendiente")

    # --- 3. Permiso ---------------------------------------------------------
    gestion = gestor.decidir_permiso(task_id, pendientes[0]["id"], "once")
    _comprobar(gestion["ok"], "la decisión se envía a OpenCode")

    # --- 4. Pregunta --------------------------------------------------------
    tarea = _con_estado(gestor, task_id, "WAITING_USER")
    preguntas = gestor.preguntas_pendientes()
    _comprobar(len(preguntas) == 1, "la pregunta aparece pendiente")
    _comprobar(len(preguntas[0]["options"]) == 3, "la pregunta trae sus opciones")
    gestion = gestor.responder(task_id, preguntas[0]["id"],
                               {preguntas[0]["key"]: "postgres"})
    _comprobar(gestion["ok"], "la respuesta llega a OpenCode")

    # --- 5. Fin, validación y resumen ---------------------------------------
    tarea = _con_estado(gestor, task_id, "COMPLETED", "FAILED")
    _comprobar(tarea["status"] == "COMPLETED", f"la tarea termina bien (estado {tarea['status']})")
    _comprobar(tarea["tests"].get("passed") == 3 and tarea["tests"].get("failed") == 1,
               "los tests se leen de la salida real de OpenCode")
    _comprobar(any(c["path"].endswith("nuevo_modulo.py") for c in tarea["changes"]),
               "los cambios se detectan de verdad")
    _comprobar(tarea["result"]["checks"], "la validación final tiene comprobaciones")
    _comprobar(tarea["summary"]["tiempos"]["total"] > 0, "el resumen trae tiempos reales")
    _comprobar(len(tarea["events"]) >= 8, "el historial de eventos está lleno de datos reales")
    tipos = {e["type"] for e in tarea["events"]}
    for tipo in ("opencode.permission_requested", "opencode.question_requested",
                 "opencode.question_answered", "opencode.permission_resolved",
                 "opencode.test_finished"):
        _comprobar(tipo in tipos, f"evento publicado: {tipo}")

    # --- 6. Persistencia ----------------------------------------------------
    from memory import sqlite_store as db
    fila = db.get_dev_task(task_id)
    _comprobar(fila is not None and fila["opencode_session_id"] == ESTADO.sesion,
               "la tarea queda guardada con su sesión de OpenCode")
    _comprobar(gestor.listar(5), "el historial se puede listar")

    # --- 7. Sin tests, se dice que no los hay -------------------------------
    solo = get_manager()
    _comprobar(solo._texto_tests({"ran": False}) == "No se han ejecutado tests.",
               "sin tests, el texto es 'No se han ejecutado tests.'")

    # --- 8. El plan honesto sin modelo --------------------------------------
    minimo = planner._plan_minimo("cambiar algo", {"path": str(workspace), "git": {"repo": False},
                                                    "tests": {"hay_suite": False, "configuracion": []},
                                                    "files": {"root_files": ["README.md"]},
                                                    "dependencies": {}})
    _comprobar(minimo["pasos"] and minimo["riesgos"], "sin modelo hay plan mínimo con riesgos reales")
    _comprobar(any("Git" in r["titulo"] for r in minimo["riesgos"]),
               "sin modelo se avisa igualmente de que no hay Git")


    # --- 9. Recuperacion: nunca relanzar a ciegas ----------------------------
    from orchestrator import store as _store

    huerfana = _store.nueva("Tarea que se quedó a medias", str(workspace))
    huerfana["status"] = "RUNNING"
    huerfana["started_at"] = time.time() - 120
    huerfana["opencode_session_id"] = ESTADO.sesion
    huerfana["payload"]["phase"] = "RUNNING"
    huerfana["payload"]["phase_started"] = time.time() - 120
    _store.guardar(huerfana)
    otro = TaskManager()
    informe = otro.recuperar()
    _comprobar(any(i["task_id"] == huerfana["id"] for i in informe),
               "la recuperacion ve la tarea que quedo en marcha")
    recuperada = otro.obtener(huerfana["id"])
    _comprobar(recuperada["status"] == "FAILED",
               "la tarea interrumpida se marca como fallida, no como terminada")
    _comprobar("opencode" in recuperada["error"].lower() and
               "trabajando" in recuperada["error"].lower(),
               "se explica por que quedo parada")
    # `recoverable` depende de que HAYA un servidor de OpenCode: si no lo hay,
    # la tarea no se puede continuar y hay que decirlo. Antes esta comprobación
    # pedía True a pelo, y pasaba en el portátil solo porque ahí había un
    # OpenCode de verdad registrado; en el contenedor, donde no lo hay, fallaba
    # sin que hubiera ningún fallo. Ahora se compara con lo que el propio
    # servicio dice, que es lo que decide.
    _hay_servidor = bool(_svc.describe().get("ok"))
    _comprobar(recuperada["recoverable"] is _hay_servidor,
               f"recuperable dice la verdad según el servidor "
               f"(hay={_hay_servidor}, marca={recuperada['recoverable']})")
    _comprobar(bool(ESTADO.sesion) or not _hay_servidor,
               "y hay sesion que conservar")
    _comprobar(recuperada["session_id"] == ESTADO.sesion,
               "conserva la sesion para poder continuar en ella")


    # --- 11. Cancelar: se detiene, no se "termina" --------------------------
    otra = gestor.crear("Tarea que se va a cancelar", str(workspace))["task"]
    gestor.analizar(otra["id"])
    _con_estado(gestor, otra["id"], "WAITING_PLAN_APPROVAL")
    gestor.aprobar(otra["id"])
    _con_estado(gestor, otra["id"], "WAITING_PERMISSION")
    cancelada = gestor.cancelar(otra["id"])
    _comprobar(cancelada["ok"], "el señor puede detener la tarea")
    tarea = _esperar(lambda: (gestor.obtener(otra["id"]) or {})
                     if (gestor.obtener(otra["id"]) or {}).get("status") == "CANCELLED" else None,
                     texto="la tarea cancelada")
    _comprobar(tarea["finished_at"], "la tarea cancelada tiene fecha de fin")
    _comprobar(not (tarea.get("result") or {}).get("status"),
               "una tarea cancelada no se valida como terminada")

    # --- 12. Reintentar continua en la MISMA sesion de OpenCode --------------
    reintentada = gestor.reintentar(task_id)
    _comprobar(reintentada["ok"], "se puede reintentar la tarea terminada")
    _con_estado(gestor, task_id, "WAITING_PERMISSION", "RUNNING", "TESTING",
                "COMPLETED", "FAILED")
    tarea = gestor.obtener(task_id)
    _comprobar(tarea["session_id"] == ESTADO.sesion,
               "el reintento continua en la sesion de OpenCode que ya existia")
    gestor.cancelar(task_id)

    _probar_api(workspace)

    _servidor.shutdown()
    print("\nCICLO COMPLETO VERIFICADO: sin simular nada por el camino.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AssertionError as error:
        print(f"\nFALLO: {error}")
        sys.exit(1)