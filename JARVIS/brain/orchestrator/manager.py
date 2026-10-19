"""El orquestador: JARVIS dirige, OpenCode ejecuta.

Una tarea recorre el ciclo real completo:

  IDEA -> ANÁLISIS -> PLAN + RIESGOS -> APROBACIÓN -> OPENCODE ->
  EJECUCIÓN -> PERMISOS -> PREGUNTAS -> TESTS -> VALIDACIÓN -> RESULTADO

Cada paso escribe en la base de datos y publica eventos en el bus que JARVIS
ya usa. JARVIS Live escucha ese bus; Discord escucha el mismo.

Aquí no hay ningún temporizador que simule progreso: lo que se muestra son
llamadas a herramientas, ficheros tocados, permisos pedidos y tests leídos de
la salida real de OpenCode. Si OpenCode no ha dicho nada, no aparece nada.
"""
from __future__ import annotations

import logging
import os
import threading
import time
from typing import Callable, Optional

from . import adapter as oc
from . import inbox, planner, service, store

log = logging.getLogger("brain.opencode.manager")

# A qué cubo de tiempo pertenece cada estado (§37 y §39: ejecución y espera
# se cuentan por separado).
_CUBO = {
    "ANALYZING": "analysis",
    "PLANNING": "planning",
    "WAITING_PLAN_APPROVAL": "approval",
    "STARTING_OPENCODE": "execution",
    "RUNNING": "execution",
    "TESTING": "tests",
    "WAITING_PERMISSION": "permission",
    "WAITING_USER": "question",
}

_POLL = 1.2          # cada cuánto se mira la sesión real
_GUARDAR = 4.0       # cada cuánto se vuelca la tarea a la base de datos
_CAMBIOS = 8.0       # cada cuánto se releen los cambios del proyecto

# Cuánto aguanta un turno de OpenCode SIN dar señales de vida antes de darlo
# por muerto.
#
# `send_prompt()` deja la llamada abierta hasta dos horas: OpenCode contesta
# cuando la sesión queda libre, así que un turno de verdad puede tardar todo lo
# que haga falta. El problema es el otro caso, comprobado en el contenedor: si
# OpenCode no tiene ningún modelo configurado, acepta el encargo, no lo ejecuta
# y NUNCA cierra el turno. La petición se queda colgada, el hilo que la espera
# no termina, y la tarea se queda en RUNNING durante horas sin decir nada.
#
# Diez minutos sin ningún mensaje nuevo, ningún fichero tocado y ninguna
# pregunta es tiempo de sobra para que cualquier trabajo real dé señales. Un
# desarrollo largo no se dispara: sigue hablando y sigue escribiendo ficheros.
#
# Se puede cambiar con JARVIS_OPENCODE_SIN_PROGRESO (segundos) para quien
# tenga un presupuesto de tiempo distinto, y para probar el camino sin tener
# que esperar diez minutos.
_SIN_PROGRESO = 600.0
try:
    _ajuste = float(os.environ.get("JARVIS_OPENCODE_SIN_PROGRESO", "") or 0)
    if _ajuste >= 5.0:
        _SIN_PROGRESO = _ajuste
except (TypeError, ValueError):
    pass
_TEST_CMDS = ("npm install", "pip install", "npm ci", "yarn add", "pnpm add")


def _nombre_de_proyecto(idea: str) -> str:
    """Un nombre de carpeta corto y seguro a partir de la idea."""
    import re as _re
    import unicodedata as _ud
    if not idea:
        return ""
    t = _ud.normalize("NFKD", idea.lower())
    t = "".join(c for c in t if not _ud.combining(c))
    t = _re.sub(r"^(desarrollame|desarrolla|programame|programa|creame|crea|"
                r"hazme|haz|quiero|necesito|montame|monta|construye|construyeme|"
                r"una?|el|la|los|las|de|del|para|que|con)\b", " ", t)
    # Palabras de relleno: no dicen qué proyecto es. Se quitan en cualquier
    # posición, no solo al principio, porque el nombre sale de una frase entera
    # («desarrollame una web para llevar los gastos» → "gastos").
    t = _re.sub(r"\b(una?|el|la|los|las|de|del|para|que|con|por|sin|"
                r"app|web|programa|script|proyecto|pagina|aplicacion|"
                r"herramienta|cosa|aplicacion)\b", " ", t)
    t = _re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    return t[:40].strip("-")


def _es_codigo_de_jarvis(ruta: str) -> bool:
    """¿Esta ruta está dentro del propio JARVIS?"""
    try:
        import config
        raices = []
        for nombre in ("ROOT_DIR", "BRAIN_DIR"):
            valor = getattr(config, nombre, None)
            if valor:
                raices.append(str(valor))
    except Exception:  # noqa: BLE001
        return False
    if not raices:
        return False
    try:
        objetivo = os.path.abspath(ruta)
    except Exception:  # noqa: BLE001
        return False
    for raiz in raices:
        try:
            if os.path.commonpath([objetivo, os.path.abspath(raiz)]) == \
                    os.path.abspath(raiz):
                return True
        except (ValueError, OSError):
            continue
    return False


def _a_carpeta_segura(ruta: str, idea: str) -> str:
    """Si la ruta cae dentro del código de JARVIS, decide si se deja pasar.

    Solo se deja si el señor ha pedido claramente tocar JARVIS. Una idea
    corriente («una web para los gastos») que acabe apuntando al cerebro es un
    descuido con consecuencias: OpenCode escribiría encima del código que lo
    está haciendo funcionar.
    """
    if not ruta:
        return ruta
    if not _es_codigo_de_jarvis(ruta):
        return ruta
    _re = __import__("re")
    if _re.search(r"\b(j|a)rvis\b|\bcerebro\b|\bnucleo\b|\bbrain\b|"
                  r"\bplugin\b|\bherramienta\b|\btool\b|\bfuente\b",
                  idea or "", _re.I):
        return ruta
    log.warning("la tarea pedía trabajar dentro del código de JARVIS (%s) "
                "y la idea no habla de modificarlo: se va a la carpeta de "
                "proyectos", ruta)
    return ""


class TaskManager:
    """Todas las tareas de desarrollo con OpenCode, con su ciclo completo."""

    def __init__(self, emit: Optional[Callable[[dict], None]] = None):
        self._emit = emit
        self._tareas: dict[str, dict] = {}
        self._adaptadores: dict[str, oc.OpenCodeAdapter] = {}
        self._hilos: dict[str, threading.Thread] = {}
        self._cancelar: dict[str, threading.Event] = {}
        self._vistos: dict[str, set] = {}
        self._lock = threading.RLock()
        inbox.set_manager(self)

    # ------------------------------------------------------------------ #
    # Bus de eventos
    # ------------------------------------------------------------------ #
    def set_emit(self, fn) -> None:
        self._emit = fn

    def _publicar(self, tipo: str, tarea: Optional[dict] = None, **campos) -> dict:
        evento = {"type": tipo, "at": time.time(), **campos}
        if tarea is not None:
            evento["task_id"] = tarea["id"]
            historial = tarea["payload"].setdefault("events", [])
            historial.append({"type": tipo, "at": evento["at"], **campos})
            if len(historial) > 400:
                del historial[:-400]
        if self._emit:
            try:
                self._emit(evento)
            except Exception as exc:  # noqa: BLE001 - el bus no puede tumbar la tarea
                log.debug("no se pudo publicar %s: %s", tipo, exc)
        return evento

    # ------------------------------------------------------------------ #
    # Estados y tiempos
    # ------------------------------------------------------------------ #
    def _set_status(self, tarea: dict, estado: str, detalle: str = "") -> None:
        ahora = time.time()
        p = tarea["payload"]
        anterior = tarea.get("status")
        if anterior == estado and estado not in ("TESTING",):
            p["current_action"] = detalle or p.get("current_action", "")
            self._guardar(tarea)
            return
        inicio = p.get("phase_started")
        cubo = _CUBO.get(anterior or "")
        if inicio and cubo:
            p["timings"][cubo] = p["timings"].get(cubo, 0.0) + (ahora - inicio)
        tarea["status"] = estado
        p["phase"] = estado
        p["phase_started"] = ahora
        if detalle:
            p["current_action"] = detalle[:300]
        if estado in ("WAITING_PERMISSION", "WAITING_USER"):
            p["waiting_since"] = ahora
        else:
            p["waiting_since"] = None
        if estado in ("RUNNING", "TESTING") and not tarea.get("started_at"):
            tarea["started_at"] = ahora
        if estado in ("COMPLETED", "FAILED", "CANCELLED"):
            tarea["finished_at"] = ahora
        self._publicar("opencode.state", tarea, state=estado, previous=anterior,
                       detail=detalle or estado)
        self._publicar("action_status", tarea, label=detalle or f"OpenCode: {estado}",
                       state="waiting_approval" if estado in ("WAITING_PERMISSION", "WAITING_USER") else "running")
        self._guardar(tarea)

    def _guardar(self, tarea: dict) -> None:
        store.guardar(tarea)
        with self._lock:
            self._tareas[tarea["id"]] = tarea

    # ------------------------------------------------------------------ #
    # Ciclo de vida público
    # ------------------------------------------------------------------ #
    def crear(self, idea: str, project_path: str = "", model: str = "",
              agent: str = "") -> dict:
        idea = (idea or "").strip()
        if not idea:
            return {"ok": False, "error": "Dime qué quieres que haga JARVIS."}
        if len(idea) > 4000:
            idea = idea[:4000]
        project_path = _a_carpeta_segura(project_path, idea)
        if not project_path:
            project_path = self._ruta_por_defecto(idea)
        tarea = store.nueva(idea, project_path, model, agent)
        self._guardar(tarea)
        self._publicar("opencode.task_created", tarea,
                       detail=f"Nueva tarea: {idea[:120]}")
        self._publicar("live_task_start", tarea, task_id=tarea["id"],
                       title=f"OpenCode · {idea[:80]}", message=idea,
                       description="Desarrollo con OpenCode")
        return {"ok": True, "task": self.resumen(tarea)}

    def analizar(self, task_id: str) -> dict:
        tarea = self._tareas.get(task_id) or store.cargar(task_id)
        if tarea is None:
            return {"ok": False, "error": "Esa tarea no existe."}
        if tarea["status"] in ("ANALYZING", "PLANNING"):
            return {"ok": True, "task": self.resumen(tarea),
                    "message": "Ese análisis ya está en marcha."}
        with self._lock:
            if self._hilos.get(task_id) and self._hilos[task_id].is_alive():
                return {"ok": True, "task": self.resumen(tarea),
                        "message": "Esa tarea ya está trabajando."}
        self._set_status(tarea, "ANALYZING", "Leyendo el proyecto de verdad…")
        hilo = threading.Thread(target=self._analizar, args=(tarea,), daemon=True,
                                name=f"jarvis-plan-{task_id}")
        with self._lock:
            self._hilos[task_id] = hilo
        hilo.start()
        return {"ok": True, "task": self.resumen(tarea),
                "message": "JARVIS está analizando el proyecto."}

    def _analizar(self, tarea: dict) -> None:
        """Lee el proyecto, pide el diseño, predice riesgos y escribe el prompt."""
        try:
            self._publicar("opencode.analysis_started", tarea,
                           detail=f"Analizando {tarea['project_path']}")
            self._set_status(tarea, "PLANNING", "Diseñando la solución y los riesgos…")
            resultado = planner.analizar(tarea["idea"], tarea["project_path"])
            diseno = resultado["design"]
            riesgos = planner.predecir_riesgos(diseno, resultado["scan"], tarea["idea"])
            estimacion = planner.estimar(diseno, riesgos)
            prompt = planner.construir_prompt(
                tarea["idea"], diseno, resultado["scan"], riesgos,
                tarea["project_path"], tarea["payload"].get("notes"))
            p = tarea["payload"]
            p["analysis"] = {"source": resultado["source"],
                             "model_error": resultado.get("model_error", ""),
                             "scan": resultado.get("scan", {}),
                             "facts": resultado.get("facts", "")}
            p["plan"] = diseno
            p["risks"] = riesgos
            p["estimate"] = estimacion
            p["prompt"] = prompt
            self._set_status(tarea, "WAITING_PLAN_APPROVAL",
                             f"Plan listo: {len(diseno.get('pasos') or [])} pasos, "
                             f"{len(riesgos)} riesgos. {estimacion['label']}.")
            self._publicar("opencode.plan_ready", tarea,
                           detail=f"{len(diseno.get('pasos') or [])} pasos, "
                                  f"{len(riesgos)} riesgos. {estimacion['label']}")
        except Exception as exc:  # noqa: BLE001 - un análisis fallido deja la tarea explicada
            log.exception("análisis fallido en %s", tarea["id"])
            tarea["payload"]["error"] = f"No se pudo analizar el proyecto: {exc}"[:300]
            self._set_status(tarea, "FAILED", tarea["payload"]["error"])

    def revisar(self, task_id: str, nota: str) -> dict:
        tarea = self._tareas.get(task_id) or store.cargar(task_id)
        if tarea is None:
            return {"ok": False, "error": "Esa tarea no existe."}
        if tarea["status"] not in ("WAITING_PLAN_APPROVAL", "FAILED", "DRAFT"):
            return {"ok": False,
                    "error": "El plan solo se puede modificar antes de aprobarlo."}
        nota = (nota or "").strip()
        if not nota:
            return {"ok": False, "error": "Dime qué quieres cambiar del plan."}
        scan = (tarea["payload"].get("analysis") or {}).get("scan") or \
            planner.scan_project(tarea["project_path"])
        diseno, cambios, error = planner.revisar(tarea["payload"].get("plan") or {},
                                                 nota, scan, tarea["project_path"])
        if error and not cambios:
            return {"ok": False, "error": error}
        p = tarea["payload"]
        p["notes"].append(nota[:600])
        p["revisions"].append({"at": time.time(), "note": nota[:600],
                               "changes": cambios, "error": error})
        p["plan"] = diseno
        p["risks"] = planner.predecir_riesgos(diseno, scan, tarea["idea"])
        p["estimate"] = planner.estimar(diseno, p["risks"])
        p["prompt"] = planner.construir_prompt(tarea["idea"], diseno, scan, p["risks"],
                                              tarea["project_path"], p["notes"])
        self._set_status(tarea, "WAITING_PLAN_APPROVAL",
                         "Plan modificado: " + ("; ".join(cambios[:3]) or "sin cambios de fondo"))
        self._publicar("opencode.plan_revised", tarea, detail="; ".join(cambios[:4]))
        return {"ok": True, "task": self.resumen(tarea), "changes": cambios}

    def aprobar(self, task_id: str) -> dict:
        tarea = self._tareas.get(task_id) or store.cargar(task_id)
        if tarea is None:
            return {"ok": False, "error": "Esa tarea no existe."}
        if tarea["status"] not in ("WAITING_PLAN_APPROVAL", "DRAFT"):
            return {"ok": False, "error": f"La tarea está en {tarea['status']}."}
        if not tarea["payload"].get("prompt"):
            return {"ok": False, "error": "Todavía no hay plan ni prompt."}
        self._lanzar(tarea, tarea["payload"]["prompt"], reanudar=False)
        return {"ok": True, "task": self.resumen(tarea),
                "message": "Enviando a OpenCode."}

    def reintentar(self, task_id: str) -> dict:
        """Reintenta: si la sesión de OpenCode sigue viva, se continúa en ella."""
        tarea = self._tareas.get(task_id) or store.cargar(task_id)
        if tarea is None:
            return {"ok": False, "error": "Esa tarea no existe."}
        if store.vivo(tarea["status"]):
            return {"ok": False, "error": "Esa tarea todavía está en marcha."}
        p = tarea["payload"]
        reanudar = bool(tarea.get("opencode_session_id"))
        if reanudar:
            texto = (f"Continúa con el trabajo anterior en esta misma sesión. "
                     f"Retoma desde donde lo dejaste y termina el plan:\n"
                     f"{p.get('prompt') or tarea['idea']}"[:6000])
        else:
            texto = p.get("prompt") or ""
        if not texto:
            return {"ok": False, "error": "No hay prompt guardado para reintentar."}
        p["attempts"] = int(p.get("attempts") or 0) + 1
        p["error"] = ""
        p["recoverable"] = False
        tarea["finished_at"] = None
        self._lanzar(tarea, texto, reanudar=reanudar)
        return {"ok": True, "task": self.resumen(tarea),
                "message": ("Continuando la sesión de OpenCode." if reanudar
                            else "Relanzando la tarea en OpenCode.")}

    def cancelar(self, task_id: str, motivo: str = "") -> dict:
        tarea = self._tareas.get(task_id) or store.cargar(task_id)
        if tarea is None:
            return {"ok": False, "error": "Esa tarea no existe."}
        evento = self._cancelar.get(task_id)
        if evento:
            evento.set()
        adaptador = self._adaptadores.get(task_id)
        if adaptador is not None:
            try:
                adaptador.pause()
            except Exception as exc:  # noqa: BLE001
                log.debug("interrupción de OpenCode: %s", exc)
        detalle = motivo or "Detenida por el usuario. Lo ya escrito se queda."
        if not store.vivo(tarea["status"]):
            return {"ok": False, "error": f"La tarea ya estaba en {tarea['status']}."}
        self._set_status(tarea, "CANCELLED", detalle)
        self._publicar("opencode.error", tarea, detail=detalle, cancelled=True)
        self._publicar("live_task_end", tarea, task_id=tarea["id"],
                       title="OpenCode · tarea detenida", failed=False)
        self._cerrar(tarea)
        return {"ok": True, "task": self.resumen(tarea), "message": detalle}

    def pausar(self, task_id: str) -> dict:
        """Interrumpe el turno de OpenCode sin matar la sesión."""
        tarea = self._tareas.get(task_id)
        adaptador = self._adaptadores.get(task_id)
        if tarea is None or adaptador is None:
            return {"ok": False, "error": "Esa tarea no está ejecutándose."}
        # Se guarda de qué estado venía: al reanudar hay que volver a él, no
        # inventar un RUNNING que puede no ser el correcto.
        tarea.setdefault("payload", {})["estado_antes_de_pausa"] = tarea.get("status")
        res = adaptador.pause()
        if res.get("ok"):
            tarea["status"] = "PAUSED"
            store.guardar(tarea)
            self._publicar("opencode.stopped", tarea,
                           detail=res.get("detail", "Turno interrumpido."))
        return res

    def reanudar(self, task_id: str) -> dict:
        """Continúa una tarea pausada, con la MISMA sesión de OpenCode.

        No es reintentar: `reintentar()` lanza la misión otra vez desde el
        principio, y aquí lo que se quiere es seguir donde se dejó, con los
        cambios ya escritos. Por eso no crea sesión nueva: si no encuentra la
        sesión guardada, lo dice en vez de duplicar trabajo.
        """
        tarea = self._tareas.get(task_id) or store.cargar(task_id)
        if tarea is None:
            return {"ok": False, "error": "Esa tarea no existe."}
        if tarea.get("status") not in ("PAUSED", "WAITING_USER", "WAITING_PERMISSION",
                                       "ERROR"):
            return {"ok": False,
                    "error": f"La tarea está en {tarea.get('status')}: no está "
                             f"pausada, así que no hay nada que reanudar."}
        adaptador = self._adaptadores.get(task_id) or self._recolocar(tarea)
        if adaptador is None:
            return {"ok": False,
                    "error": "No encuentro la sesión de OpenCode. Reintenta la "
                             "tarea para empezar de cero."}
        res = adaptador.resume()
        if not res.get("ok"):
            return res
        # Se vuelve al estado que tinha antes de la pausa, si se sabe.
        anterior = (tarea.get("payload") or {}).get("estado_antes_de_pausa") \
            or "RUNNING"
        if anterior not in store.ESTADOS_VIVOS:
            anterior = "RUNNING"
        tarea["status"] = anterior
        (tarea.get("payload") or {}).pop("estado_antes_de_pausa", None)
        store.guardar(tarea)
        self._tareas[task_id] = tarea
        self._publicar("opencode.started", tarea,
                       detail=res.get("detail", "Reanudada."))
        return {"ok": True, "status": anterior}

    def responder(self, task_id: str, question_id: str, answer: dict) -> dict:
        """Entrega la respuesta del usuario a OpenCode."""
        tarea = self._tareas.get(task_id) or store.cargar(task_id)
        adaptador = self._adaptadores.get(task_id)
        if tarea is None:
            return {"ok": False, "error": "Esa tarea no existe."}
        if adaptador is None:
            # La sesión puede haber sobrevivido aunque JARVIS no la vigilase.
            adaptador = self._recolocar(tarea)
        if adaptador is None:
            return {"ok": False, "error": "No hay sesión de OpenCode para responder."}
        if not isinstance(answer, dict) or not answer:
            return {"ok": False, "error": "La respuesta está vacía."}
        res = adaptador.send_answer(question_id, answer)
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error", "OpenCode no aceptó la respuesta.")}
        p = tarea["payload"]
        for pregunta in p.get("questions", []):
            if pregunta.get("id") == question_id:
                pregunta["status"] = "respondida"
                pregunta["answered_at"] = time.time()
                pregunta["answer"] = answer
        p.setdefault("answers", []).append(
            {"question_id": question_id, "answer": answer, "at": time.time(),
             "origin": "JARVIS Live"})
        self._cerrar_esperas(tarea, adaptador)
        return {"ok": True, "message": "Respuesta enviada a OpenCode."}

    def decidir_permiso(self, task_id: str, permission_id: str, decision: str) -> dict:
        """Devuelve la decisión al Permission Manager de OpenCode."""
        tarea = self._tareas.get(task_id) or store.cargar(task_id)
        adaptador = self._adaptadores.get(task_id)
        if tarea is None:
            return {"ok": False, "error": "Esa tarea no existe."}
        if decision not in oc.PERMISSION_DECISIONS:
            return {"ok": False, "error": "Decisión no válida para OpenCode."}
        if adaptador is None:
            adaptador = self._recolocar(tarea)
        # Antes incluso de buscar la sesión: ¿esta solicitud sigue viva? Una
        # que ya se resolvió, o que caducó, no se vuelve a enviar. Se comprueba
        # antes que la sesión porque el motivo importa más que el síntoma: si la
        # decisión es antigua, decir "no hay sesión" manda al señor a mirar el
        # sitio equivocado.
        p = tarea.get("payload") or {}
        el_permiso = next((x for x in p.get("permissions", [])
                           if x.get("id") == permission_id), None)
        if el_permiso is None:
            return {"ok": False,
                    "error": "Ese permiso ya no está en la tarea: no se envía nada."}
        if el_permiso.get("status") != "pending":
            return {"ok": False,
                    "error": f"Ese permiso ya está {el_permiso.get('status')}: "
                             f"no se vuelve a enviar."}
        if el_permiso.get("expires_at") and time.time() > float(
                el_permiso["expires_at"]):
            el_permiso["status"] = "caducado"
            store.guardar(tarea)
            return {"ok": False,
                    "error": "Ese permiso caducó antes de decidirse: "
                             "OpenCode tendrá que pedirlo otra vez."}
        if adaptador is None:
            return {"ok": False, "error": "No hay sesión de OpenCode para responder."}
        res = adaptador.reply_permission(permission_id, decision)
        if not res.get("ok"):
            return {"ok": False, "error": res.get("error", "OpenCode no aceptó la decisión.")}
        for permiso in p.get("permissions", []):
            if permiso.get("id") == permission_id:
                # OJO: el estado vive en inglés ("pending") porque es lo que
                # genera el adaptador de OpenCode y lo que mira
                # permisos_pendientes(). Aqui se escribia "resuelta", en
                # castellano, y se mezclaban los dos vocabularios: por eso
                # una comprobación que buscase "pending" después de resolver
                # no encontraba nada.
                permiso["status"] = "resolved"
                permiso["decision"] = decision
                permiso["resolved_at"] = time.time()
                if permiso.get("requested_at"):
                    permiso["wait_seconds"] = round(
                        permiso["resolved_at"] - permiso["requested_at"], 1)
        self._cerrar_esperas(tarea, adaptador)
        return {"ok": True, "message": f"Permiso enviado a OpenCode: {oc.PERMISSION_LABEL.get(decision, decision)}."}

    # ------------------------------------------------------------------ #
    # Consultas
    # ------------------------------------------------------------------ #
    def obtener(self, task_id: str) -> Optional[dict]:
        tarea = self._tareas.get(task_id) or store.cargar(task_id)
        return self.detalle(tarea) if tarea else None

    def listar(self, limit: int = 20) -> list[dict]:
        filas = store.cargar_todas(limit)
        return [self.resumen(f) for f in filas]

    def preguntas_pendientes(self) -> list[dict]:
        salida = []
        for tarea in self._tareas.values():
            if tarea["status"] not in ("WAITING_USER",):
                continue
            for pregunta in tarea["payload"].get("questions", []):
                if pregunta.get("status") == "pending":
                    salida.append({**pregunta, "task_id": tarea["id"]})
        return salida

    def permisos_pendientes(self) -> list[dict]:
        salida = []
        for tarea in self._tareas.values():
            if tarea["status"] != "WAITING_PERMISSION":
                continue
            for permiso in tarea["payload"].get("permissions", []):
                if permiso.get("status") == "pending":
                    salida.append({**permiso, "task_id": tarea["id"]})
        return salida

    def estado(self) -> dict:
        """Lo que ve el panel: servidor, sesión y tareas vivas."""
        servidor = service.describe()
        tareas = [self.resumen(t) for t in self._tareas.values() if store.vivo(t["status"])]
        vivas = [t for t in tareas if t["status"] in
                 ("RUNNING", "TESTING", "STARTING_OPENCODE", "ANALYZING", "PLANNING")]
        return {
            "ok": True,
            "opencode": servidor,
            "running": bool(vivas),
            "state": self._estado_opencode(tareas),
            "tasks": sorted(tareas, key=lambda t: -(t.get("updated_at") or 0)),
            "message": "" if vivas else "OpenCode no está ejecutando ninguna tarea.",
            "waiting": {
                "permissions": len(self.permisos_pendientes()),
                "questions": len(self.preguntas_pendientes()),
            },
        }

    @staticmethod
    def _estado_opencode(tareas: list[dict]) -> str:
        for tarea in tareas:
            if tarea["status"] == "WAITING_PERMISSION":
                return "WAITING_PERMISSION"
        for tarea in tareas:
            if tarea["status"] == "WAITING_USER":
                return "WAITING_USER"
        for tarea in tareas:
            if tarea["status"] in ("RUNNING", "TESTING", "STARTING_OPENCODE"):
                return "RUNNING"
        for tarea in tareas:
            if tarea["status"] in ("ANALYZING", "PLANNING", "WAITING_PLAN_APPROVAL"):
                return "PLAN"
        return "IDLE"

    # ------------------------------------------------------------------ #
    # Ejecución real
    # ------------------------------------------------------------------ #
    def _lanzar(self, tarea: dict, prompt: str, reanudar: bool) -> None:
        evento = threading.Event()
        with self._lock:
            self._cancelar[tarea["id"]] = evento
            self._vistos[tarea["id"]] = set()
        if tarea.get("finished_at"):
            self._publicar("live_task_start", tarea, task_id=tarea["id"],
                           title=f"OpenCode · {tarea['idea'][:80]}", message=tarea["idea"],
                           description="Desarrollo con OpenCode")
        tarea["finished_at"] = None
        hilo = threading.Thread(target=self._ejecutar, args=(tarea, prompt, reanudar, evento),
                                daemon=True, name=f"jarvis-dev-{tarea['id']}")
        with self._lock:
            self._hilos[tarea["id"]] = hilo
        self._publicar("opencode.starting", tarea,
                       detail=("Continuando la sesión " + (tarea.get("opencode_session_id") or "")
                               if reanudar else "Abriendo sesión de OpenCode"))
        hilo.start()

    def _ejecutar(self, tarea: dict, prompt: str, reanudar: bool, cancelar: threading.Event) -> None:
        task_id = tarea["id"]
        adaptador = self._adaptadores.get(task_id)
        if adaptador is None:
            adaptador = oc.OpenCodeAdapter(
                emit=lambda evento, t=tarea: self._publicar(evento["type"], t, **{
                    k: v for k, v in evento.items() if k not in ("type", "at")}))
            with self._lock:
                self._adaptadores[task_id] = adaptador
        self._set_status(tarea, "STARTING_OPENCODE",
                         "Abriendo sesión de OpenCode…" if not reanudar
                         else "Recuperando la sesión de OpenCode…")
        arranque = (adaptador.attach(tarea["opencode_session_id"], tarea["project_path"])
                    if reanudar and tarea.get("opencode_session_id")
                    else adaptador.start(tarea["project_path"], title=tarea["idea"][:100],
                                         model=tarea.get("opencode_model", ""),
                                         agent=tarea.get("opencode_agent", "")))
        if not arranque.get("ok"):
            if reanudar:
                log.info("no se pudo continuar la sesión %s: %s",
                         tarea.get("opencode_session_id"), arranque.get("error"))
                arranque = adaptador.start(tarea["project_path"],
                                           title=tarea["idea"][:100],
                                           model=tarea.get("opencode_model", ""),
                                           agent=tarea.get("opencode_agent", ""))
                if arranque.get("ok"):
                    tarea["payload"]["revisions"].append(
                        {"at": time.time(), "note": "La sesión anterior ya no existía; "
                                                    "se abrió una nueva con el mismo prompt."})
            if not arranque.get("ok"):
                self._fallo(tarea, arranque.get("error", "No se pudo abrir OpenCode."))
                self._cerrar(tarea)
                return
        tarea["opencode_session_id"] = adaptador.session_id
        tarea["payload"]["opencode"] = {
            "url": arranque.get("url", ""), "source": arranque.get("source", ""),
            "session_id": adaptador.session_id, "workspace": tarea["project_path"]}
        self._guardar(tarea)
        self._set_status(tarea, "RUNNING", "OpenCode está trabajando.")

        respuesta: dict = {}

        def _mandar() -> None:
            respuesta.update(adaptador.send_prompt(prompt))

        hilo_prompt = threading.Thread(target=_mandar, daemon=True, name=f"oc-prompt-{task_id}")
        hilo_prompt.start()
        cancelada = self._vigilar(tarea, adaptador, hilo_prompt, cancelar)
        if cancelada:
            # El señor la detuvo: la tarea ya esta en CANCELLED y explicada.
            # Validar "el resultado" aqui seria tellar algo que no ha pasado.
            hilo_prompt.join(timeout=5)
            self._publicar("opencode.stopped", tarea,
                           detail="Detenida por el usuario. Lo ya escrito se queda.")
            self._cerrar(tarea)
            return
        hilo_prompt.join(timeout=30)
        # Ultima lectura: el turno se cierra con sus ultimas llamadas a
        # herramientas y con la salida de los tests, y llegan justo al final.
        try:
            self._leer_mensajes(tarea, adaptador)
            self._revisar_permisos(tarea, adaptador)
            self._revisar_preguntas(tarea, adaptador)
        except Exception as exc:  # noqa: BLE001
            log.debug("lectura final de %s: %s", tarea["id"], exc)
        self._finalizar(tarea, adaptador, respuesta)
        self._cerrar(tarea)

    def _vigilar(self, tarea: dict, adaptador: oc.OpenCodeAdapter,
                 hilo_prompt: threading.Thread, cancelar: threading.Event) -> bool:
        """Bucle de vigilancia: lee la sesion real y refleja lo que pasa.

        Devuelve True si el señor detuvo la tarea a mitad.
        """
        ultimo_guardado = 0.0
        ultimo_cambios = 0.0
        # Cualquier señal de vida (un mensaje nuevo, un fichero escrito, una
        # pregunta) postpone el reloj de estancamiento. Ver `_SIN_PROGRESO`.
        ultimo_movimiento = time.time()
        p = tarea["payload"]
        while True:
            if cancelar.is_set():
                return True

            # Cada lectura va por separado. Antes estaban todas juntas en un
            # `try` común, y eso rompía el reloj de estancamiento en los dos
            # sentidos: si una fallaba, se saltaba la cuenta del movimiento
            # (una tarea que trabajaba bien acababa dada por muerta) y si no
            # fallaba, la comprobación entera quedaba dentro del `try`
            # (una tarea muerta no se daba por muerta nunca). Las dos cosas se
            # vieron en la prueba de atascamiento.
            try:
                antes = len(self._vistos.get(tarea["id"], ()))
                self._leer_mensajes(tarea, adaptador)
            except Exception as exc:  # noqa: BLE001
                log.debug("mensajes de %s: %s", tarea["id"], exc)
            try:
                self._revisar_permisos(tarea, adaptador)
            except Exception as exc:  # noqa: BLE001
                log.debug("permisos de %s: %s", tarea["id"], exc)
            try:
                self._revisar_preguntas(tarea, adaptador)
            except Exception as exc:  # noqa: BLE001
                log.debug("preguntas de %s: %s", tarea["id"], exc)

            ahora = time.time()
            if len(self._vistos.get(tarea["id"], ())) != antes:
                ultimo_movimiento = ahora

            try:
                if ahora - ultimo_cambios > _CAMBIOS:
                    cambios = adaptador.get_changes()
                    previos = len(p.get("changes") or [])
                    if cambios:
                        ultimo_cambios = ahora
                        if len(cambios) != previos:
                            ultimo_movimiento = ahora
                        p["changes"] = cambios
                        self._publicar("opencode.file_changed", tarea,
                                       detail=", ".join(f"{c['mark']} {c['path']}"
                                                        for c in cambios[:4]))
                if ahora - ultimo_guardado > _GUARDAR:
                    ultimo_guardado = ahora
                    self._guardar(tarea)
            except Exception as exc:  # noqa: BLE001
                log.debug("vigilancia de %s: %s", tarea["id"], exc)

            # El reloj de estancamiento va FUERA de los `try`: tiene que
            # comprobarlo siempre, aunque todo lo de arriba falle.
            if time.time() - ultimo_movimiento > _SIN_PROGRESO:
                p["atascada"] = True
                p["atascada_desde"] = ultimo_movimiento
                self._publicar(
                    "opencode.error", tarea,
                    detail=(f"OpenCode lleva {int(_SIN_PROGRESO // 60)} minutos "
                            "sin hacer ni decir nada. Se da la tarea por parada."))
                log.warning("el turno de %s lleva %.0f s sin movimiento: "
                            "se da por atascado", tarea["id"],
                            time.time() - ultimo_movimiento)
                return False
            if not hilo_prompt.is_alive():
                return False
            time.sleep(_POLL)

    def _leer_mensajes(self, tarea: dict, adaptador: oc.OpenCodeAdapter) -> None:
        """Traduce los mensajes reales de OpenCode a eventos y a estado."""
        with self._lock:
            vistos = self._vistos.setdefault(tarea["id"], set())
        mensajes = adaptador.get_messages(limit=25)
        p = tarea["payload"]
        for mensaje in reversed(mensajes):
            mid = str(mensaje.get("id") or "")
            if not mid or mid in vistos:
                continue
            vistos.add(mid)
            tipo = str(mensaje.get("type") or "")
            if tipo == "assistant":
                p["current_agent"] = str(mensaje.get("agent") or "OpenCode")[:60]
                for parte in mensaje.get("content") or []:
                    if not isinstance(parte, dict):
                        continue
                    if parte.get("type") == "tool":
                        self._nota_herramienta(tarea, parte)
                    elif parte.get("type") == "text" and (parte.get("text") or "").strip():
                        texto = " ".join(parte["text"].split())
                        p["current_action"] = texto[:240]
                        self._publicar("opencode.output", tarea, detail=texto[:240])
            elif tipo == "user" and (mensaje.get("text") or "").strip():
                p["current_action"] = " ".join(mensaje["text"].split())[:240]
            elif tipo == "idle":
                p["last_outcome"] = str(mensaje.get("outcome") or "")
                if mensaje.get("error"):
                    self._publicar("opencode.error", tarea,
                                   detail=str((mensaje.get("error") or {}).get("message")
                                              or mensaje.get("error"))[:240])

    def _nota_herramienta(self, tarea: dict, parte: dict) -> None:
        info = oc.resumir_parte(parte)
        if not info:
            return
        p = tarea["payload"]
        herramientas = p.setdefault("tools_used", [])
        clave = info.get("call") or f"{info['name']}:{info['detail']}"
        previa = next((h for h in herramientas if h.get("key") == clave), None)
        if previa is None:
            herramientas.append({"key": clave, "name": info["name"],
                                 "status": info["status"], "detail": info["detail"],
                                 "at": time.time()})
            if len(herramientas) > 120:
                del herramientas[:-120]
            self._publicar("opencode.tool_call", tarea, tool=info["name"],
                           status=info["status"], detail=info["detail"])
        else:
            previa["status"] = info["status"]
            previa["detail"] = info["detail"]
        p["current_action"] = f"{info['name']}: {info['detail']}"[:240]
        if info["name"] != "bash":
            return
        comando = (info["detail"] or "").lower()
        if oc.es_comando_de_tests(info["detail"]):
            self._registrar_test(tarea, info, clave, parte)
        elif any(c in comando for c in _TEST_CMDS):
            p.setdefault("installed", []).append(info["detail"][:160])

    def _registrar_test(self, tarea: dict, info: dict, clave: str, parte: dict) -> None:
        """Solo entra aquí lo que OpenCode ha ejecutado de verdad."""
        p = tarea["payload"]
        tests = p.setdefault("tests", {"ran": False, "passed": None, "failed": None, "runs": []})
        ya = next((r for r in tests["runs"] if r.get("key") == clave), None)
        if ya is None:
            # La herramienta se ve por primera vez (normalmente "running"; si
            # el turno va rapido, OpenCode ya la devuelve terminada y tambien
            # cuenta: lo que importa es lo que ha ejecutado de verdad).
            ya = {"key": clave, "command": info["detail"][:200], "started_at": time.time()}
            tests["runs"].append(ya)
            tests["ran"] = True
            if len(tests["runs"]) > 20:
                del tests["runs"][:-20]
            if tarea["status"] not in ("WAITING_PERMISSION", "WAITING_USER"):
                self._set_status(tarea, "TESTING", f"Ejecutando tests: {info['detail'][:120]}")
            self._publicar("opencode.test_started", tarea, command=info["detail"][:200],
                           detail=f"Tests: {info['detail'][:160]}")
        if not ya.get("finished") and info["status"] in ("completed", "error"):
            ya["finished"] = True
            estado = (parte or {}).get("state") or {}
            salida = oc.texto_tool(estado)
            ya["output"] = salida[:4000]
            ya["duration"] = round(time.time() - ya.get("started_at", time.time()), 1)
            for campo in ("passed", "failed", "errors", "skipped"):
                valor = oc.contar_tests(salida).get(campo)
                if valor is not None:
                    tests[campo] = valor
            tests["summary"] = oc.resumen_tests(salida)
            tests["last_command"] = info["detail"][:200]
            if tarea["status"] == "TESTING":
                self._set_status(tarea, "RUNNING", "OpenCode sigue trabajando.")
            self._publicar("opencode.test_finished", tarea, command=info["detail"][:200],
                           detail=tests["summary"] or "Tests terminados.",
                           passed=tests.get("passed"), failed=tests.get("failed"),
                           ok=(info["status"] == "completed"))

    def _revisar_permisos(self, tarea: dict, adaptador: oc.OpenCodeAdapter) -> None:
        pendientes = adaptador.pending_permissions()
        p = tarea["payload"]
        ids = {str(x.get("id")) for x in pendientes}
        for permiso in pendientes:
            if any(x.get("id") == permiso["id"] for x in p.get("permissions", [])):
                continue
            permiso["requested_at"] = time.time()
            permiso["status"] = "pending"
            p.setdefault("permissions", []).append(permiso)
            self._set_status(tarea, "WAITING_PERMISSION",
                             f"OpenCode pide permiso: {permiso.get('action')}")
            self._publicar("opencode.permission_requested", tarea,
                           permission_id=permiso["id"], action=permiso.get("action"),
                           detail=permiso.get("detail") or permiso.get("message"))
            inbox.notificar(inbox.mensaje_permiso(tarea, permiso,
                                                   len(pendientes)))
        for guardado in p.get("permissions", []):
            if guardado.get("status") == "pending" and str(guardado.get("id")) not in ids:
                guardado["status"] = "resuelta"
                guardado["resolved_at"] = time.time()
                if guardado.get("requested_at"):
                    guardado["wait_seconds"] = round(
                        guardado["resolved_at"] - guardado["requested_at"], 1)
                self._publicar("opencode.permission_resolved", tarea,
                               permission_id=guardado.get("id"),
                               decision=guardado.get("decision", "resuelta"),
                               detail="Permiso resuelto")
        self._cerrar_esperas(tarea, adaptador)

    def _revisar_preguntas(self, tarea: dict, adaptador: oc.OpenCodeAdapter) -> None:
        pendientes = adaptador.pending_questions()
        p = tarea["payload"]
        for pregunta in pendientes:
            if any(x.get("id") == pregunta["id"] for x in p.get("questions", [])):
                continue
            pregunta["asked_at"] = time.time()
            pregunta["status"] = "pending"
            p.setdefault("questions", []).append(pregunta)
            self._set_status(tarea, "WAITING_USER", "OpenCode necesita una decisión.")
            self._publicar("opencode.question_requested", tarea,
                           question_id=pregunta["id"], question=pregunta.get("question"),
                           detail=pregunta.get("question"))
            inbox.notificar(inbox.mensaje_pregunta(tarea, pregunta, len(pendientes)))
        for guardada in p.get("questions", []):
            if guardada.get("status") == "pending" and \
                    not any(x.get("id") == guardada.get("id") for x in pendientes):
                # Ya no está pendiente en OpenCode: se-answer-ó o se canceló allí.
                guardada["status"] = "respondida"
                guardada["resolved_at"] = time.time()
                if guardada.get("asked_at"):
                    guardada["wait_seconds"] = round(
                        guardada["resolved_at"] - guardada["asked_at"], 1)
        self._cerrar_esperas(tarea, adaptador)

    def _cerrar_esperas(self, tarea: dict, adaptador: oc.OpenCodeAdapter) -> None:
        """Si ya no queda nada pendiente de esta tarea, sigue trabajando."""
        if tarea["status"] not in ("WAITING_PERMISSION", "WAITING_USER"):
            return
        p = tarea["payload"]
        if any(x.get("status") == "pending" for x in p.get("permissions", [])):
            return
        if any(x.get("status") == "pending" for x in p.get("questions", [])):
            return
        espera = time.time() - (p.get("waiting_since") or time.time())
        detalle = (f"Esperó {int(espera // 60)} min {int(espera % 60)} s por tu decisión."
                   if espera >= 5 else "OpenCode sigue trabajando.")
        self._set_status(tarea, "RUNNING", detalle)

    # ------------------------------------------------------------------ #
    # Cierre
    # ------------------------------------------------------------------ #
    def _finalizar(self, tarea: dict, adaptador: oc.OpenCodeAdapter, respuesta: dict) -> None:
        p = tarea["payload"]
        cambios = adaptador.get_changes() or p.get("changes", [])
        if cambios:
            p["changes"] = cambios
        p["opencode"] = {**(p.get("opencode") or {}), **{
            "session_id": adaptador.session_id}}
        texto_final = respuesta.get("reply") or ""
        p["final_message"] = texto_final[:4000]
        comprobaciones = self._validar(tarea, respuesta, cambios)
        fallos = [c for c in comprobaciones if not c["ok"] and c["grave"]]
        avisos = [c for c in comprobaciones if not c["ok"] and not c["grave"]]
        if respuesta.get("ok") is False:
            resultado = "failed"
        elif fallos:
            resultado = "failed"
        elif avisos:
            resultado = "completed_with_warnings"
        else:
            resultado = "completed"

        # "Completada" sin haber tocado un fichero no es completada. Pasa
        # cuando OpenCode acepta el trabajo y no tiene con qué ejecutarlo: en
        # el contenedor se comprobó que no hay NI UN proveedor configurado, así
        # que acepta el encargo, no hace nada y devuelve vacío. Decir
        # "Implementación completada" con cero ficheros es mentarle al señor
        # sobre lo único que ha pedido.
        sin_hacer = (not cambios
                     and not (texto_final or "").strip())
        atascada = bool(p.get("atascada"))
        # OJO: hay que mirar TAMBIÉN "completed_with_warnings". Si hay avisos
        # pero ningún fichero, el resultado es "completada con matices" y la
        # tarea se daba por buena igual. Con avisos de por medio la condición
        # de antes no se cumplía y se escapaba.
        if sin_hacer and resultado in ("completed", "completed_with_warnings"):
            resultado = "failed"
            fallos = list(fallos) + [{
                "nombre": "opencode-trabajo",
                "ok": False,
                "grave": True,
                # La clave se llama `detail` y no `detalle` porque es la que
                # lee `_resumen()`. Con `detalle` salía un `KeyError: 'detail'`
                # al cerrar, el hilo de la tarea moría y la tarea se quedaba
                # en RUNNING para siempre sin decir nada. Se vio porque el
                # vigilante dejó de guardar y el contenedor tenía el
                # `Exception in thread jarvis-dev-…` enterrado en los logs.
                "detail": ("OpenCode no ha tocado ningún fichero ni ha "
                           "contestado nada."),
            }]
            comprobaciones = comprobaciones + fallos[-1:]

        p["result"] = {"status": resultado, "checks": comprobaciones,
                       "errors": [c for c in fallos], "warnings": avisos,
                       "final_message": texto_final[:2000]}
        p["summary"] = self._resumen(tarea, comprobaciones)
        estado = "COMPLETED" if resultado != "failed" else "FAILED"
        if atascada:
            mensaje = (f"OpenCode se quedó parado: {int(_SIN_PROGRESO // 60)} "
                       "minutos sin hacer ni decir nada.")
        elif sin_hacer:
            mensaje = ("OpenCode no ha hecho nada: no tiene ningún modelo "\
                       "configurado. Revise que tenga una clave (OPENAI_API_KEY, "\
                       "ANTHROPIC_API_KEY…) dentro del contenedor.")
        elif estado == "COMPLETED":
            mensaje = "Implementación completada."
        else:
            mensaje = "OpenCode terminó con problemas."
        self._set_status(tarea, estado, mensaje)
        self._publicar("opencode.completed" if estado == "COMPLETED" else "opencode.error",
                       tarea, detail=(p["summary"].get("resumen") or "")[:240],
                       result=resultado)
        self._publicar("live_task_end", tarea, task_id=tarea["id"],
                       title=("OpenCode · completada" if estado == "COMPLETED"
                              else ("OpenCode · sin hacer nada" if sin_hacer
                                    else "OpenCode · con problemas")),
                       failed=estado == "FAILED")

    def _validar(self, tarea: dict, respuesta: dict, cambios: list[dict]) -> list[dict]:
        """Comprobaciones finales (§35). Cada una con su veredicto y su motivo."""
        p = tarea["payload"]
        comprobaciones = []
        comprobaciones.append({
            "name": "OpenCode terminó el turno",
            "ok": bool(respuesta.get("ok")),
            "grave": True,
            "detail": (respuesta.get("error") or respuesta.get("reply") or "")[:300]})
        esperados = set()
        for paso in (p.get("plan") or {}).get("pasos") or []:
            for ruta in paso.get("archivos") or []:
                esperados.add(str(ruta).lower())
        tocados = {str(c["path"]).lower() for c in cambios}
        faltan = sorted(ruta for ruta in esperados
                        if ruta not in tocados and os.path.basename(ruta).lower() not in
                        {os.path.basename(t).lower() for t in tocados})[:6]
        comprobaciones.append({
            "name": "Ficheros previstos en el plan",
            "ok": (not esperados) or not faltan,
            "grave": False,
            "detail": ("Todos los ficheros previstos aparecen en los cambios."
                       if not faltan else
                       "El plan mencionaba: " + ", ".join(faltan))})
        tests = p.get("tests") or {}
        comprobaciones.append({
            "name": "Tests ejecutados",
            "ok": bool(tests.get("ran")),
            "grave": False,
            "detail": self._texto_tests(tests)})
        errores = [h for h in p.get("tools_used", [])
                   if h.get("status") == "error"]
        comprobaciones.append({
            "name": "Errores de herramientas",
            "ok": not errores,
            "grave": False,
            "detail": ("Ninguna herramienta falló." if not errores else
                       f"{len(errores)} llamadas fallaron: " +
                       "; ".join(f"{h['name']}: {h['detail'][:80]}" for h in errores[:3]))})
        scan = (p.get("analysis") or {}).get("scan") or {}
        git = scan.get("git") or {}
        comprobaciones.append({
            "name": "Estado de Git",
            "ok": True,
            "grave": False,
            "detail": (f"Rama {git.get('branch')} con {len(cambios)} ficheros tocados por OpenCode."
                       if git.get("repo") else
                       "La carpeta no está en Git: no hay vuelta atrás automática.")})
        servicios = [c for c in cambios
                     if any(s in str(c["path"]).lower()
                            for s in ("docker", "compose", ".env", "config", "service", "systemd"))]
        comprobaciones.append({
            "name": "Servicios afectados",
            "ok": True,
            "grave": False,
            "detail": ("Ningún fichero de configuración o servicio tocado."
                       if not servicios else
                       "Ha tocado ficheros de servicio: " +
                       ", ".join(c["path"] for c in servicios[:4]) +
                       ". Puede hacer falta reiniciar algo: JARVIS no lo hace sin permiso.")})
        pendientes = [q for q in p.get("questions", []) if q.get("status") == "pending"]
        comprobaciones.append({
            "name": "Preguntas respondidas",
            "ok": not pendientes,
            "grave": False,
            "detail": ("Ninguna pregunta sin responder." if not pendientes else
                       f"{len(pendientes)} preguntas siguen esperando respuesta.")})
        return comprobaciones

    @staticmethod
    def _texto_tests(tests: dict) -> str:
        if not tests or not tests.get("ran"):
            return "No se han ejecutado tests."
        return " ".join((tests.get("summary") or "Tests ejecutados").split())[:200]

    def _resumen(self, tarea: dict, comprobaciones: list[dict]) -> dict:
        p = tarea["payload"]
        # Solo interesan las comprobaciones que son diccionarios. Una que
        # llegue en otra forma no puede hacer revirar el cierre: si esta
        # función peta, el hilo de la tarea muere y el trabajo se queda
        # abierto para siempre sin estado ni error.
        checks = [c for c in (comprobaciones or []) if isinstance(c, dict)]
        t = p.get("timings") or {}
        total = ((tarea.get("finished_at") or time.time()) - (tarea.get("created_at") or time.time()))
        esperando = (t.get("permission", 0.0) or 0.0) + (t.get("question", 0.0) or 0.0)
        ficheros = p.get("changes") or []
        pruebas = p.get("tests") or {}
        resumen = p.get("final_message") or ""
        if not resumen:
            resumen = (p.get("plan") or {}).get("objetivo") or tarea["idea"]
        return {
            "resumen": " ".join(str(resumen).split())[:1200],
            "archivos": [c["path"] for c in ficheros][:80],
            "archivos_conteo": {"nuevos": len([c for c in ficheros if c["action"] == "added"]),
                                "modificados": len([c for c in ficheros if c["action"] == "modified"]),
                                "borrados": len([c for c in ficheros if c["action"] == "deleted"])},
            "tests": {"ejecutados": bool(pruebas.get("ran")),
                      "resumen": (pruebas.get("summary") or
                                  ("No se han ejecutado tests." if not pruebas.get("ran") else ""))},
            "permisos": {"total": len([x for x in p.get("permissions", [])
                                       if x.get("status") == "resuelta"]),
                         "espera": round(t.get("permission", 0.0) or 0.0, 1)},
            "preguntas": {"total": len(p.get("questions", []))},
            "tiempos": {
                "total": round(total, 1),
                "analisis": round(t.get("analysis", 0.0) or 0.0, 1),
                "plan": round(t.get("planning", 0.0) or 0.0, 1),
                "aprobacion": round(t.get("approval", 0.0) or 0.0, 1),
                "ejecucion": round(t.get("execution", 0.0) or 0.0, 1),
                "permisos": round(t.get("permission", 0.0) or 0.0, 1),
                "preguntas": round(t.get("question", 0.0) or 0.0, 1),
                "tests": round(t.get("tests", 0.0) or 0.0, 1),
                "esperando_usuario": round(esperando, 1),
            },
            # `c.get("detail")` y no `c["detail"]`: esta función solo pinta el
            # resumen, y si le falta una clave no debe reventar. Antes sí
            # reventaba, y al reventar en el hilo de la tarea el trabajo
            # hecho se quedaba sin cerrar para siempre. Una comprobación mal
            # formada se ve como un error sin texto; no como una tarea viva
            # para siempre.
            "errores": [str(c.get("detail") or c.get("nombre") or "")
                        for c in checks if not c.get("ok") and c.get("grave")][:6],
            "advertencias": [str(c.get("detail") or c.get("nombre") or "")
                             for c in checks
                             if not c.get("ok") and not c.get("grave")][:6],
        }

    def _fallo(self, tarea: dict, error: str) -> None:
        tarea["payload"]["error"] = (error or "")[:400]
        self._set_status(tarea, "FAILED", tarea["payload"]["error"])
        self._publicar("opencode.error", tarea, detail=tarea["payload"]["error"])
        self._publicar("live_task_end", tarea, task_id=tarea["id"],
                       title="OpenCode · falló", failed=True)

    def _cerrar(self, tarea: dict) -> None:
        task_id = tarea["id"]
        adaptador = self._adaptadores.pop(task_id, None)
        if adaptador is not None:
            try:
                adaptador.stop()
            except Exception:  # noqa: BLE001
                pass
        with self._lock:
            self._cancelar.pop(task_id, None)
            self._hilos.pop(task_id, None)
        self._guardar(tarea)

    # ------------------------------------------------------------------ #
    # Recuperación (§31): qué se puede hacer con una tarea interrumpida
    # ------------------------------------------------------------------ #
    def recuperar(self) -> list[dict]:
        """Al arrancar: las tareas que quedaron a medias se explican, no se relanzan."""
        informe = []
        servidor = service.describe()
        for tarea in store.cargar_todas(60):
            if not store.vivo(tarea["status"]):
                continue
            p = tarea["payload"]
            p["recoverable"] = bool(servidor.get("ok")) and bool(tarea.get("opencode_session_id"))
            p["error"] = ("JARVIS se reinició mientras OpenCode estaba trabajando. "
                          + ("La sesión sigue viva: puedes continuar en ella."
                             if p["recoverable"] else
                             "No hay servidor de OpenCode disponible ahora mismo."))
            self._set_status(tarea, "FAILED", p["error"])
            self._publicar("opencode.error", tarea, detail=p["error"], recoverable=p["recoverable"])
            self._publicar("live_task_end", tarea, task_id=tarea["id"],
                           title="OpenCode · interrumpida", failed=True)
            informe.append({"task_id": tarea["id"], "idea": tarea["idea"],
                            "recoverable": p["recoverable"]})
        with self._lock:
            self._tareas.clear()
        return informe

    # ------------------------------------------------------------------ #
    # Utilidades internas
    # ------------------------------------------------------------------ #
    def _recolocar(self, tarea: dict) -> Optional[oc.OpenCodeAdapter]:
        """Vuelve a enganchar una sesión existente sin crear otra."""
        session_id = tarea.get("opencode_session_id")
        if not session_id:
            return None
        adaptador = oc.OpenCodeAdapter(
            emit=lambda evento, t=tarea: self._publicar(evento["type"], t, **{
                k: v for k, v in evento.items() if k not in ("type", "at")}))
        resultado = adaptador.attach(session_id, tarea.get("project_path", ""))
        if not resultado.get("ok"):
            return None
        with self._lock:
            self._adaptadores[tarea["id"]] = adaptador
        return adaptador

    def _ruta_por_defecto(self, idea: str = "") -> str:
        """Dónde se trabaja si el señor no dice dónde: una carpeta SUYA.

        Antes devolvía `ROOT_DIR`, que es el propio JARVIS. Está comprobado: al
        pedir «una web para llevar los gastos» el plan SALIÓ diciendo que
        escribiera `/app/brain/models.py`, `/app/brain/routes.py` y
        `/app/brain/app.py`. Si el señor aprueba eso, OpenCode le pisa el
        cerebro a JARVIS por una app de gastos. Aquí se cueló por dos sitios:
        el plannerreceive la ruta, y además `tools/opencode_tools` usa su
        propia carpeta (`DATA_DIR/proyectos`), así que los dos caminos
        escribían en sitios distintos.

        Ahora la idea nueva va a `DATA_DIR/proyectos/<nombre>`, y aunque
        alguien pase una ruta dentro del código de JARVIS a propósito, se
        comprueba abajo: solo se permite si la idea habla de tocar JARVIS.
        """
        try:
            from config import DATA_DIR
            base = str(DATA_DIR) if str(DATA_DIR) else os.getcwd()
        except Exception:  # noqa: BLE001
            base = os.getcwd()
        nombre = _nombre_de_proyecto(idea) or "proyecto"
        destino = os.path.join(base, "proyectos", nombre)
        try:
            os.makedirs(destino, exist_ok=True)
        except OSError:
            pass
        return destino

    # ------------------------------------------------------------------ #
    # Vistas para el panel
    # ------------------------------------------------------------------ #
    def resumen(self, tarea: dict) -> dict:
        p = tarea.get("payload") or {}
        vivo = store.vivo(tarea.get("status", "DRAFT"))
        ahora = time.time()
        inicio = tarea.get("started_at")
        creado = tarea.get("created_at")
        transcurrido = ((tarea.get("finished_at") or ahora) - (creado or ahora)) if creado else 0.0
        estimacion = p.get("estimate") or {}
        restante = None
        nota_estimacion = ""
        if vivo and estimacion.get("max") and inicio:
            restante = max(0, int(estimacion["max"] - (ahora - inicio)))
            nota_estimacion = (f"Estimación actualizada (aproximada): ~{restante} min restantes"
                               if restante else "La estimación inicial se ha superado.")
        return {
            "id": tarea["id"],
            "idea": tarea.get("idea", ""),
            "status": tarea.get("status", "DRAFT"),
            "project_path": tarea.get("project_path", ""),
            "session_id": tarea.get("opencode_session_id", ""),
            "model": tarea.get("opencode_model", ""),
            "agent": p.get("current_agent", ""),
            "created_at": creado,
            "started_at": inicio,
            "finished_at": tarea.get("finished_at"),
            "updated_at": tarea.get("updated_at"),
            "elapsed": round(max(0.0, transcurrido), 1),
            "current_step": p.get("current_step"),
            "current_action": p.get("current_action", ""),
            "steps_total": len((p.get("plan") or {}).get("pasos") or []),
            "risks": len(p.get("risks") or []),
            "estimate": estimacion,
            "estimate_remaining": restante,
            "estimate_note": nota_estimacion,
            "progress": 100 if tarea.get("status") == "COMPLETED" else (0 if not vivo else None),
            "permissions_pending": len([x for x in p.get("permissions", [])
                                        if x.get("status") == "pending"]),
            "questions_pending": len([x for x in p.get("questions", [])
                                     if x.get("status") == "pending"]),
            "changes": len(p.get("changes") or []),
            "tests_ran": bool((p.get("tests") or {}).get("ran")),
            "error": p.get("error", ""),
            "recoverable": bool(p.get("recoverable")),
            "state": self._estado_de(tarea),
        }

    @staticmethod
    def _estado_de(tarea: dict) -> str:
        """Estado de OpenCode tal y como lo ve el usuario (§29)."""
        estado = tarea.get("status", "DRAFT")
        if estado in ("RUNNING", "TESTING", "STARTING_OPENCODE"):
            return "RUNNING"
        if estado == "WAITING_PERMISSION":
            return "WAITING_PERMISSION"
        if estado == "WAITING_USER":
            return "WAITING_USER"
        if estado in ("COMPLETED",):
            return "COMPLETED"
        if estado in ("FAILED", "CANCELLED"):
            return "ERROR"
        if estado == "WAITING_PLAN_APPROVAL":
            return "WAITING_PLAN_APPROVAL"
        if estado in ("ANALYZING", "PLANNING"):
            return "PREPARING"
        return "DRAFT"

    def detalle(self, tarea: dict) -> dict:
        p = tarea.get("payload") or {}
        base = self.resumen(tarea)
        base.update({
            "analysis": {"source": (p.get("analysis") or {}).get("source", ""),
                         "model_error": (p.get("analysis") or {}).get("model_error", ""),
                         "facts": (p.get("analysis") or {}).get("facts", ""),
                         "scan": (p.get("analysis") or {}).get("scan", {})},
            "plan": p.get("plan") or {},
            "risks": p.get("risks") or [],
            "prompt": p.get("prompt", ""),
            "prompt_chars": len(p.get("prompt", "")),
            "events": (p.get("events") or [])[-120:],
            "permissions": p.get("permissions") or [],
            "questions": p.get("questions") or [],
            "answers": p.get("answers") or [],
            "changes": p.get("changes") or [],
            "tools": (p.get("tools_used") or [])[-40:],
            "tests": p.get("tests") or {},
            "result": p.get("result") or {},
            "summary": p.get("summary") or {},
            "timings": p.get("timings") or {},
            "notes": p.get("notes") or [],
            "revisions": p.get("revisions") or [],
            "opencode": p.get("opencode") or {},
            "final_message": p.get("final_message", ""),
            "attempts": p.get("attempts", 0),
        })
        return base

    def tasks_snapshot(self) -> dict:
        return self.estado()


_manager: Optional[TaskManager] = None


def get_manager(emit=None) -> TaskManager:
    global _manager
    if _manager is None:
        _manager = TaskManager(emit)
    elif emit is not None:
        _manager.set_emit(emit)
    return _manager