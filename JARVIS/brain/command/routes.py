"""API del centro de mando: /api/cmd/*

Todo lo que JARVIS Live muestra y todo lo que controla sale de aquí. No hay
endpoints de adorno: cada ruta llama al sistema real que dice controlar, y las
acciones peligrosas pasan por el RiskEngine que JARVIS ya tenía.

    GET  /api/cmd/estado                     una llamada, todo el centro de mando
    GET  /api/cmd/capacidades                matriz de control real
    GET  /api/cmd/salud                      health center
    GET  /api/cmd/recursos                   resource center
    GET  /api/cmd/agentes                    agentes detectados + acciones
    POST /api/cmd/agentes/{id}/start         abrir el programa del agente
    GET  /api/cmd/notificaciones             bandeja real
    POST /api/cmd/notificaciones/{id}        read | dismiss | mute
    GET  /api/cmd/incidentes                 incidentes reales
    POST /api/cmd/incidentes/{id}            acknowledge | resolve
    GET  /api/cmd/auditoria                  auditoría con filtros
    GET  /api/cmd/historial                  recap de los últimos días
    GET  /api/cmd/memoria                    memoria de JARVIS
    GET  /api/cmd/conocimiento               estado de la base de documentos
    GET  /api/cmd/conocimiento/buscar?q=     búsqueda real (Qdrant)
    POST /api/cmd/conocimiento/indexar       reindexar
    POST /api/cmd/conocimiento/olvidar       borrar la colección (permiso)
    GET  /api/cmd/automatizaciones           planificador + protocolos
    POST /api/cmd/automatizaciones           crear
    POST /api/cmd/automatizaciones/{id}/...  pausar|reanudar|correr|borrar|editar
    GET/POST/DELETE /api/cmd/protocolos      automatizaciones por disparador
    GET  /api/cmd/decisiones                 registro de decisiones
    GET  /api/cmd/pruebas                    comandos de test detectados
    POST /api/cmd/pruebas                    ejecutar tests (permiso)
    GET  /api/cmd/pruebas/{job_id}           salida real del proceso
    GET  /api/cmd/cambios                    estado Git real
    GET  /api/cmd/cambios/diff               diff real
    POST /api/cmd/cambios/commit             commit (permiso)
    GET/POST/DELETE /api/cmd/checkpoints     puntos de retorno
    POST /api/cmd/checkpoints/{id}/restaurar restaurar (permiso)
    POST /api/cmd/orden                      hablar con JARVIS por texto
    POST /api/cmd/briefing                   resumen de lo ausente
    POST /api/cmd/presentacion               modo presentación
    POST /api/cmd/diagnostico                diagnóstico real del sistema
    POST /api/cmd/sistema/optimizar          optimización real (permiso)
    POST /api/cmd/sistema/disco              limpieza de disco (permiso)
    POST /api/cmd/opencode/servidor          reiniciar el servidor de OpenCode (permiso)
"""
from __future__ import annotations

import asyncio
import logging
import re
# `Optional` se usa en las anotaciones de los modelos de Pydantic de este
# fichero. Con `from __future__ import annotations` las anotaciones llegan como
# texto y Pydantic las resuelve contra el namespace del módulo: si el nombre no
# está importado, el modelo no se puede construir y el endpoint devuelve un
# 500 al señor. No estaba importado, y PUT /memoria/recuerdos/{id} estaba roto.
from typing import Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from auth import coincide, origen_permitido

from . import activity, ai, briefing, centers, notify, runner, services, store

log = logging.getLogger("brain.cmd.routes")
router = APIRouter(prefix="/api/cmd")

_ID = re.compile(r"^[A-Za-z0-9_.:-]{1,120}$")


def _authorized(request: Request) -> bool:
    authorization = request.headers.get("authorization", "")
    candidato = authorization[7:].strip() if authorization.lower().startswith("bearer ") else ""
    candidato = candidato or request.headers.get("x-jarvis-token")
    return origen_permitido(request.headers.get("origin")) and coincide(candidato)


def _no_autorizado() -> JSONResponse:
    return JSONResponse({"ok": False, "error": "Sesión no autorizada. Recarga JARVIS Live."},
                        status_code=401, headers={"Cache-Control": "no-store"})


def _error(exc: Exception) -> JSONResponse:
    log.exception("fallo en el centro de mando")
    return JSONResponse({"ok": False, "error": str(exc)[:220]}, status_code=500)


async def _permiso(nombre: str, texto: str, riesgo: str = "medium") -> bool:
    """Pasa la acción por el RiskEngine de siempre. False = el señor lo negó."""
    try:
        from main import brain
        engine = getattr(brain, "risk", None)
        if engine is None:
            return True
        return bool(await engine.check({"name": nombre, "risk_level": riesgo,
                                        "requires_confirmation": True,
                                        "confirm_text": texto}, {}))
    except Exception as exc:  # noqa: BLE001 - si el motor falla, no se ejecuta
        log.warning("RiskEngine no disponible para %s: %s", nombre, exc)
        return False


def _id_valido(valor: str) -> bool:
    return bool(_ID.match(str(valor or "")))


class Texto(BaseModel):
    texto: str = Field(default="", max_length=2000)


class NuevaAutomatizacion(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    mensaje: str = Field(min_length=1, max_length=1000)
    cuando: str = Field(min_length=1, max_length=60)


class EditarAutomatizacion(BaseModel):
    nombre: str = Field(default="", max_length=80)
    mensaje: str = Field(default="", max_length=1000)
    cuando: str = Field(default="", max_length=60)


class NuevoProtocolo(BaseModel):
    nombre: str = Field(min_length=1, max_length=80)
    triggers: list = Field(default_factory=list)
    actions: list = Field(default_factory=list)
    say: str = Field(default="", max_length=400)


class Decision(BaseModel):
    chosen: str = Field(min_length=1, max_length=400)
    question: str = Field(default="", max_length=400)
    reason: str = Field(default="", max_length=600)
    alternatives: list = Field(default_factory=list)
    mission_id: str = Field(default="", max_length=64)


class CorrerTests(BaseModel):
    ruta: str = Field(default="", max_length=400)
    comando: str = Field(default="", max_length=200)
    timeout: int = Field(default=600, ge=30, le=7200)


class Commit(BaseModel):
    ruta: str = Field(default="", max_length=400)
    mensaje: str = Field(default="", max_length=400)


class Checkpoint(BaseModel):
    ruta: str = Field(default="", max_length=400)
    mission_id: str = Field(default="", max_length=64)
    nota: str = Field(default="", max_length=200)


class Accion(BaseModel):
    accion: str = Field(default="", max_length=40)
    resolucion: str = Field(default="", max_length=400)


class Briefing(BaseModel):
    minutos: int = Field(default=0, ge=0, le=60 * 24 * 30)
    hablar: bool = False


class ConfiguracionIA(BaseModel):
    FAST: str = Field(default="", max_length=160)
    GENERAL: str = Field(default="", max_length=160)
    REASONING: str = Field(default="", max_length=160)
    VISION: str = Field(default="", max_length=160)
    fallback: bool | None = None
    groq: bool | None = None
    gemini: bool | None = None
    openrouter: bool | None = None


class ProbarIA(BaseModel):
    provider: str = Field(default="groq", max_length=24)


class Presentacion(BaseModel):
    activo: bool = True


# --------------------------------------------------------------------------- #
# Estado general
# --------------------------------------------------------------------------- #
@router.get("/estado")
async def cmd_estado(request: Request, forzar: int = 0):
    if not _authorized(request):
        return _no_autorizado()
    try:
        return JSONResponse(await asyncio.to_thread(_estado_completo, bool(forzar)),
                            headers={"Cache-Control": "no-store"})
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


def _estado_completo(forzar: bool = False) -> dict:
    """Una sola respuesta para pintar el centro de mando entero."""
    salud = centers.salud(forzar)
    recursos = centers.recursos(forzar)
    notificaciones = store.contar_notificaciones()
    incidentes = store.contar_incidentes()
    try:
        from orchestrator.manager import get_manager
        gestor = get_manager()
        misiones = gestor.listar(8)
        pendientes = {"permisos": len(gestor.permisos_pendientes()),
                      "preguntas": len(gestor.preguntas_pendientes())}
        estado_opencode = gestor.estado()
    except Exception as exc:  # noqa: BLE001
        log.debug("orquestador no disponible: %s", exc)
        misiones, pendientes = [], {"permisos": 0, "preguntas": 0}
        estado_opencode = {"state": "UNKNOWN", "opencode": {}, "message": str(exc)[:120]}
    try:
        from main import brain
        riesgo = getattr(brain, "risk", None)
        riesgo_pendiente = len(riesgo._pending) if riesgo is not None else 0
    except Exception:  # noqa: BLE001
        riesgo_pendiente = 0
    notificaciones_pendientes = notificaciones.get("sin_leer", 0)
    ausentes = briefing.ausente(0)
    try:
        conocimiento = services.conocimiento_estado()
    except Exception as exc:  # noqa: BLE001
        conocimiento = {"disponible": False, "error": str(exc)[:120]}
    return {
        "ok": True,
        "ts": __import__("time").time(),
        "salud": salud,
        "recursos": recursos,
        "contadores": {
            "notificaciones": notificaciones.get("total", 0),
            "notificaciones_sin_leer": notificaciones_pendientes,
            "incidentes": incidentes.get("abiertos", 0),
            "incidentes_graves": incidentes.get("graves", 0),
            "permisos": pendientes["permisos"] + riesgo_pendiente,
            "preguntas": pendientes["preguntas"],
            "misiones_vivas": sum(1 for m in misiones if m.get("status") in
                                  ("RUNNING", "TESTING", "WAITING_PERMISSION",
                                   "WAITING_USER", "STARTING_OPENCODE")),
        },
        "misiones": misiones,
        "opencode": {"estado": estado_opencode.get("state", "IDLE"),
                     "servidor": estado_opencode.get("opencode", {}),
                     "mensaje": estado_opencode.get("message", "")},
        "automatizaciones": services.automatizaciones().get("total", 0),
        "conocimiento": conocimiento,
        "cambios": runner.cambios_git() if runner.es_repo(".") else {"repo": False},
        "ausente": {"texto": ausentes["texto"], "minutos": ausentes["ventana"]["minutos"],
                    "completadas": ausentes["completadas"],
                    "fallidas": ausentes["fallidas"],
                    "pendientes": ausentes["pendientes"]},
        "notificaciones": store.listar_notificaciones(6),
        "incidentes": store.listar_incidentes(5, solo_abiertos=True),
        "capacidades": centers.matriz()["capacidades"],
        "presentacion": briefing.hay_presentacion(),
    }


# --------------------------------------------------------------------------- #
# Salud, recursos, agentes
# --------------------------------------------------------------------------- #
@router.get("/actividad")
async def cmd_actividad(request: Request):
    """La tarea protagonista de la pantalla, con sus fases y su progreso real."""
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(activity.actividad),
                        headers={"Cache-Control": "no-store"})


@router.get("/ia")
async def cmd_ia(request: Request, minutos: int = 1440):
    """Estado de los modelos: proveedores, catalogo, uso real y seleccion."""
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(ai.estado),
                        headers={"Cache-Control": "no-store"})


@router.get("/ia/modelos")
async def cmd_ia_modelos(request: Request, consultar: int = 0):
    """Catalogo de modelos. `consultar=1` pregunta al proveedor de verdad."""
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(ai.modelos, bool(consultar)))


@router.post("/ia/configurar")
async def cmd_ia_configurar(request: Request, cuerpo: ConfiguracionIA):
    """Elige modelo por tarea y enciende/apaga proveedores."""
    if not _authorized(request):
        return _no_autorizado()
    datos = cuerpo.model_dump(exclude_none=True)
    return JSONResponse(await asyncio.to_thread(ai.configurar, datos))


@router.post("/ia/probar")
async def cmd_ia_probar(request: Request, cuerpo: ProbarIA):
    """Prueba de conexion real (una llamada de 1 token). Solo Groq."""
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(ai.probar, cuerpo.provider or "groq"))


@router.get("/ia/peticiones")
async def cmd_ia_peticiones(request: Request, minutos: int = 1440,
                            limite: int = 40):
    """Las ultimas peticiones a modelos, con tokens y latencia reales."""
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(ai.peticiones, minutos, limite))


@router.get("/preguntas")
async def cmd_preguntas(request: Request):
    """Lo que OpenCode necesita decidir para seguir, tal y como lo tiene guardado."""
    if not _authorized(request):
        return _no_autorizado()

    def _leer() -> list[dict]:
        try:
            from orchestrator.manager import get_manager
            return get_manager().preguntas_pendientes()
        except Exception as exc:  # noqa: BLE001
            log.debug("preguntas del orquestador: %s", exc)
            return []

    preguntas = await asyncio.to_thread(_leer)
    return JSONResponse({"ok": True, "preguntas": preguntas, "total": len(preguntas)},
                        headers={"Cache-Control": "no-store"})


@router.get("/permisos")
async def cmd_permisos(request: Request):
    """Pendientes, concedidos y denegados, de JARVIS y de OpenCode."""
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(activity.permisos))


@router.get("/salud")
async def cmd_salud(request: Request, forzar: int = 0):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(centers.salud, bool(forzar)),
                        headers={"Cache-Control": "no-store"})


@router.get("/recursos")
async def cmd_recursos(request: Request, forzar: int = 0):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(centers.recursos, bool(forzar)))


# --------------------------------------------------------------------------- #
# Resource Manager: qué ha decidido y por qué (no solo cuánto mide)
# --------------------------------------------------------------------------- #
@router.get("/recursos/decisiones")
async def cmd_recursos_decisiones(request: Request, limite: int = 20):
    if not _authorized(request):
        return _no_autorizado()
    import resource_manager

    return JSONResponse(await asyncio.to_thread(resource_manager.estado))


@router.post("/recursos/revisar")
async def cmd_recursos_revisar(request: Request):
    """Fuerza una medición y devuelve la decisión que se ha tomado."""
    if not _authorized(request):
        return _no_autorizado()
    import resource_manager

    return JSONResponse(await asyncio.to_thread(resource_manager.revisar, True))


# --------------------------------------------------------------------------- #
# Agent Manager: inventario, cola y control
# --------------------------------------------------------------------------- #
@router.get("/agentes/estado")
async def cmd_agentes_estado(request: Request):
    if not _authorized(request):
        return _no_autorizado()
    import agent_manager

    return JSONResponse(await asyncio.to_thread(agent_manager.estado))


@router.get("/agentes/cola")
async def cmd_agentes_cola(request: Request, limite: int = 20):
    if not _authorized(request):
        return _no_autorizado()
    import agent_manager

    return JSONResponse({"ok": True,
                         "cola": await asyncio.to_thread(agent_manager.cola, limite),
                         "resumen": await asyncio.to_thread(agent_manager.resumen_cola),
                         "historial": await asyncio.to_thread(
                             agent_manager.historial, 10)})


class TrabajoAgent(BaseModel):
    agent_id: str
    payload: dict = Field(default_factory=dict)
    prioridad: int = 50
    mission_id: str = ""
    task_id: str = ""


@router.post("/agentes/trabajo")
async def cmd_agentes_trabajo(request: Request, cuerpo: TrabajoAgent):
    """Encola un trabajo para un agente."""
    if not _authorized(request):
        return _no_autorizado()
    import agent_manager

    if not await _permiso("encolar_trabajo",
                          f"¿Encolar un trabajo para «{cuerpo.agent_id}»?"):
        return JSONResponse({"ok": False, "error": "Denegado por el usuario."},
                            status_code=409)
    return JSONResponse(await asyncio.to_thread(
        agent_manager.encolar, cuerpo.agent_id, cuerpo.payload,
        prioridad=cuerpo.prioridad, mission_id=cuerpo.mission_id,
        task_id=cuerpo.task_id))


@router.post("/agentes/trabajo/{job_id}/siguiente")
async def cmd_agentes_siguiente(request: Request, job_id: str):
    """Saca el siguiente trabajo de la cola, si el Agent Manager dice que cabe."""
    if not _authorized(request):
        return _no_autorizado()
    import agent_manager

    trabajo = await asyncio.to_thread(agent_manager.siguiente, job_id)
    return JSONResponse({"ok": True, "trabajo": trabajo})


@router.post("/agentes/trabajo/{job_id}/heartbeat")
async def cmd_agentes_heartbeat(request: Request, job_id: str):
    if not _authorized(request):
        return _no_autorizado()
    import agent_manager

    return JSONResponse(await asyncio.to_thread(agent_manager.heartbeat, job_id))


class ResultadoTrabajo(BaseModel):
    ok: bool = True
    resultado: str = ""
    error: str = ""


@router.post("/agentes/trabajo/{job_id}/terminar")
async def cmd_agentes_terminar(request: Request, job_id: str, cuerpo: ResultadoTrabajo):
    if not _authorized(request):
        return _no_autorizado()
    import agent_manager

    return JSONResponse(await asyncio.to_thread(
        agent_manager.terminar, job_id, cuerpo.ok, cuerpo.resultado, cuerpo.error))


@router.post("/agentes/trabajo/{job_id}/cancelar")
async def cmd_agentes_cancelar(request: Request, job_id: str, cuerpo: dict = None):
    if not _authorized(request):
        return _no_autorizado()
    import agent_manager

    if not await _permiso("cancelar_trabajo", f"¿Cancelar el trabajo {job_id}?"):
        return JSONResponse({"ok": False, "error": "Denegado por el usuario."},
                            status_code=409)
    return JSONResponse(await asyncio.to_thread(
        agent_manager.cancelar, job_id, (cuerpo or {}).get("motivo", "")))


@router.post("/agentes/atascados")
async def cmd_agentes_atascados(request: Request):
    """Marca los que llevan demasiado sin avanzar. No los cancela."""
    if not _authorized(request):
        return _no_autorizado()
    import agent_manager

    return JSONResponse({"ok": True,
                         "atascados": await asyncio.to_thread(
                             agent_manager.detectar_atascados)})


@router.post("/agentes/recuperar")
async def cmd_agentes_recuperar(request: Request):
    """Qué se quedó a medias. Consulta; no relanza nada."""
    if not _authorized(request):
        return _no_autorizado()
    import agent_manager

    return JSONResponse(await asyncio.to_thread(agent_manager.recuperar_al_arrancar))


# --------------------------------------------------------------------------- #
# Recuerdos (memoria estructurada)
# --------------------------------------------------------------------------- #
@router.get("/memoria/recuerdos")
async def cmd_recuerdos_listar(request: Request, estado: str = "activo",
                               tipo: str = "", categoria: str = "",
                               proyecto: str = "", mission_id: str = "",
                               decision_id: str = "", limite: int = 50,
                               orden: str = "relevancia"):
    if not _authorized(request):
        return _no_autorizado()
    from memory import recuerdos as _rec

    filas = await asyncio.to_thread(_rec.listar, estado=estado, tipo=tipo,
                                   categoria=categoria, proyecto=proyecto,
                                   mission_id=mission_id, decision_id=decision_id,
                                   limite=limite, orden=orden)
    return JSONResponse({"ok": True, "recuerdos": filas, "total": len(filas)})


class RecuerdoNuevo(BaseModel):
    contenido: str
    tipo: str = "hecho"
    categoria: str = ""
    origen: str = "chat"
    procedencia: str = ""
    proyecto: str = ""
    mission_id: str = ""
    decision_id: str = ""
    metadata: dict = Field(default_factory=dict)
    relevancia: float = 0.5
    forzar: bool = False


@router.post("/memoria/recuerdos")
async def cmd_recuerdos_crear(request: Request, cuerpo: RecuerdoNuevo):
    if not _authorized(request):
        return _no_autorizado()
    from memory import recuerdos as _rec

    r = await asyncio.to_thread(
        _rec.recordar, cuerpo.contenido, cuerpo.tipo,
        categoria=cuerpo.categoria, origen=cuerpo.origen,
        procedencia=cuerpo.procedencia, proyecto=cuerpo.proyecto,
        mission_id=cuerpo.mission_id, decision_id=cuerpo.decision_id,
        metadata=cuerpo.metadata, relevancia=cuerpo.relevancia,
        forzar=cuerpo.forzar)
    return JSONResponse(r)


@router.get("/memoria/recuerdos/buscar")
async def cmd_recuerdos_buscar(request: Request, q: str = "",
                               proyecto: str = "", mission_id: str = "",
                               topes: int = 5):
    """Recuperación por relevancia, y cada resultado explica por qué salió."""
    if not _authorized(request):
        return _no_autorizado()
    from memory import recuerdos as _rec

    hits = await asyncio.to_thread(_rec.recuperar, q, proyecto=proyecto,
                                  mission_id=mission_id, topes=topes)
    return JSONResponse({"ok": True, "recuerdos": hits, "total": len(hits),
                         "consulta": q})


@router.get("/memoria/recuerdos/{rec_id}")
async def cmd_recuerdos_uno(request: Request, rec_id: str):
    if not _authorized(request):
        return _no_autorizado()
    from memory import recuerdos as _rec

    d = await asyncio.to_thread(_rec.obtener, rec_id)
    if d is None:
        return JSONResponse({"ok": False, "error": "Ese recuerdo no existe."},
                            status_code=404)
    return JSONResponse({"ok": True, "recuerdo": d})


class RecuerdoCambio(BaseModel):
    contenido: Optional[str] = None
    tipo: Optional[str] = None
    categoria: Optional[str] = None
    proyecto: Optional[str] = None
    mission_id: Optional[str] = None
    relevance_note: str = ""
    relevancia: Optional[float] = None
    estado: Optional[str] = None
    metadata: Optional[dict] = None


@router.put("/memoria/recuerdos/{rec_id}")
async def cmd_recuerdos_actualizar(request: Request, rec_id: str,
                                   cuerpo: RecuerdoCambio):
    if not _authorized(request):
        return _no_autorizado()
    from memory import recuerdos as _rec

    cambios = {k: v for k, v in cuerpo.dict().items()
               if v is not None and k != "relevance_note"}
    return JSONResponse(await asyncio.to_thread(_rec.actualizar, rec_id, **cambios))


@router.post("/memoria/recuerdos/{rec_id}/archivar")
async def cmd_recuerdos_archivar(request: Request, rec_id: str):
    if not _authorized(request):
        return _no_autorizado()
    from memory import recuerdos as _rec

    return JSONResponse(await asyncio.to_thread(_rec.archivar, rec_id))


@router.post("/memoria/recuerdos/{rec_id}/olvidar")
async def cmd_recuerdos_olvidar(request: Request, rec_id: str):
    if not _authorized(request):
        return _no_autorizado()
    from memory import recuerdos as _rec

    return JSONResponse(await asyncio.to_thread(_rec.olvidar, rec_id))


@router.get("/memoria/recuerdos-resumen")
async def cmd_recuerdos_resumen(request: Request):
    if not _authorized(request):
        return _no_autorizado()
    from memory import recuerdos as _rec

    return JSONResponse(await asyncio.to_thread(_rec.resumen))


# --------------------------------------------------------------------------- #
# Conocimiento por categorías
# --------------------------------------------------------------------------- #
@router.get("/conocimiento/documentos")
async def cmd_conocimiento_docs(request: Request, categoria: str = "",
                                proyecto: str = "", estado: str = "",
                                limite: int = 100):
    if not _authorized(request):
        return _no_autorizado()
    import library as lib

    docs = await asyncio.to_thread(lib.listar_documentos, categoria, proyecto,
                                   estado, limite)
    return JSONResponse({"ok": True, "documentos": docs, "total": len(docs),
                         "categorias": lib.CATEGORIAS})


@router.get("/conocimiento/categoria/{categoria}")
async def cmd_conocimiento_categoria(request: Request, categoria: str, limite: int = 5):
    if not _authorized(request):
        return _no_autorizado()
    import library as lib

    return JSONResponse({"ok": True,
                         "documentos": await asyncio.to_thread(
                             lib.buscar_por_categoria, categoria, limite)})


class Reclasificar(BaseModel):
    ruta: str
    categoria: str


@router.post("/conocimiento/reclasificar")
async def cmd_conocimiento_reclasificar(request: Request, cuerpo: Reclasificar):
    if not _authorized(request):
        return _no_autorizado()
    import library as lib

    return JSONResponse(await asyncio.to_thread(lib.reasignar_categoria,
                                                cuerpo.ruta, cuerpo.categoria))


@router.post("/conocimiento/indexar-documentos")
async def cmd_conocimiento_indexar_docs(request: Request):
    """Registra lo ya indexado con su categoría, sin reindexar el contenido."""
    if not _authorized(request):
        return _no_autorizado()
    import library as lib

    def _registrar():
        m = lib._load_manifest()
        count = 0
        for ruta in (m.get("files") or {}):
            lib.registrar_documento(ruta)
            count += 1
        return count

    total = await asyncio.to_thread(_registrar)
    return JSONResponse({"ok": True, "documentos": total,
                         "categorias": lib.categorias_stats()})


# --------------------------------------------------------------------------- #
# Autorizaciones recordadas
# --------------------------------------------------------------------------- #
@router.get("/autorizaciones")
async def cmd_autorizaciones(request: Request):
    if not _authorized(request):
        return _no_autorizado()
    import authz

    return JSONResponse(await asyncio.to_thread(authz.estado))


@router.get("/autorizaciones/activas")
async def cmd_autorizaciones_activas(request: Request, session_id: str = ""):
    if not _authorized(request):
        return _no_autorizado()
    import authz

    return JSONResponse({"ok": True,
                         "autorizaciones": await asyncio.to_thread(
                             authz.activas, session_id)})


class AutorizacionNueva(BaseModel):
    alcance: str
    decision: str = "session"
    tipo_operacion: str = ""
    recursos: list = Field(default_factory=list)
    restricciones: dict = Field(default_factory=dict)
    segundos: int = 0
    actor: str = ""
    caducidad_unix: float = 0.0


@router.post("/autorizaciones")
async def cmd_autorizaciones_conceder(request: Request, cuerpo: AutorizacionNueva):
    """Concede una autorización. `once` no deja rastro, por diseño."""
    if not _authorized(request):
        return _no_autorizado()
    import authz

    r = await asyncio.to_thread(
        authz.concede, cuerpo.alcance, cuerpo.decision,
        tipo_operacion=cuerpo.tipo_operacion, recursos=cuerpo.recursos,
        restricciones=cuerpo.restricciones, segundos=cuerpo.segundos,
        session_id=cuerpo.actor, actor=cuerpo.actor,
        caducidad_unix=cuerpo.caducidad_unix)
    return JSONResponse(r, status_code=200 if r.get("ok") else 400)


class Revocar(BaseModel):
    alcance: str = ""
    authz_id: str = ""
    actor: str = ""


@router.post("/autorizaciones/revocar")
async def cmd_autorizaciones_revocar(request: Request, cuerpo: Revocar):
    if not _authorized(request):
        return _no_autorizado()
    import authz

    r = await asyncio.to_thread(authz.revoca, cuerpo.alcance, cuerpo.authz_id,
                               cuerpo.actor)
    return JSONResponse(r)


@router.get("/capacidades")
async def cmd_capacidades(request: Request):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(centers.matriz())


@router.get("/agentes")
async def cmd_agentes(request: Request):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(services.agentes))


@router.post("/agentes/{agente_id}/start")
async def cmd_agente_start(request: Request, agente_id: str):
    if not _authorized(request):
        return _no_autorizado()
    if not _id_valido(agente_id):
        return JSONResponse({"ok": False, "error": "Agente desconocido."}, status_code=400)
    if not await _permiso("start_agent", f"¿Abrir el programa del agente «{agente_id}»?"):
        return JSONResponse({"ok": False, "error": "Denegado por el usuario."}, status_code=409)
    return JSONResponse(await asyncio.to_thread(services.agente_iniciar, agente_id))


@router.post("/agentes/{agente_id}/stop")
async def cmd_agente_stop(request: Request, agente_id: str):
    """Cierra el programa del agente.

    Pide permiso como el arranque: cerrar el editor del señor con el trabajo a
    medio es de las cosas que no se hacen sin preguntar.
    """
    if not _authorized(request):
        return _no_autorizado()
    if not _id_valido(agente_id):
        return JSONResponse({"ok": False, "error": "Agente desconocido."}, status_code=400)
    if not await _permiso("stop_agent", f"¿Cerrar el programa del agente «{agente_id}»?"):
        return JSONResponse({"ok": False, "error": "Denegado por el usuario."}, status_code=409)
    return JSONResponse(await asyncio.to_thread(services.agente_parar, agente_id))


# --------------------------------------------------------------------------- #
# Notificaciones e incidentes
# --------------------------------------------------------------------------- #
@router.get("/notificaciones")
async def cmd_notificaciones(request: Request, limite: int = 40, todas: int = 0):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse({"ok": True,
                         "notificaciones": store.listar_notificaciones(limite, bool(todas)),
                         "contador": store.contar_notificaciones()})


@router.post("/notificaciones/{notif_id}")
async def cmd_notificacion(request: Request, notif_id: str, cuerpo: Accion):
    if not _authorized(request):
        return _no_autorizado()
    if not _id_valido(notif_id):
        return JSONResponse({"ok": False, "error": "Notificación desconocida."}, status_code=400)
    ok = store.marcar_notificacion(notif_id, cuerpo.accion)
    return JSONResponse({"ok": ok, "contador": store.contar_notificaciones()},
                        status_code=200 if ok else 404)


@router.get("/incidentes")
async def cmd_incidentes(request: Request, limite: int = 30, abiertos: int = 0):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse({"ok": True,
                         "incidentes": store.listar_incidentes(limite, bool(abiertos)),
                         "contador": store.contar_incidentes()})


@router.post("/incidentes/{inc_id}")
async def cmd_incidente(request: Request, inc_id: str, cuerpo: Accion):
    if not _authorized(request):
        return _no_autorizado()
    if not _id_valido(inc_id):
        return JSONResponse({"ok": False, "error": "Incidente desconocido."}, status_code=400)
    if cuerpo.accion not in ("resolve", "acknowledge", "reopen"):
        return JSONResponse({"ok": False, "error": "Acción no válida."}, status_code=400)
    if cuerpo.accion == "resolve" and not await _permiso(
            "resolve_incident", "¿Marcar este incidente como resuelto?",
            riesgo="low"):
        return JSONResponse({"ok": False, "error": "Denegado por el usuario."}, status_code=409)
    if cuerpo.accion == "resolve":
        ok = store.cerrar_incidente(inc_id, cuerpo.resolucion)
    else:
        ok = bool(store.obtener_incidente(inc_id))
    return JSONResponse({"ok": ok, "contador": store.contar_incidentes()},
                        status_code=200 if ok else 404)


# --------------------------------------------------------------------------- #
# Auditoría, historial, memoria, decisiones
# --------------------------------------------------------------------------- #
@router.get("/auditoria")
async def cmd_auditoria(request: Request, limite: int = 60, risk: str = "",
                        resultado: str = "", q: str = ""):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(services.auditoria, limite, risk, resultado, q))


@router.get("/historial")
async def cmd_historial(request: Request, dias: int = 7):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(services.historial, dias))


@router.get("/memoria")
async def cmd_memoria(request: Request, q: str = "", limite: int = 60):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(services.memoria, q, min(limite, 200)))


@router.get("/decisiones")
async def cmd_decisiones(request: Request, mission_id: str = "", limite: int = 40):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(services.decisiones(mission_id, limite))


@router.post("/decisiones")
async def cmd_registrar_decision(request: Request, cuerpo: Decision):
    if not _authorized(request):
        return _no_autorizado()
    fila = store.registrar_decision(cuerpo.chosen, cuerpo.question, cuerpo.reason,
                                    cuerpo.alternatives, cuerpo.mission_id)
    return JSONResponse({"ok": True, "decision": fila}, status_code=201)


# --------------------------------------------------------------------------- #
# Conocimiento
# --------------------------------------------------------------------------- #
@router.get("/conocimiento")
async def cmd_conocimiento(request: Request):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(services.conocimiento_estado))


@router.get("/conocimiento/buscar")
async def cmd_conocimiento_buscar(request: Request, q: str = "", limite: int = 8):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(services.conocimiento_buscar, q, limite))


@router.post("/conocimiento/indexar")
async def cmd_conocimiento_indexar(request: Request, cuerpo: Texto):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(services.conocimiento_indexar,
                                                cuerpo.texto == "forzar"))


@router.post("/conocimiento/olvidar")
async def cmd_conocimiento_olvidar(request: Request):
    if not _authorized(request):
        return _no_autorizado()
    if not await _permiso("forget_knowledge",
                          "¿Borrar TODA la base de conocimiento? No hay vuelta atrás.",
                          riesgo="sensitive"):
        return JSONResponse({"ok": False, "error": "Denegado por el usuario."}, status_code=409)
    return JSONResponse(await asyncio.to_thread(services.conocimiento_olvidar))


# --------------------------------------------------------------------------- #
# Automatizaciones y protocolos
# --------------------------------------------------------------------------- #
@router.get("/automatizaciones")
async def cmd_automatizaciones(request: Request):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(services.automatizaciones))


@router.post("/automatizaciones")
async def cmd_crear_automatizacion(request: Request, cuerpo: NuevaAutomatizacion):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(services.automatizacion_crear,
                                                cuerpo.nombre, cuerpo.mensaje, cuerpo.cuando))


def _automatizacion(request: Request, task_id: str, funcion):
    if not _authorized(request):
        return _no_autorizado()
    if not _id_valido(task_id):
        return JSONResponse({"ok": False, "error": "Automatización desconocida."}, status_code=400)
    res = funcion(task_id)
    return JSONResponse(res, status_code=200 if res.get("ok") else 409)


@router.post("/automatizaciones/{task_id}/pausar")
async def cmd_pausar_automatizacion(request: Request, task_id: str):
    return _automatizacion(request, task_id, services.automatizacion_pausar)


@router.post("/automatizaciones/{task_id}/reanudar")
async def cmd_reanudar_automatizacion(request: Request, task_id: str):
    return _automatizacion(request, task_id, services.automatizacion_reanudar)


@router.post("/automatizaciones/{task_id}/correr")
async def cmd_correr_automatizacion(request: Request, task_id: str):
    return _automatizacion(request, task_id, services.automatizacion_correr_ya)


@router.post("/automatizaciones/{task_id}/borrar")
async def cmd_borrar_automatizacion(request: Request, task_id: str):
    return _automatizacion(request, task_id, services.automatizacion_borrar)


@router.post("/automatizaciones/{task_id}/editar")
async def cmd_editar_automatizacion(request: Request, task_id: str, cuerpo: EditarAutomatizacion):
    if not _authorized(request):
        return _no_autorizado()
    if not _id_valido(task_id):
        return JSONResponse({"ok": False, "error": "Automatización desconocida."}, status_code=400)
    return JSONResponse(services.automatizacion_editar(task_id, cuerpo.nombre,
                                                      cuerpo.mensaje, cuerpo.cuando))


@router.get("/protocolos")
async def cmd_protocolos(request: Request):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(services.protocolos())


@router.post("/protocolos")
async def cmd_crear_protocolo(request: Request, cuerpo: NuevoProtocolo):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(services.protocolo_crear(cuerpo.nombre, cuerpo.triggers,
                                                cuerpo.actions, cuerpo.say))


@router.delete("/protocolos/{nombre}")
async def cmd_borrar_protocolo(request: Request, nombre: str):
    if not _authorized(request):
        return _no_autorizado()
    if not _id_valido(nombre):
        return JSONResponse({"ok": False, "error": "Nombre no válido."}, status_code=400)
    return JSONResponse(services.protocolo_borrar(nombre))


# --------------------------------------------------------------------------- #
# Test Center
# --------------------------------------------------------------------------- #
@router.get("/pruebas")
async def cmd_pruebas(request: Request, ruta: str = ""):
    if not _authorized(request):
        return _no_autorizado()
    deteccion = await asyncio.to_thread(runner.detectar_tests, ruta or _ruta_proyecto())
    trabajos = runner.listar_tests()
    return JSONResponse({**deteccion, "trabajos": trabajos["trabajos"]})


@router.post("/pruebas")
async def cmd_correr_pruebas(request: Request, cuerpo: CorrerTests):
    if not _authorized(request):
        return _no_autorizado()
    ruta = cuerpo.ruta or _ruta_proyecto()
    if not await _permiso("run_tests",
                          f"¿Ejecutar los tests de «{ruta}» con «"
                          f"{cuerpo.comando or 'el comando detectado'}»?",
                          riesgo="medium"):
        return JSONResponse({"ok": False, "error": "Denegado por el usuario."}, status_code=409)
    return JSONResponse(await asyncio.to_thread(runner.ejecutar_tests, ruta,
                                                cuerpo.comando, cuerpo.timeout))


@router.get("/pruebas/{job_id}")
async def cmd_estado_pruebas(request: Request, job_id: str):
    if not _authorized(request):
        return _no_autorizado()
    if not _id_valido(job_id):
        return JSONResponse({"ok": False, "error": "Trabajo desconocido."}, status_code=400)
    return JSONResponse(runner.estado_test(job_id))


# --------------------------------------------------------------------------- #
# Cambios (Git) y checkpoints
# --------------------------------------------------------------------------- #
@router.get("/cambios")
async def cmd_cambios(request: Request, ruta: str = ""):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(runner.cambios_git, ruta or _ruta_proyecto()))


@router.get("/cambios/diff")
async def cmd_diff(request: Request, ruta: str = "", fichero: str = ""):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(runner.diff_fichero,
                                                ruta or _ruta_proyecto(), fichero))


@router.post("/cambios/commit")
async def cmd_commit(request: Request, cuerpo: Commit):
    if not _authorized(request):
        return _no_autorizado()
    if not await _permiso("git_commit",
                          f"¿Confirmar todos los cambios de «{cuerpo.ruta or _ruta_proyecto()}» "
                          f"con el mensaje «{cuerpo.mensaje[:60]}»?", riesgo="medium"):
        return JSONResponse({"ok": False, "error": "Denegado por el usuario."}, status_code=409)
    return JSONResponse(await asyncio.to_thread(runner.commit,
                                                cuerpo.ruta or _ruta_proyecto(), cuerpo.mensaje))


@router.get("/checkpoints")
async def cmd_checkpoints(request: Request, ruta: str = ""):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(runner.listar_checkpoints,
                                                ruta or _ruta_proyecto()))


@router.post("/checkpoints")
async def cmd_crear_checkpoint(request: Request, cuerpo: Checkpoint):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(runner.crear_checkpoint,
                                                cuerpo.ruta or _ruta_proyecto(),
                                                cuerpo.mission_id, cuerpo.nota))


@router.get("/checkpoints/{cp_id}")
async def cmd_comparar_checkpoint(request: Request, cp_id: str):
    if not _authorized(request):
        return _no_autorizado()
    if not _id_valido(cp_id):
        return JSONResponse({"ok": False, "error": "Checkpoint desconocido."}, status_code=400)
    return JSONResponse(await asyncio.to_thread(runner.comparar_checkpoint, cp_id))


@router.post("/checkpoints/{cp_id}/restaurar")
async def cmd_restaurar_checkpoint(request: Request, cp_id: str):
    if not _authorized(request):
        return _no_autorizado()
    if not _id_valido(cp_id):
        return JSONResponse({"ok": False, "error": "Checkpoint desconocido."}, status_code=400)
    if not await _permiso("restore_checkpoint",
                          "¿Restaurar este checkpoint? Se aplica encima de lo que hay ahora.",
                          riesgo="sensitive"):
        return JSONResponse({"ok": False, "error": "Denegado por el usuario."}, status_code=409)
    return JSONResponse(await asyncio.to_thread(runner.restaurar_checkpoint, cp_id))


@router.delete("/checkpoints/{cp_id}")
async def cmd_borrar_checkpoint(request: Request, cp_id: str):
    if not _authorized(request):
        return _no_autorizado()
    if not _id_valido(cp_id):
        return JSONResponse({"ok": False, "error": "Checkpoint desconocido."}, status_code=400)
    return JSONResponse(runner.borrar_checkpoint(cp_id))


# --------------------------------------------------------------------------- #
# Hablar con JARVIS, briefing, presentación y mantenimiento
# --------------------------------------------------------------------------- #
def _ruta_proyecto() -> str:
    try:
        from config import ROOT_DIR
        return str(ROOT_DIR)
    except Exception:  # noqa: BLE001
        import os
        return os.getcwd()


@router.post("/orden")
async def cmd_orden(request: Request, cuerpo: Texto):
    """Una orden escrita va por el mismo camino que la voz: handle_user_message."""
    if not _authorized(request):
        return _no_autorizado()
    texto = (cuerpo.texto or "").strip()
    if not texto:
        return JSONResponse({"ok": False, "error": "No has escrito nada."}, status_code=400)
    try:
        from main import handle_user_message
        respuesta = await asyncio.to_thread(handle_user_message, texto, "text", None, False)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)
    return JSONResponse({"ok": True, "respuesta": respuesta})


@router.post("/briefing")
async def cmd_briefing(request: Request, cuerpo: Briefing):
    if not _authorized(request):
        return _no_autorizado()
    resumen = await asyncio.to_thread(briefing.ausente, cuerpo.minutos)
    if cuerpo.hablar:
        try:
            from main import _speak_text
            await asyncio.to_thread(_speak_text, resumen["texto"], False)
        except Exception as exc:  # noqa: BLE001
            log.debug("no se pudo hablar el briefing: %s", exc)
    if not cuerpo.minutos:
        briefing.marcar_visita()
    return JSONResponse(resumen)


@router.post("/presentacion")
async def cmd_presentacion(request: Request, cuerpo: Presentacion):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse({"ok": True, "modo": briefing.modo_presentacion(cuerpo.activo)})


@router.get("/presentacion")
async def cmd_presentacion_estado(request: Request, mission_id: str = ""):
    if not _authorized(request):
        return _no_autorizado()
    return JSONResponse(await asyncio.to_thread(briefing.presentacion, mission_id))


@router.get("/misiones/{mission_id}/pasos")
async def cmd_pasos(request: Request, mission_id: str):
    if not _authorized(request):
        return _no_autorizado()
    try:
        from orchestrator.manager import get_manager
        tarea = get_manager().obtener(mission_id)
    except Exception as exc:  # noqa: BLE001
        return _error(exc)
    if not tarea:
        return JSONResponse({"ok": False, "error": "Esa misión no existe."}, status_code=404)
    return JSONResponse({"ok": True, "pasos": await asyncio.to_thread(
        briefing.pasos_con_evidencia, tarea),
        "nota": "OpenCode no informa de pasos: el estado sale de ficheros tocados y tests."})


@router.post("/diagnostico")
async def cmd_diagnostico(request: Request, cuerpo: Texto):
    if not _authorized(request):
        return _no_autorizado()
    areas = [a.strip() for a in (cuerpo.texto or "jarvis,rendimiento").split(",") if a.strip()]

    def _correr():
        from tools import diag_tools
        return diag_tools.system_diagnose_handler({"areas": areas})
    try:
        return JSONResponse(await asyncio.to_thread(_correr))
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.post("/sistema/optimizar")
async def cmd_optimizar(request: Request):
    if not _authorized(request):
        return _no_autorizado()
    if not await _permiso("auto_optimize",
                          "¿Ejecutar la optimización automática de JARVIS?", riesgo="medium"):
        return JSONResponse({"ok": False, "error": "Denegado por el usuario."}, status_code=409)

    def _correr():
        from tools import syshealth_tools
        return syshealth_tools.auto_optimize_handler({})
    try:
        return JSONResponse(await asyncio.to_thread(_correr))
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.post("/sistema/disco")
async def cmd_limpiar_disco(request: Request):
    if not _authorized(request):
        return _no_autorizado()
    if not await _permiso("cleanup_disk",
                          "¿Limpiar disco? Borra temporales y archivos antiguos.",
                          riesgo="sensitive"):
        return JSONResponse({"ok": False, "error": "Denegado por el usuario."}, status_code=409)

    def _correr():
        from tools import syshealth_tools
        return syshealth_tools.cleanup_disk_handler({})
    try:
        return JSONResponse(await asyncio.to_thread(_correr))
    except Exception as exc:  # noqa: BLE001
        return _error(exc)


@router.post("/opencode/servidor")
async def cmd_opencode_servidor(request: Request, cuerpo: Texto):
    """Reinicia SOLO el servidor propio de OpenCode. El compartido no se toca."""
    if not _authorized(request):
        return _no_autorizado()
    if not await _permiso("restart_opencode",
                          "¿Reiniciar el servidor de OpenCode? Las sesiones abiertas "
                          "se interrumpen.", riesgo="medium"):
        return JSONResponse({"ok": False, "error": "Denegado por el usuario."}, status_code=409)

    def _correr():
        from orchestrator import service
        service.stop_owned()
        return service.ensure()
    try:
        res = await asyncio.to_thread(_correr)
        return JSONResponse({"ok": bool(res.get("ok")), "servidor": res})
    except Exception as exc:  # noqa: BLE001
        return _error(exc)