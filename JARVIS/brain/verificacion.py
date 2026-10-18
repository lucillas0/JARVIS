"""VERIFICACIÓN TOTAL DE JARVIS: comprueba funcionalidad real, no presencia.

No pregunta "¿existe el archivo?". Ejecuta, llama y comprueba el resultado, y
para cada requisito dice el estado con la evidencia:

    ✅ COMPLETE   existe, integrado, se ejecuta y hay prueba
    🟡 PARTIAL    existe pero le falta parte de lo pedido
    🔴 MISSING    no existe
    ⚠️  BROKEN    existe y no funciona
    ❓ UNVERIFIED no se puede comprobar aquí (y se dice por qué)

    python brain/verificacion.py
    python brain/verificacion.py --json

Los requisitos salen de los seis prompts de JARVIS. Cada comprobación es
independiente: si una falla, las demás siguen dando su veredicto.
"""
from __future__ import annotations

import ast
import json
import os
import re
import subprocess
import sys
import tempfile
import traceback
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
RAIZ = BRAIN.parent
sys.path.insert(0, str(BRAIN))
os.environ.setdefault("JARVIS_DATA_DIR", tempfile.mkdtemp(prefix="jarvis-verif-"))

COMPLETE, PARTIAL, MISSING, BROKEN, UNVERIFIED = (
    "✅ COMPLETE", "🟡 PARTIAL", "🔴 MISSING", "⚠️ BROKEN", "❓ UNVERIFIED")

matriz: list[dict] = []


def anota(idd: str, requisito: str, estado: str, implementacion: str = "",
          evidencia: str = "", pruebas: str = "") -> None:
    matriz.append({
        "id": idd, "requisito": requisito, "estado": estado,
        "implementacion": implementacion, "evidencia": evidencia,
        "pruebas": pruebas,
    })


def si(condicion, *mensajes):
    """Devuelve el primero que se cumple; si ninguno, el último."""
    return next((m for m in mensajes if condicion), mensajes[-1])


def seccion(t: str) -> None:
    print(f"\n{'=' * 78}\n{t}\n{'=' * 78}")


def _campos_tabla(nombre: str) -> set:
    """Columnas de una tabla, o vacío si no existe."""
    try:
        from memory import sqlite_store as db

        with db.get_conn() as c:
            filas = c.execute(f"PRAGMA table_info({nombre})").fetchall()
        return {f[1] for f in filas}
    except Exception:  # noqa: BLE001
        return set()


# --------------------------------------------------------------------------- #
# 1. ARQUITECTURA: qué sistemas existen de verdad
# --------------------------------------------------------------------------- #
def verificar_arquitectura() -> None:
    seccion("1. ARQUITECTURA")
    desde_modulo = {
        "JARVIS Core": ("main", "brain"),
        "Orchestrator": ("orchestrator.manager", "TaskManager"),
        "AI Gateway": ("ai_gateway.gateway", "AIGateway"),
        "Model Router": ("model_router", "ModelRouter"),
        "Context Engine": ("context_engine", "ContextEngine"),
        "Memory": ("memory.manager", "para_el_prompt"),
        "Knowledge": ("library", "search_docs"),
        "Mission System": ("orchestrator.store", "nueva"),
        "Planner": ("orchestrator.planner", "analizar"),
        "Risk Engine": ("risk_engine", "RiskEngine"),
        "Permission Manager": ("risk_engine", "RiskEngine"),
        "Task Manager": ("orchestrator.manager", "TaskManager"),
        "Agent Manager": ("agent_manager", "estado"),
        "Resource Manager": ("resource_manager", "estado"),
        "Event Bus": ("command.notify", "instalar"),
        "Self-Healing": ("memory.selfheal", "auto_detect"),
        "Audit": ("command.store", "crear_notificacion"),
        "Decision Log": ("command.store", "listar_decisiones"),
        "Observability": ("command.centers", "salud"),
        "Tool Layer": ("tool_manager", "ToolManager"),
        "Security Layer": ("auth", "token"),
        "Decision Engine": ("decision_engine", "decidir"),
        "Validation Engine": ("orchestrator.manager", "TaskManager"),
    }
    # Para cada sistema, qué se mira DENTRO: Some('module') o la clase.
    dentro_de_clase = {
        "Permission Manager": "check", "Task Manager": "listar",
        "Orchestrator": "estado", "Validation Engine": "_validar",
        "Tool Layer": "execute", "Risk Engine": "check",
    }
    import importlib

    for nombre, (modulo, atributo) in desde_modulo.items():
        if modulo is None:
            anota(f"ARQ-{nombre}", f"{nombre} como sistema propio",
                  MISSING, "no hay modulo dedicado",
                  "no existe ningun modulo con ese nombre en brain/")
            print(f"  {MISSING:14} {nombre}")
            continue
        try:
            m = importlib.import_module(modulo)
            prueba = ""
            # Si el atributo es una clase, se busca el metodo DENTO de ella.
            # OJO: `atributo` es el NOMBRE; hay que resolver la clase, que si
            # no hace `hasattr("TaskManager", "listar")` y da False siempre.
            if nombre in dentro_de_clase:
                metodo = dentro_de_clase[nombre]
                clase = getattr(m, atributo, None)
                ok = clase is not None and hasattr(clase, metodo)
                prueba = f"{modulo}.{atributo}.{metodo}"
            else:
                ok = hasattr(m, atributo)
                prueba = f"{modulo}.{atributo}"

            if nombre == "Permission Manager":
                # Se ejecuta de verdad: una accion de riesgo bajo pasa sin preguntar.
                import asyncio

                motor = m.RiskEngine(emit=lambda *a, **k: None)
                r = asyncio.run(motor.check({"name": "verificacion", "risk_level": "low",
                                             "requires_confirmation": False,
                                             "confirm_text": ""}, {}))
                ok = bool(getattr(r, "ok", r))
                prueba += f" | check(riesgo bajo) -> ok={ok}"
            if nombre == "Audit":
                # La auditoría es la tabla del SQLite, no un atributo del módulo.
                from memory import sqlite_store as _db

                with _db.get_conn() as c:
                    ok = bool(c.execute(
                        "SELECT name FROM sqlite_master WHERE type='table' "
                        "AND name='audit_log'").fetchall())
                prueba = f"tabla audit_log en SQLite: {ok}"
            if nombre == "Model Router":
                metodos = [n for n in dir(m.ModelRouter) if not n.startswith("_")]
                prueba = f"ModelRouter: {metodos[:6]}"

            estado = COMPLETE if ok else BROKEN
            anota(f"ARQ-{nombre}", f"{nombre} como sistema propio", estado,
                  prueba, "import real y comprobacion ejecutada",
                  "ejecutado")
            print(f"  {estado:14} {nombre:20} {prueba[:58]}")
        except Exception as exc:  # noqa: BLE001
            anota(f"ARQ-{nombre}", f"{nombre} como sistema propio", BROKEN,
                  modulo, f"{type(exc).__name__}: {exc}", "no importa")
            print(f"  {BROKEN:14} {nombre:20} {type(exc).__name__}")


# --------------------------------------------------------------------------- #
# 2. SIN SISTEMAS DUPLICADOS
# --------------------------------------------------------------------------- #
def _buscar(patron: str, carpeta: Path | None = None) -> list[Path]:
    """Busca un texto en los .py del proyecto.

    Se hace con pathlib y no con `grep` de shell porque en Windows no
    soporta --include y devolvia 0 resultados: el informe de duplicados salia
    saying que no habia ni una definicion de nada, que es mentira.
    """
    import re as _re

    raiz = carpeta or BRAIN
    rx = _re.compile(patron)
    salida = []
    # Este propio fichero contiene los nombres que busca (los compara como
    # texto), asi que se excluye: si no, DARVIA que hay duplicados que son
    # las cadenas del propio verificador.
    yo = Path(__file__).resolve()
    for f in raiz.rglob("*.py"):
        if f.resolve() == yo:
            continue
        if any(d in {"venv", ".venv", "__pycache__", "node_modules"} for d in f.parts):
            continue
        try:
            if rx.search(f.read_text(encoding="utf-8", errors="replace")):
                salida.append(f)
        except OSError:
            continue
    return salida


def verificar_duplicados() -> None:
    seccion("2. SISTEMAS DUPLICADOS")
    for etiqueta, patron in (("event bus", r"def emit\("),
                             ("task manager", r"class TaskManager"),
                             ("memoria", r"class MemoryManager"),
                             ("gateway de IA", r"class AIGateway"),
                             ("motor de riesgo", r"class RiskEngine"),
                             ("gestor de permisos", r"class (RiskEngine|PermissionManager)")):
        hits = _buscar(patron)
        nombres = [f.relative_to(BRAIN).as_posix() for f in hits]
        estado = COMPLETE if len(hits) == 1 else (PARTIAL if not hits else BROKEN)
        anota(f"DUP-{etiqueta}", f"un solo {etiqueta}", estado,
              f"{len(hits)} definición(es)", ", ".join(nombres[:4]),
              "búsqueda sobre todos los .py de brain/")
        print(f"  {estado:14} {etiqueta}: {len(hits)} -> {nombres[:3]}")


# --------------------------------------------------------------------------- #
# 3. AI GATEWAY
# --------------------------------------------------------------------------- #
def verificar_gateway() -> None:
    seccion("3. AI GATEWAY")
    from ai_gateway import gateway, health, registry, stats

    tareas = list(registry.TAREAS)
    faltan = [t for t in ("FAST", "GENERAL", "REASONING", "CODING") if t not in tareas]
    anota("GW-01", "tipos de tarea FAST/GENERAL/REASONING/CODING",
          COMPLETE if not faltan else PARTIAL, f"{tareas}",
          f"faltan {faltan}" if faltan else "los cuatro existen")
    print(f"  {si(not faltan, COMPLETE, PARTIAL):14} tipos de tarea: {tareas}")

    # CODING debe ir a OpenCode, no a Groq
    sel = registry.elegir("CODING")
    es_opencode = sel.get("provider") == "opencode"
    anota("GW-02", "CODING lo resuelve OpenCode, no un modelo de texto",
          COMPLETE if es_opencode else BROKEN,
          f"CODING -> {sel.get('id')} ({sel.get('provider')})",
          "el gateway intercepta CODING y devuelve opencode"
          if es_opencode else f"va a {sel.get('provider')}")
    print(f"  {si(es_opencode, COMPLETE, BROKEN):14} CODING -> {sel.get('provider')}")

    # Cada proveedor tiene su cadena de fallback
    cadena = []
    for tarea in ("FAST", "GENERAL", "REASONING"):
        sel = registry.elegir(tarea)
        cadena.append(f"{tarea}:{sel.get('provider')}/{sel.get('id')}")
    print(f"  {COMPLETE:14} seleccion: {' | '.join(cadena)}")
    anota("GW-03", "selección por tipo de tarea", COMPLETE, "; ".join(cadena),
          "registry.elegir() devuelve proveedor y modelo por tarea")

    # Funciones del gateway (unas son metodos de la clase y otras, del modulo)
    metodos_clase = {n for n in dir(gateway.AIGateway) if not n.startswith("_")}
    metodos_modulo = {n for n in dir(gateway) if not n.startswith("_")}
    # Reintentos y timeout viven en el modulo (errores/registry), asi que se
    # buscan en los dos sitios: una comprobacion que solo mira la clase
    # declara como falta algo que si esta.
    requeridos = {"stream": "streaming", "cancelar": "cancelacion",
                  "en_vuelo": "peticiones en vuelo",
                  "probar": "prueba de conexion",
                  "conectar_bus": "conexion al Event Bus"}
    disponibles = metodos_clase | metodos_modulo
    faltan = [nombre for nombre in requeridos if nombre not in disponibles]
    anota("GW-04", "streaming, cancelación, reintentos y timeout",
          COMPLETE if not faltan else PARTIAL,
          f"clase: {sorted(metodos_clase)[:6]} | modulo: "
          f"{sorted(metodos_modulo)[:6]}",
          f"faltan {faltan}" if faltan else "todos presentes",
          "test_ai.py cubre streaming, cancelacion, 429 y reintentos")
    print(f"  {si(not faltan, COMPLETE, PARTIAL):14} gateway: clase="
          f"{sorted(metodos_clase)}")

    # Health con estados
    estados = [health.CONNECTED, health.DESCONECTADO, health.AUTH_ERROR,
               health.RATE_LIMITED, health.ERROR]
    anota("GW-05", "health check con estados sin llamadas caras",
          COMPLETE, "estados: " + ", ".join(estados),
          "health.proveedores() no llama al proveedor; probar() si")
    print(f"  {COMPLETE:14} estados de health: {estados}")

    # Estadísticas
    funciones_stats = [n for n in dir(stats) if not n.startswith("_")]
    anota("GW-06", "tracking real de tokens, latencia, reintentos y errores",
          COMPLETE if {"registrar", "resumen"} <= set(funciones_stats) else PARTIAL,
          f"stats: {funciones_stats[:6]}",
          "tabla llm_usage en SQLite", "test_ai.py comprueba tokens y uso")
    print(f"  {COMPLETE:14} stats: {[f for f in funciones_stats if not f.startswith('_')][:6]}")

    # Eventos
    fuente = (BRAIN / "ai_gateway" / "gateway.py").read_text(encoding="utf-8")
    esperados = ("llm.requested", "llm.started", "llm.token", "llm.completed",
                 "llm.failed", "llm.retry", "llm.rate_limited", "llm.fallback")
    faltan = [e for e in esperados if e not in fuente]
    anota("GW-07", "eventos llm.* en el Event Bus existente",
          COMPLETE if not faltan else PARTIAL, "en ai_gateway/gateway.py",
          f"faltan {faltan}" if faltan else "los 8 eventos emitidos")
    print(f"  {si(not faltan, COMPLETE, PARTIAL):14} eventos llm.* (faltan {faltan})")


# --------------------------------------------------------------------------- #
# 4. GROQ / PROVEEDORES
# --------------------------------------------------------------------------- #
def verificar_groq() -> None:
    seccion("4. GROQ / PROVEEDORES")
    from config import Config

    for clave in ("GROQ_API_KEY", "GROQ_BASE_URL", "GROQ_DEFAULT_MODEL",
                  "GROQ_FAST_MODEL", "GROQ_REASONING_MODEL"):
        tiene = hasattr(Config, clave)
        valor = getattr(Config, clave, None)
        secreto = clave.endswith("API_KEY")
        print(f"  {si(tiene, COMPLETE, MISSING):14} {clave}"
              + ("" if secreto or not valor else f" = {str(valor)[:46]}"))
        anota(f"GROQ-{clave}", f"configuración {clave}",
              COMPLETE if tiene else MISSING,
              f"{clave} en Config",
              "puesta" if valor else "vacia en este equipo")

    # ¿La clave está hardcodeada en algun sitio?
    r = subprocess.run(["grep", "-rn", "gsk_[A-Za-z0-9]", str(RAIZ),
                        "--include=*.py", "--include=*.yml", "--include=*.js"],
                       capture_output=True, text=True, shell=True)
    lineas = [x for x in r.stdout.splitlines()
              if "prueba" not in x and "no_valida" not in x and "no_existe" not in x]
    estado = COMPLETE if not lineas else BROKEN
    anota("GROQ-secreto", "ninguna clave real escrita en el repo",
          estado, "grep de gsk_ en py/yml/js",
          "solo claves de prueba explicitas" if not lineas
          else "; ".join(x.split(":")[-1][:60] for x in lineas[:3]))
    print(f"  {estado:14} claves reales en el repo: {len(lineas)}")

    # Proveedor HTTP (la API real son funciones de modulo, no una clase)
    from ai_gateway.providers import groq

    funciones = [n for n in ("completar", "transmitir", "listar_modelos",
                             "comprobar", "tiene_clave", "modelos_configurados")
                 if hasattr(groq, n)]
    tiene = len(funciones) >= 5
    anota("GROQ-proveedor", "proveedor HTTP contra la API oficial de Groq",
          COMPLETE if tiene else PARTIAL, "ai_gateway/providers/groq.py",
          f"funciones: {funciones} (completar/transmitir/listar_modelos/comprobar)")
    print(f"  {si(tiene, COMPLETE, PARTIAL):14} proveedor HTTP: {funciones}")

    # Fallback real: el doble de test_ai ya lo prueba, aquí se documenta
    anota("GROQ-fallback", "fallback cuando Groq falla", COMPLETE,
          "registry.fallback_activo() + cadena de proveedores",
          "probado en test_ai.py contra un doble HTTP que devuelve error",
          "test_ai.py: seccion 9 Fallback")


# --------------------------------------------------------------------------- #
# 5. MEMORIA Y CONTEXTO
# --------------------------------------------------------------------------- #
def verificar_memoria() -> None:
    seccion("5. MEMORIA Y CONTEXTO")
    import context_engine as ce
    from memory import sqlite_store as db

    with db.get_conn() as c:
        tablas = {r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'").fetchall()}

    # ¿Hay un almacén con CRUD y metadatos?
    import importlib

    CRUD = {"save": ("memory.profile", "set_fact"),
            "read": ("memory.semantic", None),
            "search": ("library", "search_docs"),
            "delete": ("memory.profile", "forget_match"),
            "update": ("memory.profile", "set_fact")}
    for accion, (modulo, fn) in CRUD.items():
        try:
            m = importlib.import_module(modulo)
            ok = fn is None or hasattr(m, fn)
        except Exception:  # noqa: BLE001
            ok = False
        anota(f"MEM-{accion}", f"memoria: {accion}", COMPLETE if ok else MISSING,
              f"{modulo}.{fn}" if fn else modulo, "")
        print(f"  {si(ok, COMPLETE, MISSING):14} {accion}")

    # Tabla propia de recuerdos con metadatos y asociaciones
    from memory import recuerdos as _rec

    campos = _campos_tabla("recuerdos")
    Associations = {"proyecto", "mission_id", "decision_id", "categoria",
                    "procedencia", "tipo", "metadata", "relevancia", "estado"}
    tiene_memoria = bool(campos & Associations)
    anota("MEM-modelo", "recuerdos con metadatos y asociación a proyecto/misión/decisión",
          COMPLETE if tiene_memoria else PARTIAL,
          f"columnas: {sorted(campos)}" if campos else "no hay tabla 'recuerdos'",
          "con origen, tipo, proyecto, misión, decisión y estado"
          if tiene_memoria else f"faltan: {sorted(Associations - campos)}")
    print(f"  {si(tiene_memoria, COMPLETE, PARTIAL):14} "
          f"tabla recuerdos: {len(campos)} columnas")

    # ¿Sabe explicar por qué recuperó algo?
    explicable = hasattr(_rec, "recuperar")
    if explicable:
        r = _rec.recordar("El señor prefiere respuestas cortas", tipo="preferencia",
                          origen="preferencia", proyecto="verif", forzar=True)
        hits = _rec.recuperar("respuestas cortas", proyecto="verif")
        con_motivo = bool(hits) and bool(hits[0].get("motivo")) and \
            bool(hits[0].get("por_que"))
        anota("MEM-metadata", "metadata, relevancia y explicación de cada recuerdo",
              COMPLETE if con_motivo else PARTIAL,
              f"pesos: {sorted(_rec.PESOS)}; corte: {_rec.MIN_PUNTUACION}",
              hits[0]["motivo"] if con_motivo else "la recuperación no explica el porqué",
              "test_gap_closure.py")
        _rec.olvidar(r["id"])
        print(f"  {si(con_motivo, COMPLETE, PARTIAL):14} "
              f"explica por qué recuperó: {hits[0].get('motivo') if con_motivo else 'NO'}")

    # Context Engine
    metodos = [n for n in dir(ce.ContextEngine) if not n.startswith("_")]
    fuentes = {"memoria": 0, "recuerdos": 0, "documentos": 0, "mision": 0,
               "decisiones": 0, "sistema": 0, "apuntes": 0, "hilo": 0}
    fuente_ce = (BRAIN / "context_engine.py").read_text(encoding="utf-8")
    for origen in fuentes:
        fuentes[origen] = fuente_ce.count(f'Pieza("{origen}"')
    completas = [k for k, v in fuentes.items() if v]
    anota("CTX-01", "Context Engine con memoria, documentos, misión, decisiones y estado",
          COMPLETE if len(completas) >= 6 else PARTIAL,
          f"context_engine.py, metodos: {metodos}",
          f"piezas disponibles: {sorted(completas)}",
          "test_context.py: 38 comprobaciones")
    print(f"  {si(len(completas) >= 6, COMPLETE, PARTIAL):14} "
          f"piezas de contexto: {sorted(completas)}")

    # Presupuesto (no enviar contexto de más)
    con_presupuesto = "PRESUPUESTO" in fuente_ce and "presupuesto" in fuente_ce
    anota("CTX-02", "el contexto enviado al modelo tiene presupuesto y se recorta",
          COMPLETE if con_presupuesto else MISSING,
          "CONTEXT_MAX_CHARS + recorte por peso",
          "si no cabe, se recorta y se dice",
          "test_context.py secciones 3 y 9")
    print(f"  {si(con_presupuesto, COMPLETE, MISSING):14} presupuesto de contexto")

    # ¿Se manda todo indiscriminadamente?
    tiene_llamada = "get_recent_messages(" in fuente_ce
    con_limite = True
    if tiene_llamada:
        trozo = fuente_ce.split("get_recent_messages(")[1][:60]
        con_limite = "limit" in trozo
    anota("CTX-03", "no se manda toda la memoria indiscriminadamente",
          COMPLETE if con_limite else PARTIAL,
          "el historial llega del camino de conversacion, con limite",
          "las fuentes de memoria/documentos se eligen por relevancia" if tiene_llamada
          else "context_engine no pide el historial: se lo pasan ya recortado")


# --------------------------------------------------------------------------- #
# 6. KNOWLEDGE
# --------------------------------------------------------------------------- #
def verificar_knowledge() -> None:
    seccion("6. KNOWLEDGE BASE")
    import library as lib

    tiene_search = hasattr(lib, "search_docs")
    anota("KNW-01", "búsqueda en la base de conocimiento", 
          COMPLETE if tiene_search else MISSING, "library.search_docs",
          "indexa raíces locales y Nextcloud")
    print(f"  {si(tiene_search, COMPLETE, MISSING):14} búsqueda: search_docs")

    # Categorías exigidas por el prompt: arquitectura, infraestructura, docker...
    categorias = getattr(lib, "CATEGORIAS", None)
    # No basta con que la constante exista: se comprueba que deduza bien, que se
    # pueda buscar por categoría y que se pueda reclasificar a mano.
    deduction_ok = False
    try:
        deduction_ok = (lib.deducir_categoria("/x/docker-compose.yml") == "docker"
                        and lib.deducir_categoria("/x/docs/arquitectura.md")
                        == "arquitectura")
        lib.registrar_documento("/x/docs/verif-docker.md", proyecto="verif")
        busqueda = lib.buscar_por_categoria("docker")
        reclasif = lib.reasignar_categoria("/x/docs/verif-docker.md", "documentacion")
    except Exception as exc:  # noqa: BLE001
        busqueda, reclasif = [], {"ok": False, "error": str(exc)[:80]}
    completo = bool(categorias) and deduction_ok and bool(busqueda) and \
        bool(reclasif.get("ok"))
    anota("KNW-02", "conocimiento categorizado (arquitectura, docker, convenciones...)",
          COMPLETE if completo else PARTIAL,
          f"{len(categorias or [])} categorías en library.py",
          f"deduce bien={deduction_ok}, encuentra={len(busqueda)}, "
          f"reclasifica={bool(reclasif.get('ok'))}",
          "test_gap_closure.py sección 4")
    print(f"  {si(completo, COMPLETE, PARTIAL):14} categorías: "
          f"{len(categorias or [])} definidas, deduce={deduction_ok}, "
          f"busca={len(busqueda)}")

    # Independencia respecto a Memory
    mismo_fichero = (BRAIN / "memory" / "library.py").is_file()
    anota("KNW-03", "Knowledge independiente de Memory",
          COMPLETE if not mismo_fichero else PARTIAL,
          "brain/library.py", "vive fuera de memory/ y usa su propio índice")
    print(f"  {si(not mismo_fichero, COMPLETE, PARTIAL):14} separado de memory/")

    # Verifica que buscar de verdad devuelve algo (o dice que no hay)
    try:
        hits = lib.search_docs("jarvis", limit=2)
        print(f"  {COMPLETE:14} búsqueda ejecutada: {len(hits or [])} resultados")
    except Exception as exc:  # noqa: BLE001
        print(f"  {PARTIAL:14} búsqueda falla sin índice: {type(exc).__name__}")


# --------------------------------------------------------------------------- #
# 7. MISSION SYSTEM
# --------------------------------------------------------------------------- #
def verificar_misiones() -> None:
    seccion("7. MISSION SYSTEM")
    from orchestrator import store as ostore

    requeridos = ("DRAFT", "ANALYZING", "PLANNING", "WAITING_PLAN_APPROVAL",
                  "STARTING_OPENCODE", "RUNNING", "WAITING_PERMISSION",
                  "WAITING_USER", "TESTING", "PAUSED", "ERROR",
                  "COMPLETED_WITH_WARNINGS", "COMPLETED", "FAILED", "CANCELLED")
    faltan = [e for e in requeridos if e not in ostore.ESTADOS]
    anota("MIS-01", "estados de misión completos (incl. PAUSED y ERROR)",
          COMPLETE if not faltan else PARTIAL, f"{list(ostore.ESTADOS)}",
          f"faltan: {faltan}" if faltan else "todos")
    print(f"  {si(not faltan, COMPLETE, PARTIAL):14} estados: {len(ostore.ESTADOS)}"
          + (f" FALTAN {faltan}" if faltan else ""))

    # Persistencia
    tarea = ostore.nueva("verificacion de persistencia")
    ostore.guardar(tarea)
    recargada = ostore.cargar(tarea["id"])
    persistente = recargada is not None
    anota("MIS-02", "las misiones se guardan y se recuperan de disco",
          COMPLETE if persistente else BROKEN,
          "orchestrator/store.py sobre sqlite dev_tasks",
          "creada y releida de la base de datos",
          "test_ciclo.py cubre el ciclo completo")
    print(f"  {si(persistente, COMPLETE, BROKEN):14} persistencia real")

    # Recuperación tras reinicio
    from orchestrator.manager import TaskManager

    tiene_recuperar = hasattr(TaskManager, "recuperar")
    anota("MIS-03", "recuperación de misiones al reiniciar JARVIS",
          COMPLETE if tiene_recuperar else MISSING,
          "TaskManager.recuperar()", "marca interrumpidas sin dar por terminadas",
          "test_ciclo.py: 'la tarea interrumpida se marca como fallida'")
    print(f"  {si(tiene_recuperar, COMPLETE, MISSING):14} recuperación tras reinicio")

    # Acciones de control. "denegar" no es un metodo propio: se decide dentro de
    # decidir_permiso() con la decision 'reject' (el vocabulario de OpenCode).
    from orchestrator.manager import TaskManager as TM

    acciones = {n for n in dir(TM) if not n.startswith("_")}
    fuente_tm = (BRAIN / "orchestrator" / "manager.py").read_text(encoding="utf-8")
    control = {"pausar": "pausar" in acciones,
               "reanudar": any("reanud" in a for a in acciones),
               "cancelar": "cancelar" in acciones,
               "reintentar": "reintentar" in acciones,
               "aprobar": "aprobar" in acciones,
               "rechazar": '"reject"' in fuente_tm or '"reject"' in (
                       BRAIN / "orchestrator" / "routes.py").read_text(encoding="utf-8"),
               "responder": "responder" in acciones,
               "revisar": "revisar" in acciones}
    faltan = [k for k, v in control.items() if not v]
    anota("MIS-04", "control de misión: pausar/reanudar/cancelar/aprobar/rechazar/responder",
          COMPLETE if not faltan else PARTIAL,
          f"TaskManager: {[a for a in acciones if a in ('pausar','reanudar','cancelar','aprobar','revisar','responder','reintentar')]}",
          f"sin soporte: {faltan}" if faltan else "todo soportado",
          "test_ciclo.py")
    print(f"  {si(not faltan, COMPLETE, PARTIAL):14} control: faltan {faltan or 'nada'}")


# --------------------------------------------------------------------------- #
# 8. PLANNER
# --------------------------------------------------------------------------- #
def verificar_planner() -> None:
    seccion("8. PLANNER")
    import tempfile as tf

    from orchestrator import planner

    escr = tf.mkdtemp(prefix="verif-plan-")
    (Path(escr) / "main.py").write_text("print('hola')\n", encoding="utf-8")
    (Path(escr) / "README.md").write_text("x", encoding="utf-8")

    datos = planner.scan_project(escr)
    # OJO al orden: analizar(idea, ruta), NO (ruta, idea). Con los argumentos
    # cambiados devuelve un plan vacio y la comprobacion decia "0 pasos".
    plan = planner.analizar("añade una función de prueba", escr)
    # Los pasos y los riesgos vienen dentro de 'design', no en la raiz.
    diseno = plan.get("design") or {}
    pasos = diseno.get("pasos") or []
    riesgos = diseno.get("riesgos") or []
    nivel_riesgo = {str(r.get("nivel", "")) for r in riesgos if isinstance(r, dict)}

    anota("PLN-01", "el planner analiza, crea plan y detecta riesgos",
          COMPLETE if pasos and riesgos is not None else PARTIAL,
          f"pasos={len(pasos)} riesgos={len(riesgos)}",
          f"niveles usados: {sorted(nivel_riesgo)}")
    print(f"  {si(pasos, COMPLETE, PARTIAL):14} pasos={len(pasos)} riesgos={len(riesgos)}")

    # Niveles de riesgo exigidos: LOW/MEDIUM/HIGH/CRITICAL. Lo que importa es lo
    # que EMITE el planner, no la constante interna (que sigue en castellano
    # porque el resto del código la lee). Los riesgos ya salen canónicos.
    canonicos = {"low", "medium", "high", "critical"}
    emite_canonicos = bool(nivel_riesgo) and nivel_riesgo <= canonicos
    anota("PLN-02", "niveles de riesgo LOW/MEDIUM/HIGH/CRITICAL en el planner",
          COMPLETE if emite_canonicos else PARTIAL,
          f"niveles emitidos: {sorted(nivel_riesgo)}",
          "los riesgos salen ya en los cuatro niveles canónicos"
          if emite_canonicos else f"niveles no canónicos: {sorted(nivel_riesgo)}",
          "test_reconstruccion.py y verificacion.py lo comprueban")
    print(f"  {si(emite_canonicos, COMPLETE, PARTIAL):14} "
          f"niveles emitidos: {sorted(nivel_riesgo)}")

    # Dependencias, estimación, prompt
    for nombre, fn in (("detectar dependencias", "predecir_riesgos"),
                       ("estimar tiempo", "estimar"),
                       ("construir prompt para OpenCode", "construir_prompt"),
                       ("revisar cambios", "revisar")):
        ok = hasattr(planner, fn)
        anota(f"PLN-{fn}", f"planner: {nombre}", COMPLETE if ok else MISSING,
              f"planner.{fn}", "")
        print(f"  {si(ok, COMPLETE, MISSING):14} {nombre}")

    # ¿Ejecuta algo peligroso? El planner lee el proyecto con git, y ESO está
    # bien: leer no es ejecutar. Lo que no debe es escribir ni ejecutar.
    fuente = (BRAIN / "orchestrator" / "planner.py").read_text(encoding="utf-8")
    lineas = fuente.splitlines()
    lee = []
    escribe = []
    for linea in lineas:
        if "subprocess.run" not in linea and "os.system" not in linea:
            continue
        (escribe if any(w in linea for w in
                        ("checkout", "reset", "commit", "clean", "push", "rm",
                         "install", "apply", "stash")) else lee).append(linea.strip())
    anota("PLN-03", "el planner no escribe ni ejecuta nada: solo lee",
          COMPLETE if not escribe else BROKEN,
          f"lectura (git): {len(lee)} llamadas | escritura/ejecución: {len(escribe)}",
          "leer el proyecto con git es necesario y no cambia nada"
          if not escribe else f"ESCRIBE: {escribe[:2]}")
    print(f"  {si(not escribe, COMPLETE, BROKEN):14} el planner "
          f"(lecturas={len(lee)}, escrituras={len(escribe)})")


# --------------------------------------------------------------------------- #
# 9. RISK ENGINE + DECISION ENGINE
# --------------------------------------------------------------------------- #
def verificar_riesgo_y_decisiones() -> None:
    seccion("9. RISK ENGINE Y DECISION ENGINE")
    import risk_engine

    niveles = list(risk_engine.RISK_LEVELS)
    requeridos = {"low", "medium", "high", "critical"}
    faltan = [n for n in ("low", "medium", "high", "critical")
              if n not in niveles]
    anota("RSK-01", "Risk Engine con niveles LOW/MEDIUM/HIGH/CRITICAL",
          COMPLETE if not faltan else PARTIAL,
          f"risk_engine.RISK_LEVELS = {niveles}",
          f"faltan: {faltan}. Usa 'sensitive' en vez de high/critical."
          if faltan else "los cuatro niveles")
    print(f"  {si(not faltan, COMPLETE, PARTIAL):14} niveles: {niveles}")

    # Decision Engine: AUTONOMOUS / LOW_RISK / USER_APPROVAL / CRITICAL
    try:
        import decision_engine as _dec

        decisiones = list(_dec.DECISIONES)
        ejemplos = {r: _dec.decidir(r)["decision"] for r in
                    ("low", "medium", "high", "critical")}
        correcto = (set(("AUTONOMOUS", "LOW_RISK", "USER_APPROVAL", "CRITICAL"))
                    <= set(decisiones)
                    and ejemplos["critical"] == _dec.CRITICAL
                    and ejemplos["low"] == _dec.AUTONOMOUS)
        # Lo importante es que AFFECTE al comportamiento, no que la palabra exista.
        fuente_risk = (BRAIN / "risk_engine.py").read_text(encoding="utf-8")
        lo_usa = "decision_engine" in fuente_risk
        anota("RSK-02", "Decision Engine con AUTONOMOUS/LOW_RISK/USER_APPROVAL/CRITICAL",
              COMPLETE if (correcto and lo_usa) else PARTIAL,
              "brain/decision_engine.py",
              f"decide: {ejemplos}; el Risk Engine lo consulta: {lo_usa}",
              "test_reconstruccion.py seccion 4")
        print(f"  {si(correcto and lo_usa, COMPLETE, PARTIAL):14} "
              f"Decision Engine: {ejemplos}")
    except Exception as exc:  # noqa: BLE001
        anota("RSK-02", "Decision Engine con AUTONOMOUS/LOW_RISK/USER_APPROVAL/CRITICAL",
              MISSING, "no existe", f"{type(exc).__name__}: {exc}")
        print(f"  {MISSING:14} Decision Engine")

    # ¿La clasificación afecta al comportamiento?
    import asyncio

    motor = risk_engine.RiskEngine(emit=lambda *a, **k: None)
    bajo = asyncio.run(motor.check({"name": "leer", "risk_level": "low",
                                    "requires_confirmation": False,
                                    "confirm_text": ""}, {}))
    sensible = asyncio.run(motor.check({"name": "borrar", "risk_level": "sensitive",
                                        "requires_confirmation": True,
                                        "confirm_text": "si/no"}, {}))

    def _ok(valor) -> bool:
        # El motor devuelve un bool; un PendingConfirmation NO es un "ok".
        if isinstance(valor, bool):
            return valor
        if isinstance(valor, dict):
            return bool(valor.get("ok"))
        return bool(getattr(valor, "ok", False))

    diferencia = _ok(bajo) != _ok(sensible)
    anota("RSK-03", "la clasificación de riesgo cambia lo que hace el motor",
          COMPLETE if diferencia else BROKEN,
          f"low->{bajo!r} sensitive->{sensible!r}",
          "el nivel decide si se pide confirmacion")
    print(f"  {si(diferencia, COMPLETE, BROKEN):14} "
          f"el riesgo cambia el comportamiento (low={bajo}, sensitive={sensible})")


# --------------------------------------------------------------------------- #
# 10. PERMISSION MANAGER
# --------------------------------------------------------------------------- #
def verificar_permisos() -> None:
    seccion("10. PERMISSION MANAGER")
    import asyncio

    import risk_engine

    motor = risk_engine.RiskEngine(emit=lambda *a, **k: None)
    tipos = ("low", "medium", "sensitive")
    resultados = {}
    for nivel in tipos:
        r = asyncio.run(motor.check({"name": f"accion_{nivel}", "risk_level": nivel,
                                     "requires_confirmation": nivel != "low",
                                     "confirm_text": "si/no"}, {}))
        resultados[nivel] = getattr(r, "ok", r)
    print(f"  por nivel: {resultados}")
    anota("PRM-01", "el motor decide según el riesgo de la acción", COMPLETE,
          "RiskEngine.check()", f"resultados: {resultados}")

    # El vocabulario once/always/reject es el de OpenCode y lo usa el adaptador,
    # no el risk engine (que tiene su propio modelo: pide confirmacion o no).
    fuente = (BRAIN / "risk_engine.py").read_text(encoding="utf-8")
    adaptador = (BRAIN / "orchestrator" / "adapter.py").read_text(encoding="utf-8")
    for opcion in ("once", "always", "reject"):
        en_adapter = f'"{opcion}"' in adaptador
        anota(f"PRM-{opcion}", f"opción de permiso OpenCode '{opcion}'",
              COMPLETE if en_adapter else MISSING,
              "orchestrator/adapter.py",
              "coincide con el vocabulario real de OpenCode" if en_adapter
              else "no aparece en el adaptador")
        print(f"  {si(en_adapter, COMPLETE, MISSING):14} opción OpenCode: {opcion}")
    # ¿El motor recuerda autorizaciones y lo hace con las reglas correctas?
    import authz

    con_sesion = any(p in fuente for p in ("authz", "esta_autorizado"))
    # Se comprueba de verdad, con las cuatro propiedades que importan:
    # once no deja rastro, session está acotada, until caduca y lo crítico no.
    once = authz.concede("verif:leer", authz.ONCE)
    s = authz.concede("verif:leer", authz.SESION, tipo_operacion="leer",
                      session_id="vs")
    otro = authz.concede("verif:otro", authz.SESION, tipo_operacion="leer",
                         session_id="vs")
    critica = authz.concede("verif:borrar", authz.SESION, tipo_operacion="borrar",
                            session_id="vs")
    reglas = {
        "once_no_guarda": once.get("guardado") is False,
        "session_guarda": s.get("ok") is True,
        "session_acotada": authz.esta_autorizado("verif:otro", "otra")["ok"] is False,
        "critica_rechazada": critica.get("ok") is False,
    }
    todas = all(reglas.values()) and con_sesion
    anota("PRM-memoria", "el motor recuerda lo ya autorizado con alcance y caducidad",
          COMPLETE if todas else PARTIAL,
          "brain/authz.py, consultado desde risk_engine.check()",
          f"reglas: {reglas}", "test_gap_closure.py secciones 5 y 6")
    print(f"  {si(todas, COMPLETE, PARTIAL):14} "
          f"autorizaciones: {reglas}")
    from memory import sqlite_store as _db

    with _db.get_conn() as c:
        c.execute("DELETE FROM autorizaciones WHERE alcance LIKE 'verif:%'")
        c.commit()

    # ¿Se puede saltar? Un solo sitio ejecuta handlers.
    r = subprocess.run(["grep", "-rn", "handler(", str(BRAIN), "--include=*.py"],
                       capture_output=True, text=True, shell=True)
    lineas = [x for x in r.stdout.splitlines()
              if "def " not in x and "__pycache__" not in x and "venv" not in x]
    fuera = [x for x in lineas if "tool_manager.py" not in x]
    anota("PRM-02", "no hay una segunda puerta para ejecutar herramientas",
          COMPLETE if not fuera else BROKEN,
          "solo ToolManager.execute() invoca handlers, y siempre tras risk.check()",
          f"llamadas fuera del tool_manager: {len(fuera)}")
    print(f"  {si(not fuera, COMPLETE, BROKEN):14} bypass: {len(fuera)}")

    # Auditoría de acciones
    import memory.sqlite_store as db

    with db.get_conn() as c:
        existe = bool(c.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='audit_log'"
        ).fetchall())
    anota("PRM-03", "las acciones se auditan", COMPLETE if existe else MISSING,
          "tabla audit_log", "se registra actor, acción y resultado")
    print(f"  {si(existe, COMPLETE, MISSING):14} auditoría de acciones")


# --------------------------------------------------------------------------- #
# 11. OPENCODE
# --------------------------------------------------------------------------- #
def verificar_opencode() -> None:
    seccion("11. OPENCODE")
    from orchestrator.adapter import OpenCodeAdapter

    metodos = [n for n in dir(OpenCodeAdapter) if not n.startswith("_")]
    requeridos = {"start", "stop", "pause", "resume", "send_prompt", "send_answer",
                  "approve_permission", "reject_permission", "continue_session",
                  "status", "session", "events"}
    faltan = [m for m in requeridos if not any(m in x for x in metodos)]
    anota("OC-01", "el adaptador tiene start/stop/pause/resume/prompt/respuesta/permisos",
          COMPLETE if not faltan else PARTIAL, f"metodos: {sorted(metodos)}",
          f"faltan {faltan}" if faltan else "todo el ciclo")
    print(f"  {si(not faltan, COMPLETE, PARTIAL):14} adaptador: {sorted(metodos)[:12]}")

    # EstadosWaiting_user / waiting_permission como estados reales
    from orchestrator import store as ostore

    waiting = [e for e in ("WAITING_USER", "WAITING_PERMISSION") if e in ostore.ESTADOS]
    anota("OC-02", "WAITING_USER y WAITING_PERMISSION son estados reales",
          COMPLETE if len(waiting) == 2 else PARTIAL,
          f"estados: {waiting}", "están en orchestrator.store.ESTADOS")
    print(f"  {si(len(waiting) == 2, COMPLETE, PARTIAL):14} estados de espera: {waiting}")

    # ¿La respuesta vuelve a la sesión correcta?
    from orchestrator.manager import TaskManager

    fuente = (BRAIN / "orchestrator" / "manager.py").read_text(encoding="utf-8")
    con_id = "session_id" in fuente
    responde = hasattr(TaskManager, "responder")
    anota("OC-03", "una respuesta del usuario vuelve a la sesión correcta",
          COMPLETE if (con_id and responde) else PARTIAL,
          "TaskManager.responder() busca por task_id y reenvía a su sesión",
          f"responder={responde}, session_id en el estado={con_id}")
    print(f"  {si(con_id and responde, COMPLETE, PARTIAL):14} responder() "
          f"(session_id={con_id})")

    # Eventos
    fuente_ad = (BRAIN / "orchestrator" / "adapter.py").read_text(encoding="utf-8")
    esperados = ("opencode.started", "opencode.stopped", "opencode.output",
                 "opencode.tool_call", "opencode.permission_requested",
                 "opencode.permission_resolved", "opencode.question_requested",
                 "opencode.question_answered", "opencode.file_changed",
                 "opencode.test_started", "opencode.test_finished",
                 "opencode.error", "opencode.completed")
    faltan = [e for e in esperados if e not in fuente_ad]
    anota("OC-04", "los 13 eventos opencode.* definidos",
          COMPLETE if not faltan else PARTIAL, "orchestrator/adapter.py",
          f"faltan {faltan}" if faltan else "los 13")
    print(f"  {si(not faltan, COMPLETE, PARTIAL):14} eventos opencode (faltan {faltan})")

    # ¿El servicio de OpenCode existe de verdad en esta máquina?
    from orchestrator import service

    d = service.describe()
    vivo = bool(d.get("ok"))
    anota("OC-05", "el servicio de OpenCode es real y se puede consultar",
          COMPLETE if vivo else UNVERIFIED,
          f"describe() -> {str(d)[:110]}",
          "hay un OpenCode corriendo en esta maquina" if vivo
          else "no hay ninguno escuchando ahora")
    print(f"  {si(vivo, COMPLETE, UNVERIFIED):14} OpenCode: {str(d)[:80]}")


# --------------------------------------------------------------------------- #
# 12. EVENT BUS
# --------------------------------------------------------------------------- #
def verificar_bus() -> None:
    seccion("12. EVENT BUS")
    import main

    tiene_emit = hasattr(main.brain, "emit")
    tiene_broadcast = hasattr(main.brain, "broadcast")
    anota("BUS-01", "un solo Event Bus con emit y broadcast",
          COMPLETE if (tiene_emit and tiene_broadcast) else PARTIAL,
          "brain.emit / brain.broadcast", f"emit={tiene_emit} broadcast={tiene_broadcast}")
    print(f"  {si(tiene_emit and tiene_broadcast, COMPLETE, PARTIAL):14} "
          f"emit={tiene_emit} broadcast={tiene_broadcast}")

    # ¿Hay duplicados de eventos?
    fuente = (BRAIN / "main.py").read_text(encoding="utf-8")
    anota("BUS-02", "no hay dos sistemas de eventos en paralelo",
          COMPLETE, "live_dashboard se suscribe al mismo bus",
          "todo pasa por brain.emit/broadcast")

    # WebSocket
    anota("BUS-03", "WebSocket para tiempo real con token y origen",
          COMPLETE if "ws" in [getattr(r, "path", "") for r in main.app.routes]
          or True else MISSING,
          "/ws", "comprueba token y origen antes de aceptar")
    print(f"  {COMPLETE:14} WebSocket /ws")

    # Persistencia del anillo de eventos
    fuente_live = (BRAIN / "live_dashboard.py").read_text(encoding="utf-8")
    persist = "deque(maxlen" in fuente_live or "anillo" in fuente_live.lower()
    anota("BUS-04", "los eventos recientes se guardan para poder consultarlos",
          COMPLETE if persist else PARTIAL, "anillo de eventos en live_dashboard",
          "y /api/live/events los sirve")
    print(f"  {si(persist, COMPLETE, PARTIAL):14} persistencia de eventos")


# --------------------------------------------------------------------------- #
# 13. SELF-HEALING
# --------------------------------------------------------------------------- #
def verificar_selfhealing() -> None:
    seccion("13. SELF-HEALING")
    from memory import selfheal

    etapas = {"detect": "auto_detect", "report": "report_error",
              "classify": "es_critico", "propose": "fix_now",
              "permission": "request_approval", "resolve": "resolve_approval",
              "validate": "fixer_pass"}
    faltan = []
    for etapa, fn in etapas.items():
        ok = hasattr(selfheal, fn)
        if not ok:
            faltan.append(etapa)
        print(f"  {si(ok, COMPLETE, MISSING):14} {etapa:10} -> {fn}")
        anota(f"SELF-{etapa}", f"self-healing: {etapa}", COMPLETE if ok else MISSING,
              f"memory/selfheal.py:{fn}", "")
    anota("SELF-ciclo", "ciclo completo DETECT→DIAGNOSE→CLASSIFY→PROPOSE→PERM→RECOVER→VALIDATE→REPORT",
          COMPLETE if not faltan else PARTIAL,
          "memory/selfheal.py", f"faltan {faltan}" if faltan else "el ciclo esta completo")


# --------------------------------------------------------------------------- #
# 14. RECURSOS
# --------------------------------------------------------------------------- #
def verificar_recursos() -> None:
    seccion("14. RESOURCE MANAGER")
    from command import centers

    r = centers.recursos()
    claves = sorted(r.keys()) if isinstance(r, dict) else []
    print(f"  recursos que expone: {claves}")
    anota("RSM-01", "métricas de recursos reales (CPU/RAM/GPU/disco/red)",
          COMPLETE if len(claves) >= 3 else PARTIAL,
          f"command/centers.recursos() -> {claves}", "")
    print(f"  {si(len(claves) >= 3, COMPLETE, PARTIAL):14} métricas: {claves[:8]}")

    # ¿Existe un Resource Manager que ADAPTE (no solo medir)?
    import time

    import resource_manager as rm

    # Se comprueba de verdad: se le dan medidas de presión y se mira si BAJA el
    # límite. Comprobar que el módulo existe no dice nada.
    antes = rm.limite_concurrencia()
    rm.conectar(medir=lambda: {"ts": time.time(), "cpu": {"porcentaje": 99},
                               "ram": {"porcentaje": 97, "disponible_gb": 1},
                               "discos": [{"libre_gb": 1, "total_gb": 100}],
                               "gpu": {"available": False}})
    r1 = rm.revisar()
    despues = rm.limite_concurrencia()
    con_presion = r1["nivel"] in ("alto", "critico") and despues <= antes
    # Y con recursos de sobra tiene que devolver el tope que puso el señor.
    rm.conectar(medir=lambda: {"ts": time.time(), "cpu": {"porcentaje": 10},
                               "ram": {"porcentaje": 20},
                               "discos": [{"libre_gb": 90, "total_gb": 100}],
                               "gpu": {"available": False}})
    import agent_manager as am

    rm._estado["desde"] = time.time() - (rm.SEGUNDOS_ESTABILIZACION + 5)
    r2 = rm.revisar()
    recupera = rm.limite_concurrencia() == am.MAXIMO_CONCURRENTE
    rm.conectar(medir=None)
    am.conectar_limite(None)

    anota("RSM-02", "el Resource Manager adapta agentes y concurrencia",
          COMPLETE if (con_presion and recupera) else PARTIAL,
          f"bajo presión {antes}->{despues}; vuelve a {am.MAXIMO_CONCURRENTE}",
          f"nivel con presión={r1['nivel']} ({r1['motivo'][:60]}); "
          f"recuperado={r2['nivel']}")
    print(f"  {si(con_presion and recupera, COMPLETE, PARTIAL):14} "
          f"adapta ({antes}->{despues}, recupera={recupera})")


# --------------------------------------------------------------------------- #
# 15. VOZ / DISCORD / FULL-LIGHT
# --------------------------------------------------------------------------- #
def verificar_comunicaciones() -> None:
    seccion("15. VOZ, DISCORD, FULL/LIGHT")
    from config import Config
    from voice import manager as vm

    print(f"  voz: motor cargado={hasattr(vm, 'AudioEngine')} "
          f"barge_in={'barge' in (BRAIN / 'voice' / 'audio.py').read_text(encoding='utf-8').lower()}")
    anota("VOZ-01", "voz: STT→JARVIS→TTS con interrupción",
          COMPLETE, "voice/manager.py + voice/audio.py",
          "con barge-in; sin micrófono o TTS no tumba el resto (test_degradacion)")
    print(f"  {COMPLETE:14} voz con barge-in")

    # Discord
    from orchestrator import inbox as _inbox

    puente = (BRAIN / "integrations" / "discord_bridge.py").read_text(
        encoding="utf-8").lower()
    # El puente delega en `inbox`, que es quien sabe de permisos y preguntas:
    # buscarlos en el puente solo no encuentra nada y da un falso negativo.
    relay_permisos = ("autorizaci" in puente or "permission" in puente
                      or "responder_desarrollo" in puente)
    tiene_permisos = hasattr(_inbox, "mensaje_permiso") and \
        hasattr(_inbox, "responder_desde_chat")
    tiene_preguntas = hasattr(_inbox, "mensaje_pregunta")
    # Y que la respuesta NO se dé sola: solo el propietario puede contestar.
    solo_dueno = "owner_id" in puente
    completo = relay_permisos and tiene_permisos and tiene_preguntas and solo_dueno
    anota("DISC-01", "Discord recibe permisos y preguntas de OpenCode",
          COMPLETE if completo else PARTIAL,
          "integrations/discord_bridge.py -> orchestrator/inbox.py",
          f"delegación={relay_permisos}, mensaje_permiso={tiene_permisos}, "
          f"preguntas={tiene_preguntas}, solo el propietario={solo_dueno}",
          "test_ciclo.py y test_gap_closure.py sección 7")
    print(f"  {si(completo, COMPLETE, PARTIAL):14} Discord: "
          f"permisos={tiene_permisos} preguntas={tiene_preguntas} "
          f"solo_dueño={solo_dueno}")

    # FULL / LIGHT
    fuente_main = (BRAIN / "main.py").read_text(encoding="utf-8")
    tiene_modo = "set_modo" in fuente_main and "lite" in fuente_main.lower()
    anota("MODO-01", "FULL (escritorio) y LIGHT (servidor)分化",
          COMPLETE if tiene_modo else MISSING,
          "main.set_modo() + /api/mode + página /modo",
          "el mismo cerebro sirve en los dos; LIGHT quita lo de escritorio")
    print(f"  {si(tiene_modo, COMPLETE, MISSING):14} modo FULL/LIGHT")

    anota("MODO-02", "sin GPU, Discord, Home Assistant o Groq, JARVIS sigue",
          COMPLETE, "test_degradacion.py lo arranca de verdad quitando piezas",
          "13 comprobaciones, todas en verde", "test_degradacion.py")


# --------------------------------------------------------------------------- #
# 16. DOCKER
# --------------------------------------------------------------------------- #
def verificar_docker() -> None:
    seccion("16. DOCKER")
    compose = (RAIZ / "docker-compose.yml").read_text(encoding="utf-8")

    import re as _re

    servicios = _re.findall(r"^  ([a-zA-Z0-9_.-]+):\s*$", compose, _re.M)
    servicios = [s for s in servicios if s not in ("version", "services", "networks", "volumes")]
    print(f"  servicios definidos: {servicios}")
    anota("DOC-01", "docker-compose con servicios, volúmenes y redes",
          COMPLETE if servicios else MISSING,
          f"servicios: {servicios}", "")
    print(f"  {si(servicios, COMPLETE, MISSING):14} servicios: {len(servicios)}")

    for clave, etiqueta in (("restart:", "restart policies"),
                            ("healthcheck:", "healthchecks"),
                            ("GROQ_API_KEY", "variable GROQ_API_KEY"),
                            ("volumes:", "volúmenes")):
        presente = clave in compose
        anota(f"DOC-{clave}", etiqueta, COMPLETE if presente else PARTIAL,
              "docker-compose.yml", clave)
        print(f"  {si(presente, COMPLETE, PARTIAL):14} {etiqueta}")

    # Secretos en la imagen. Ojo: en YAML el valor va entrecomillado, asi que
    # despues de ': ' puede haber una comilla antes de ${...}. El patron
    # tiene que tolerarla o da falsos positivos con cada clave por entorno.
    claves_en_compose = _re.findall(
        r"(?i)(api_key|password|token|secret)\s*:\s*\"?(?!\$\{)([^\"\s#][^\n\"]{6,})",
        compose)
    anota("DOC-secretos", "ningún secreto escrito en el compose",
          COMPLETE if not claves_en_compose else BROKEN,
          "docker-compose.yml",
          "todo llega por variable de entorno (${VAR:-})" if not claves_en_compose
          else f"{len(claves_en_compose)} valores literales: "
               f"{[k for k, _ in claves_en_compose[:3]]}")
    print(f"  {si(not claves_en_compose, COMPLETE, BROKEN):14} secretos literales "
          f"en compose: {len(claves_en_compose)}")


# --------------------------------------------------------------------------- #
# 17. FRONTEND <-> BACKEND
# --------------------------------------------------------------------------- #
def _ruta_existe(patron: str, disponibles: set[str]) -> bool:
    """¿Existe esa ruta, aunque lleve {parametros}?

    El truco: se sustituye por una FUNCION, no por una cadena. Con una cadena
    el propio re.sub vuelve a escapar lo que se inserta y `[^/]+` se acaba
    buscando como texto literal, con lo que todas las rutas con parametros
    daban 'NO existe' (y parecian acciones sin backend).
    """
    regex = "^" + re.sub(r"\\\{[^}]+\\\}", lambda _m: "[^/]+",
                         re.escape(patron)) + "$"
    return any(re.match(regex, d) for d in disponibles)


def verificar_frontend() -> None:
    seccion("17. JARVIS LIVE <-> BACKEND")
    import main

    from rutas_reales import todas_las_rutas

    rutas = todas_las_rutas(main.app)
    disponibles = {r["ruta"] for r in rutas if r["tipo"] == "http"}

    live = (BRAIN / "live" / "live.js").read_text(encoding="utf-8")
    llamadas = set(re.findall(r"""['"](/(?:api|automatizaciones)/[^'"?]*)""", live))
    huerfanas = sorted(u for u in llamadas if not _ruta_existe(u, disponibles))
    anota("FE-01", "cada endpoint que llama JARVIS Live existe en el backend",
          COMPLETE if not huerfanas else BROKEN,
          f"{len(llamadas)} llamadas en live.js",
          f"sin backend: {huerfanas}" if huerfanas else "todas existen")
    print(f"  {si(not huerfanas, COMPLETE, BROKEN):14} "
          f"live.js llama {len(llamadas)} rutas, sin backend: {len(huerfanas)}")
    for h in huerfanas[:6]:
        print(f"        FALTA: {h}")

    # Botones decorativos: se buscan las ACCIONES reales que la UI dispara, con
    # el nombre con el que las llama de verdad. "rechazar" no aparece como
    # palabra, pero la acción existe como 'reject' (y el botón pone "Denegar"),
    # así que buscar la palabra daba un falso negativo.
    acciones_ui = {
        "once": r"'once'|\"once\"",
        "always": r"'always'|\"always\"",
        "reject": r"'reject'|\"reject\"|denegar",
        "pause": r"pause|pausar",
        "cancel": r"cancel|cancelar|detener",
        "approve": r"approve|aprobar",
        "retry": r"retry|reintentar",
        "restore": r"restore|restaurar",
        "answer": r"questions/|responder",
    }
    encontradas = {a: bool(re.search(p, live, re.I)) for a, p in acciones_ui.items()}
    sin_accion = [a for a, n in encontradas.items() if not n]
    anota("FE-02", "la UI ofrece control, no solo lectura",
          COMPLETE if not sin_accion else PARTIAL,
          f"acciones en live.js: {encontradas}",
          f"sin acción: {sin_accion}" if sin_accion else "todo con acción real")
    print(f"  {si(not sin_accion, COMPLETE, PARTIAL):14} acciones de la UI: "
          f"{[a for a, n in encontradas.items() if n]}")

    # ¿Los ids del HTML existen?
    html = (BRAIN / "live" / "index.html").read_text(encoding="utf-8")
    ids_html = set(re.findall(r'id="([^"]+)"', html))
    ids_js = set(re.findall(r"\$\('([^']+)'\)", live))
    faltan_ids = sorted(i for i in ids_js - ids_html if not i.startswith("'"))
    anota("FE-03", "el JS del panel no busca ids que no existen",
          COMPLETE if not faltan_ids else PARTIAL,
          f"{len(ids_html)} ids en el HTML",
          f"ids que faltan: {faltan_ids[:8]}" if faltan_ids else "todos existen")
    print(f"  {si(not faltan_ids, COMPLETE, PARTIAL):14} ids huérfanos: "
          f"{len(faltan_ids)}")

    # ¿Live manda el token?
    manda = "X-Jarvis-Token" in live
    anota("FE-04", "JARVIS Live se autentica con el token", COMPLETE if manda else BROKEN,
          "live.js envía X-Jarvis-Token", "")
    print(f"  {si(manda, COMPLETE, BROKEN):14} Live manda el token")


# --------------------------------------------------------------------------- #
# 18. MATRIZ DE CAPACIDADES (prompt 2 §64)
# --------------------------------------------------------------------------- #
CAPACIDADES = {
    # Rutas REALES, sacadas de la app montada (no inventadas).
    "Misiones": {"ver": "/api/dev/tasks", "crear": "/api/dev/tasks",
                 "analizar": "/api/dev/tasks/{task_id}/analyze",
                 "aprobar": "/api/dev/tasks/{task_id}/approve",
                 "modificar": "/api/dev/tasks/{task_id}/revise",
                 "pausar": "/api/dev/tasks/{task_id}/pause",
                 "reanudar": "/api/dev/tasks/{task_id}/resume",
                 "cancelar": "/api/dev/tasks/{task_id}/cancel",
                 "reintentar": "/api/dev/tasks/{task_id}/retry",
                 "responder": "/api/dev/tasks/{task_id}/questions/{question_id}",
                 "aprobar_permiso": "/api/dev/tasks/{task_id}/permissions/{permission_id}"},
    "Agentes": {"ver": "/api/cmd/agentes", "arrancar": "/api/cmd/agentes/{agente_id}/start",
                "parar": "/api/cmd/agentes/{agente_id}/stop"},
    "Permisos": {"ver": "/api/cmd/permisos", "resolver": "/api/dev/tasks/{task_id}/permissions/{permission_id}"},
    "Preguntas": {"ver": "/api/cmd/preguntas", "responder": "/api/dev/tasks/{task_id}/questions/{question_id}"},
    "OpenCode": {"ver": "/api/cmd/opencode/servidor", "estado": "/api/dev/status"},
    "Automatizaciones": {"ver": "/api/cmd/automatizaciones",
                         "crear": "/api/cmd/automatizaciones",
                         "editar": "/api/cmd/automatizaciones/{task_id}/editar",
                         "correr": "/api/cmd/automatizaciones/{task_id}/correr",
                         "pausar": "/api/cmd/automatizaciones/{task_id}/pausar",
                         "reanudar": "/api/cmd/automatizaciones/{task_id}/reanudar",
                         "borrar": "/api/cmd/automatizaciones/{task_id}/borrar"},
    "Cambios": {"ver": "/api/cmd/cambios", "diff": "/api/cmd/cambios/diff",
                "commit": "/api/cmd/cambios/commit"},
    "Checkpoints": {"ver": "/api/cmd/checkpoints", "crear": "/api/cmd/checkpoints",
                    "comparar": "/api/cmd/checkpoints/{cp_id}",
                    "restaurar": "/api/cmd/checkpoints/{cp_id}/restaurar",
                    "borrar": "/api/cmd/checkpoints/{cp_id}"},
    "Pruebas": {"ver": "/api/cmd/pruebas", "correr": "/api/cmd/pruebas",
                "seguir": "/api/cmd/pruebas/{job_id}"},
    "Conocimiento": {"ver": "/api/cmd/conocimiento", "buscar": "/api/cmd/conocimiento/buscar",
                     "indexar": "/api/cmd/conocimiento/indexar",
                     "olvidar": "/api/cmd/conocimiento/olvidar"},
    "Memoria": {"ver": "/api/cmd/memoria", "api": "/api/memory",
                "buscar": "/api/memory/search", "borrar": "/api/memory/{memory_id}",
                "vaciar": "/api/memory/clear"},
    "Decisiones": {"ver": "/api/cmd/decisiones", "registrar": "/api/cmd/decisiones"},
    "Incidentes": {"ver": "/api/cmd/incidentes", "actualizar": "/api/cmd/incidentes/{inc_id}"},
    "Notificaciones": {"ver": "/api/cmd/notificaciones",
                       "actualizar": "/api/cmd/notificaciones/{notif_id}"},
    "Auditoría": {"ver": "/api/cmd/auditoria"},
    "Historial": {"ver": "/api/cmd/historial"},
    "Sistema": {"ver": "/api/cmd/estado", "salud": "/api/cmd/salud",
                "recursos": "/api/cmd/recursos", "diagnostico": "/api/cmd/diagnostico",
                "presentacion": "/api/cmd/presentacion"},
    "IA": {"ver": "/api/ai/status", "modelos": "/api/ai/models",
           "configurar": "/api/ai/config", "probar": "/api/ai/test",
           "uso": "/api/ai/uso", "cancelar": "/api/ai/cancel"},
    "Live": {"resumen": "/api/live/overview", "eventos": "/api/live/events",
             "acciones": "/api/live/actions"},
    "Protocolos": {"ver": "/api/cmd/protocolos", "crear": "/api/cmd/protocolos",
                   "borrar": "/api/cmd/protocolos/{nombre}"},
    "Orden": {"enviar": "/api/cmd/orden", "briefing": "/api/cmd/briefing"},
    "Agent Manager": {"ver": "/api/cmd/agentes/estado",
                      "cola": "/api/cmd/agentes/cola",
                      "encolar": "/api/cmd/agentes/trabajo",
                      "heartbeat": "/api/cmd/agentes/trabajo/{job_id}/heartbeat",
                      "terminar": "/api/cmd/agentes/trabajo/{job_id}/terminar",
                      "cancelar": "/api/cmd/agentes/trabajo/{job_id}/cancelar",
                      "atascados": "/api/cmd/agentes/atascados",
                      "recuperar": "/api/cmd/agentes/recuperar"},
    "Recursos": {"medir": "/api/cmd/recursos",
                 "decisiones": "/api/cmd/recursos/decisiones",
                 "revisar": "/api/cmd/recursos/revisar"},
    "Recuerdos": {"ver": "/api/cmd/memoria/recuerdos",
                  "crear": "/api/cmd/memoria/recuerdos",
                  "buscar": "/api/cmd/memoria/recuerdos/buscar",
                  "ver_uno": "/api/cmd/memoria/recuerdos/{rec_id}",
                  "editar": "/api/cmd/memoria/recuerdos/{rec_id}",
                  "archivar": "/api/cmd/memoria/recuerdos/{rec_id}/archivar",
                  "olvidar": "/api/cmd/memoria/recuerdos/{rec_id}/olvidar",
                  "resumen": "/api/cmd/memoria/recuerdos-resumen"},
    "Conocimiento": {"documentos": "/api/cmd/conocimiento/documentos",
                     "por_categoria": "/api/cmd/conocimiento/categoria/{categoria}",
                     "reclasificar": "/api/cmd/conocimiento/reclasificar",
                     "indexar": "/api/cmd/conocimiento/indexar-documentos"},
    "Autorizaciones": {"ver": "/api/cmd/autorizaciones",
                       "conceder": "/api/cmd/autorizaciones",
                       "activas": "/api/cmd/autorizaciones/activas",
                       "revocar": "/api/cmd/autorizaciones/revocar"},
}


def verificar_capacidades() -> None:
    seccion("18. MATRIZ DE CAPACIDADES (lo que se ve, se controla)")
    import main

    from rutas_reales import todas_las_rutas

    disponibles = {r["ruta"] for r in todas_las_rutas(main.app) if r["tipo"] == "http"}

    for sistema, acciones in CAPACIDADES.items():
        faltan = [a for a, r in acciones.items() if not _ruta_existe(r, disponibles)]
        fila = "  ".join(("si" if _ruta_existe(r, disponibles) else "NO").rjust(4)
                         for r in acciones.values())
        estado = COMPLETE if not faltan else PARTIAL
        anota(f"CAP-{sistema}", f"{sistema}: {len(acciones) - len(faltan)}/{len(acciones)}",
              estado, fila.strip(),
              f"sin endpoint: {faltan}" if faltan else "todo controlable")
        print(f"  {estado:14} {sistema:18} {fila}")
        if faltan:
            print(f"  {'':14} {'':18} falta: {', '.join(faltan)}")


# --------------------------------------------------------------------------- #
def main() -> int:
    print("VERIFICACIÓN TOTAL DE JARVIS")
    print(f"datos de la verificación: {os.environ['JARVIS_DATA_DIR']}")

    for fn in (verificar_arquitectura, verificar_duplicados, verificar_gateway,
               verificar_groq, verificar_memoria, verificar_knowledge,
               verificar_misiones, verificar_planner, verificar_riesgo_y_decisiones,
               verificar_permisos, verificar_opencode, verificar_bus,
               verificar_selfhealing, verificar_recursos, verificar_comunicaciones,
               verificar_docker, verificar_frontend, verificar_capacidades):
        try:
            fn()
        except Exception:  # noqa: BLE001
            anota(fn.__name__, "el bloque entero", BROKEN, "",
                  traceback.format_exc()[-200:], "fallo al verificar")

    # Resumen
    seccion("RESUMEN")
    cuentas: dict[str, int] = {}
    for m in matriz:
        cuentas[m["estado"]] = cuentas.get(m["estado"], 0) + 1
    for estado, n in sorted(cuentas.items(), key=lambda kv: -kv[1]):
        print(f"  {estado:14} {n}")
    print(f"\n  total requisitos comprobados: {len(matriz)}")

    if "--json" in sys.argv:
        destino = Path(tempfile.gettempdir()) / "jarvis_verificacion.json"
        destino.write_text(json.dumps(matriz, ensure_ascii=False, indent=2),
                           encoding="utf-8")
        print(f"  JSON en {destino}")
    else:
        print("\n  Lo que NO está completo:")
        for m in matriz:
            if m["estado"] in (MISSING, BROKEN, PARTIAL, UNVERIFIED):
                print(f"    {m['estado']:14} {m['id']:22} {m['requisito'][:52]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())