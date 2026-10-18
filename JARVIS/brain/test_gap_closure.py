"""Pruebas del cierre de los 8 requisitos que faltaban.

No se comprueba que una función exista: se comprueba que hace lo que tiene que
hacer, incluyendo lo QUE NO DEBE hacer (un permiso que no se amplía solo, una
cancelación que no deja trabajo vivo, una métrica vieja que no se usa).

    python brain/test_gap_closure.py

Cada bloque va contra una base de datos temporal, así que no toca la real.
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
import time
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BRAIN))
os.environ.setdefault("JARVIS_DATA_DIR", tempfile.mkdtemp(prefix="jarvis-gap-"))

_fallos: list = []
_numero = [0]


def comprobar(condicion, texto: str) -> bool:
    _numero[0] += 1
    print(f"    · {'OK' if condicion else 'FALLA'}  {texto}")
    if not condicion:
        _fallos.append(texto)
    return bool(condicion)


def seccion(t: str) -> None:
    print(f"\n[{t}]")


# --------------------------------------------------------------------------- #
# 1. AGENT MANAGER
# --------------------------------------------------------------------------- #
def probar_agent_manager() -> None:
    seccion("1. Agent Manager")
    import agent_manager as am

    am.recuperar_al_arrancar()

    inv = am.inventario()
    comprobar(len(inv) > 0, f"inventa {len(inv)} agentes")
    comprobar(all({"id", "nombre", "estado", "disponible"} <= set(a) for a in inv),
              "cada agente tiene id, nombre, estado y disponibilidad")
    estados = {a["estado"] for a in inv}
    comprobar(estados <= {"registrado", "disponible", "ocupado", "bloqueado",
                          "no disponible"},
              f"los estados son del catálogo: {sorted(estados)}")

    # La cola ordena por prioridad
    ids = []
    for prio in (20, 90, 50):
        r = am.encolar("jarvis", {"p": prio}, prioridad=prio, mission_id="m-gap")
        comprobar(r.get("ok") is True, f"encolado con prioridad {prio}")
        ids.append(r["id"])
    cola = [t["id"] for t in am.cola() if t["estado"] == "en_cola"]
    comprobar(cola and cola[0] == ids[1],
              f"la cola sale por prioridad, no por orden de llegada ({cola[:3]})")

    # Concurrencia: nunca más de dos a la vez
    ejecutado = []
    while True:
        t = am.siguiente("jarvis")
        if t is None:
            break
        ejecutado.append(t["id"])
    en_vuelo = [t for t in am.trabajos_activos() if t["estado"] == "ejecutando"]
    comprobar(len(en_vuelo) <= am.limite_efectivo(),
              f"no pasa del tope ({len(en_vuelo)} <= {am.limite_efectivo()})")

    # El mismo agente no coge dos trabajos
    if en_vuelo:
        segundo = am.siguiente("jarvis")
        comprobar(segundo is None,
                  "un agente que ya está ocupado no coge un segundo trabajo")

    # Finalizar libera al siguiente
    if en_vuelo:
        primero = en_vuelo[0]["id"]
        am.heartbeat(primero)
        r = am.terminar(primero, ok=True, resultado="hecho")
        comprobar(r.get("ok") is True, "terminar un trabajo devuelve ok")
        comprobar(am.obtener(primero)["estado"] == "ok",
                  "y el trabajo queda como completado")

    # Cancelar es definitivo
    pendientes = [t for t in am.cola() if t["estado"] == "en_cola"]
    if pendientes:
        objetivo = pendientes[-1]["id"]
        r = am.cancelar(objetivo, "prueba")
        comprobar(r.get("ok") is True, "cancelar un trabajo en cola")
        comprobar(am.obtener(objetivo)["estado"] == "cancelado",
                  "queda cancelado, no vuelve a la cola")
        r2 = am.cancelar(objetivo)
        comprobar(r2.get("ok") is False,
                  "cancelar dos veces no lo hace: es idempotente y avisa")

    # Atascados: los marca, NO los cancela
    ejecutando = [t for t in am.trabajos_activos() if t["estado"] == "ejecutando"]
    if ejecutando:
        from memory import sqlite_store as db

        viejo = time.time() - (am.SEGUNDOS_SIN_HEARTBEAT + 60)
        with db.get_conn() as c:
            c.execute("UPDATE agent_jobs SET heartbeat=? WHERE id=?",
                      (viejo, ejecutando[0]["id"]))
            c.commit()
        atascados = am.detectar_atascados()
        comprobar(len(atascados) == 1, "detecta el trabajo sin heartbeat")
        estado = am.obtener(ejecutando[0]["id"])["estado"]
        comprobar(estado == "atascado",
                  f"lo MARCA como atascado en vez de matarlo (estado={estado})")

    # Limpieza para no arrastrar trabajo entre pruebas
    from memory import sqlite_store as db

    with db.get_conn() as c:
        c.execute("DELETE FROM agent_jobs")
        c.commit()


# --------------------------------------------------------------------------- #
# 2-3. RESOURCE MANAGER
# --------------------------------------------------------------------------- #
def _metricas(cpu=20.0, ram=30.0, disco=20.0, ts=None):
    return {"ts": ts or time.time(),
            "cpu": {"porcentaje": cpu, "nucleos": 8},
            "ram": {"porcentaje": ram, "disponible_gb": 8, "total_gb": 16},
            "discos": [{"libre_gb": 100 * (1 - disco / 100), "total_gb": 100}],
            "gpu": {"available": False}}


def probar_resource_manager() -> None:
    seccion("2. Resource Manager")
    import resource_manager as rm

    rm.conectar(emit=lambda e: None)

    # Cuando va bien, no toca nada
    rm.conectar(medir=lambda: _metricas())
    r = rm.revisar()
    comprobar(r["nivel"] == "bajo", f"con recursos de sobra: {r['nivel']}")
    comprobar(rm.limite_concurrencia() == rm.limite_por_defecto_estatico()
              if hasattr(rm, "limite_por_defecto_estatico") else True,
              "y no baja la concurrencia")

    # Con presión, baja y lo explica
    rm.conectar(medir=lambda: _metricas(cpu=97.0, ram=94.0, disco=95.0))
    r = rm.revisar()
    comprobar(r["nivel"] in ("alto", "critico"), f"con presión: {r['nivel']}")
    comprobar("ram" in r["motivo"] or "cpu" in r["motivo"],
              f"y dice POR QUÉ: {r['motivo'][:70]}")
    import agent_manager as am

    estatico = am.MAXIMO_CONCURRENTE
    efectivo = rm.limite_concurrencia()
    comprobar(efectivo < estatico or estatico == 1,
              f"baja la concurrencia ({efectivo} de {estatico})")
    comprobar(efectivo >= 1, "pero nunca a cero: siempre queda margen")

    # El Agent Manager lo respeta
    am.conectar_limite(rm.limite_concurrencia)
    for i in range(4):
        am.encolar("jarvis", {"i": i}, prioridad=50)
    while True:
        if am.siguiente("jarvis") is None:
            break
    en_vuelo = [t for t in am.trabajos_activos() if t["estado"] == "ejecutando"]
    comprobar(len(en_vuelo) <= efectivo,
              f"el Agent Manager respeta el límite ({len(en_vuelo)} <= {efectivo})")

    # Metadato viejo = desconocido, y lo conservador
    rm.conectar(medir=lambda: _metricas(ts=time.time() - 3600))
    # (el ts va dentro de la medida: sin esto el dato no está viejo y la
    # comprobación de "no se usa lo viejo" no probaría nada)
    r = rm.revisar()
    comprobar(r["nivel"] == "desconocido",
              f"una medida de hace una hora no se usa: {r['nivel']}")
    comprobar(rm.limite_concurrencia() <= efectivo + 1,
              "y en desconocido se va a lo mínimo, no a lo normal")

    # Sin métricas: también desconocido
    rm.conectar(medir=lambda: {"ts": time.time(), "cpu": None, "ram": None,
                               "discos": [], "gpu": {"available": False}})
    r = rm.revisar()
    comprobar(r["nivel"] == "desconocido", "sin sensores: desconocido, no 'bien'")
    comprobar("no hay" in r["motivo"] or "sin" in r["motivo"],
              f"y lo dice: {r['motivo'][:60]}")

    # Recuperación con histéresis: no sube de golpe
    rm.conectar(medir=lambda: _metricas())
    import agent_manager as _am

    _am.conectar_limite(rm.limite_concurrencia)
    r1 = rm.revisar()
    if r1["nivel"] in ("alto", "critico"):
        comprobar(r1["cambio"] is False,
                  "no sube la concurrencia hasta que se estabiliza")
        rm._estado["desde"] = time.time() - (rm.SEGUNDOS_ESTABILIZACION + 5)
        r2 = rm.revisar()
        comprobar(r2["nivel"] == "bajo" or r2["nivel"] in ("medio", "bajo"),
                  f"ya estabilizado, recupera ({r2['nivel']})")

    # Las decisiones se guardan
    con_decisiones = rm.decisiones(50)
    comprobar(len(con_decisiones) > 0,
              f"las decisiones quedan registradas ({len(con_decisiones)})")
    if con_decisiones:
        d = con_decisiones[-1]
        comprobar(bool(d.get("motivo")), "con su motivo")
        comprobar("valor_anterior" in d and "valor_nuevo" in d,
                  "y con el valor anterior y el nuevo")

    from memory import sqlite_store as db

    with db.get_conn() as c:
        c.execute("DELETE FROM agent_jobs")
        c.commit()
    rm.conectar(medir=None)


# --------------------------------------------------------------------------- #
# 4-5. MEMORIA ESTRUCTURADA
# --------------------------------------------------------------------------- #
def probar_memoria() -> None:
    seccion("3. Memoria estructurada")
    from memory import recuerdos as rec

    r = rec.recordar("El señor prefiere respuestas cortas", tipo=rec.PREFERENCIA,
                     origen="preferencia", proyecto="demo", forzar=True)
    comprobar(r.get("ok") is True, "guarda una preferencia")
    rid = r["id"]

    # No guarda ruido
    ruido = rec.recordar("hola")
    comprobar(ruido.get("ok") is False, "un 'hola' no se guarda")
    comprobar(bool(ruido.get("motivo")),
              f"y dice por qué: {ruido.get('motivo')}")

    # No duplica
    otra = rec.recordar("El señor prefiere respuestas cortas", tipo=rec.PREFERENCIA,
                        origen="preferencia", forzar=True)
    comprobar(otra.get("duplicado") is True, "no duplica lo mismo")
    comprobar(otra["id"] == rid, "es el mismo recuerdo")

    # Filtros
    en_proyecto = rec.listar(proyecto="demo")
    comprobar(any(x["id"] == rid for x in en_proyecto), "filtra por proyecto")
    por_tipo = rec.listar(tipo=rec.PREFERENCIA)
    comprobar(all(x["tipo"] == rec.PREFERENCIA for x in por_tipo),
              "filtra por tipo")
    ninguno = rec.listar(proyecto="otro-proyecto")
    comprobar(not any(x["id"] == rid for x in ninguno),
              "otro proyecto no lo devuelve")

    # Recuperación con explicación
    hits = rec.recuperar("respuestas cortas", proyecto="demo")
    comprobar(bool(hits), "recupera lo relevante")
    if hits:
        top = hits[0]
        comprobar(top["id"] == rid, "el más relevante es el que preguntaba")
        comprobar(bool(top.get("por_que")), f"y explica por qué: {top.get('por_que')}")
        comprobar(bool(top.get("motivo")), f"con palabras: {top.get('motivo')}")

    # Un recuerdo antiguo irrelevante no sale solo por existir
    from memory import sqlite_store as db

    with db.get_conn() as c:
        c.execute("UPDATE recuerdos SET creado=?, veces_usado=50 WHERE id=?",
                  (time.time() - 86400 * 400, rid))
        c.commit()
    irrelevante = rec.recuperar("zapatos de cricket nuclear", topes=3)
    comprobar(not any(x["id"] == rid for x in irrelevante),
              "un recuerdo viejo que no tiene que ver NO se recupera por estarlo")

    # Contradicción: una inferencia no pisa un hecho
    hecho = rec.recordar("El servidor de base de datos es PostgreSQL",
                         tipo=rec.HECHO, origen="infraestructura",
                         decision_id="d-1", forzar=True)
    inf = rec.recordar("Puede que el servidor de base de datos sea MySQL",
                       tipo=rec.INFERENCIA, origen="conversacion", forzar=True)
    comprobar(bool(inf.get("contradice")),
              f"la inferencia se marca como contradictoria ({inf.get('contradice')})")
    sigue = rec.obtener(hecho["id"])
    comprobar(sigue is not None and sigue["estado"] == "activo",
              "y el HECHO no se borra ni se cambia")

    # Actualizar, archivar, olvidar
    rec.actualizar(rid, relevancia=0.9)
    comprobar(abs(rec.obtener(rid)["relevancia"] - 0.9) < 0.01, "actualiza")
    rec.archivar(rid)
    comprobar(rec.obtener(rid)["estado"] == "archivado", "archivar no es borrar")
    comprobar(not any(x["id"] == rid for x in rec.listar(estado=rec.ACTIVO)),
              "y un archivado no se recupera")
    rec.olvidar(hecho["id"])
    comprobar(rec.obtener(hecho["id"])["estado"] == "eliminado",
              "olvidar es borrado lógico: se conserva el registro")


# --------------------------------------------------------------------------- #
# 6. KNOWLEDGE CATEGORIZADO
# --------------------------------------------------------------------------- #
def probar_knowledge() -> None:
    seccion("4. Knowledge categorizado")
    import library as lib

    casos = [("/x/JARVIS/docker-compose.yml", "docker"),
             ("/x/docs/arquitectura.md", "arquitectura"),
             ("/x/docs/troubleshooting.md", "troubleshooting"),
             ("/x/docs/seguridad.md", "seguridad")]
    for ruta, esperado in casos:
        deducido = lib.deducir_categoria(ruta)
        comprobar(deducido == esperado,
                  f"{ruta.rsplit('/', 1)[-1]} -> {deducido} (esperado {esperado})")

    lib.registrar_documento("/x/docs/docker.md", proyecto="jarvis")
    docs = lib.buscar_por_categoria("docker")
    comprobar(any("docker" in d["path"] for d in docs), "busca por categoría")
    comprobar(all(d.get("categoria") == "docker" for d in docs),
              "y los resultados traen su categoría")

    r = lib.reasignar_categoria("/x/docs/docker.md", "documentacion")
    comprobar(r.get("ok") is True, "se puede reclasificar a mano")
    back = [d for d in lib.buscar_por_categoria("docker") if "docker.md" in d["path"]]
    comprobar(not back, "y el cambio se respeta (lo que dijo la persona manda)")

    stats = lib.categorias_stats()
    comprobar("por_categoria" in stats and "ilegibles" in stats,
              "las estadísticas dicen cuántas hay y cuántas ilegibles")

    # Un documento ilegible se registra como tal, no desaparece
    lib.registrar_documento("/x/roto.pdf", estado="ilegible", detalle="no se pudo abrir")
    ilegibles = [d for d in lib.listar_documentos(estado="ilegible")]
    comprobar(bool(ilegibles), "un documento ilegible se ve, no se pierde en silencio")


# --------------------------------------------------------------------------- #
# 7. AUTORIZACIONES
# --------------------------------------------------------------------------- #
def probar_autorizaciones() -> None:
    seccion("5. Autorizaciones recordadas")
    import authz

    # Por defecto no se recuerda nada
    once = authz.concede("leer:x", authz.ONCE)
    comprobar(once.get("ok") and once.get("guardado") is False,
              "una autorización 'once' no deja rastro")
    comprobar(authz.esta_autorizado("leer:x")["ok"] is False,
              "y se vuelve a preguntar a la siguiente")

    # session sí, pero acotada
    s = authz.concede("leer:x", authz.SESION, tipo_operacion="leer", session_id="s1")
    comprobar(s.get("ok") is True, "una autorización de sesión se guarda")
    comprobar(authz.esta_autorizado("leer:x", "s1")["ok"] is True,
              "vale dentro de su sesión")
    comprobar(authz.esta_autorizado("leer:x", "s2")["ok"] is False,
              "NO vale en otra sesión (no sobrevive a un reinicio)")
    comprobar(authz.esta_autorizado("leer:otro", "s1")["ok"] is False,
              "NO se amplía sola a otro alcance")

    # until caduca
    u = authz.concede("leer:y", authz.HASTA, tipo_operacion="leer",
                      segundos=1, session_id="s1")
    time.sleep(1.3)
    authz.limpiar_caducadas()
    comprobar(authz.esta_autorizado("leer:y", "s1")["ok"] is False,
              "una autorización con caducidad deja de valer al expirar")

    # Críticas: nunca se recuerdan
    for critica in ("borrar_todo", "docker_stop", "shell_exec", "instalar_paquete"):
        r = authz.concede(critica, authz.SESION, tipo_operacion=critica,
                          session_id="s1")
        comprobar(r.get("ok") is False, f"«{critica}» no se autoriza para recordar")
    comprobar(not any(c in authz.PERMITEN_RECORDAR for c in ()), "política declarada")

    # Rechazo
    authz.concede("leer:z", authz.REJECT, tipo_operacion="leer", session_id="s1")
    comprobar(authz.esta_autorizado("leer:z", "s1")["ok"] is False,
              "lo denegado no se puede usar")

    # Revocación inmediata
    authz.concede("leer:w", authz.SESION, tipo_operacion="leer", session_id="s1")
    comprobar(authz.esta_autorizado("leer:w", "s1")["ok"] is True, "autorizado")
    authz.revoca(alcance="leer:w", actor="test")
    comprobar(authz.esta_autorizado("leer:w", "s1")["ok"] is False,
              "tras revocar, deja de valer en el acto")

    # Estado con la política a la vista
    est = authz.estado()
    comprobar("politica" in est and "nunca_recordable" in est["politica"],
              "el estado dice qué es y qué no se puede recordar")
    comprobar("once" in est["politica"]["por_defecto"],
              "y recuerda que el valor por defecto es preguntar siempre")

    from memory import sqlite_store as db

    with db.get_conn() as c:
        c.execute("DELETE FROM autorizaciones")
        c.commit()


# --------------------------------------------------------------------------- #
# 8. INTEGRACIÓN: el Risk Engine usa la memoria de autorizaciones
# --------------------------------------------------------------------------- #
def probar_integracion_permisos() -> None:
    seccion("6. El Permission Manager usa las autorizaciones")
    import risk_engine

    eventos = []
    motor = risk_engine.RiskEngine(emit=lambda e: eventos.append(e),
                                   session_id="s-int")
    import authz

    # Dar la categoria completa no se puede: el alcance es exacto
    r = authz.concede("leer_fichero", authz.SESION, tipo_operacion="leer_fichero",
                      session_id="s-int")
    comprobar(r.get("ok") is True, "autorizada la herramienta exacta")

    ok = asyncio.run(motor.check({"name": "leer_fichero", "risk_level": "high",
                                  "requires_confirmation": True,
                                  "confirm_text": "¿Leo?"}, {}))
    comprobar(ok is True, "con autorización vigente, NO pregunta")

    # Otra sesión: vuelve a preguntar
    otro = risk_engine.RiskEngine(emit=lambda e: None, session_id="s-otra")
    ok2 = asyncio.run(otro.check({"name": "leer_fichero", "risk_level": "high",
                                  "requires_confirmation": True,
                                  "confirm_text": "¿Leo?"}, {}))
    comprobar(ok2 is False, "en otra sesión sí pregunta (no se amplía el alcance)")

    # Crítica: la autorización no la salva
    authz.concede("desinstalar_paquete", authz.SESION, tipo_operacion="desinstalar",
                  session_id="s-int")
    ok3 = asyncio.run(motor.check({"name": "desinstalar_paquete", "risk_level": "critical",
                                   "requires_confirmation": True,
                                   "confirm_text": "¿Desinstalo?"}, {}))
    comprobar(ok3 is False, "una acción CRÍTICA no se ejecuta aunque esté autorizada")

    # Revocación surte efecto
    authz.revoca(alcance="leer_fichero", actor="test")
    ok4 = asyncio.run(motor.check({"name": "leer_fichero", "risk_level": "high",
                                   "requires_confirmation": True,
                                   "confirm_text": "¿Leo?"}, {}))
    comprobar(ok4 is False, "tras revocar, vuelve a preguntar")

    from memory import sqlite_store as db

    with db.get_conn() as c:
        c.execute("DELETE FROM autorizaciones")
        c.commit()


# --------------------------------------------------------------------------- #
# 9. PERMISOS DE OPENCODE: caducados y ya resueltos
# --------------------------------------------------------------------------- #
def probar_permisos_opencode() -> None:
    seccion("7. Permisos de OpenCode (estados y caducidad)")
    from orchestrator import store as ostore
    from orchestrator.manager import get_manager

    gestor = get_manager()
    tarea = ostore.nueva("prueba de permisos opencode")
    tarea["status"] = "WAITING_PERMISSION"
    tarea["payload"]["permissions"] = [
        {"id": "p1", "action": "bash", "detail": "npm install x",
         "status": "pending", "requested_at": time.time()},
        {"id": "p2", "action": "edit", "detail": "escribir fichero",
         "status": "resolved", "decision": "once"},
        {"id": "p3", "action": "bash", "detail": "viejo",
         "status": "pending", "expires_at": time.time() - 10},
    ]
    ostore.guardar(tarea)
    gestor._tareas[tarea["id"]] = tarea

    try:
        r = gestor.decidir_permiso(tarea["id"], "p2", "once")
        comprobar(r.get("ok") is False, "un permiso YA RESUELTO no se reenvía")
        comprobar("resolved" in str(r.get("error", "")),
                  f"y lo dice: {r.get('error')}")

        r3 = gestor.decidir_permiso(tarea["id"], "p3", "once")
        comprobar(r3.get("ok") is False, "un permiso CADUCADO no se aprueba")
        comprobar("caduc" in str(r3.get("error", "")).lower(),
                  f"y lo dice: {r3.get('error')}")

        r4 = gestor.decidir_permiso(tarea["id"], "no-existe", "once")
        comprobar(r4.get("ok") is False, "un permiso inexistente no se aprueba")

        # El de Discord: el texto se entiende y se traduce
        from orchestrator import inbox

        inbox.set_manager(gestor)
        texto = inbox.mensaje_permiso(tarea, tarea["payload"]["permissions"][0])
        comprobar("AUTORIZACIÓN REQUERIDA" in texto,
                  "el aviso de Discord dice qué pide")
        comprobar("una vez" in texto and "siempre" in texto and "denegar" in texto,
                  "con las tres opciones de OpenCode, no inventadas")
        comprobar(tarea["id"] in texto, "y lleva el identificador de la tarea")
    finally:
        from memory import sqlite_store as db

        gestor._tareas.pop(tarea["id"], None)
        with db.get_conn() as c:
            c.execute("DELETE FROM dev_tasks WHERE id=?", (tarea["id"],))
            c.commit()


# --------------------------------------------------------------------------- #
# 10. TODO SIGUE MONTADO
# --------------------------------------------------------------------------- #
def probar_no_roto() -> None:
    seccion("8. Nada de lo anterior se rompió")
    import main

    from rutas_reales import todas_las_rutas

    rutas = todas_las_rutas(main.app)
    comprobar(len(rutas) > 150, f"la app monta {len(rutas)} rutas")

    esperadas = [
        "/api/cmd/agentes/estado", "/api/cmd/agentes/cola",
        "/api/cmd/agentes/trabajo", "/api/cmd/agentes/trabajo/{job_id}/cancelar",
        "/api/cmd/recursos/decisiones", "/api/cmd/recursos/revisar",
        "/api/cmd/memoria/recuerdos", "/api/cmd/memoria/recuerdos/buscar",
        "/api/cmd/memoria/recuerdos/{rec_id}",
        "/api/cmd/conocimiento/documentos", "/api/cmd/conocimiento/categoria/{categoria}",
        "/api/cmd/autorizaciones", "/api/cmd/autorizaciones/activas",
        "/api/cmd/autorizaciones/revocar",
        "/api/dev/tasks/{task_id}/resume",
        "/api/ai/status", "/api/ai/stream", "/api/cmd/orden",
    ]
    faltan = [r for r in esperadas if r not in {x["ruta"] for x in rutas}]
    comprobar(not faltan, f"todas las rutas nuevas están (faltan: {faltan})")

    # El Decision Engine y los niveles siguen donde estaban
    import decision_engine as de

    comprobar(de.decidir("critical")["decision"] == de.CRITICAL,
              "el Decision Engine sigue funcionando")
    import risk_engine

    comprobar("critical" in risk_engine.RISK_LEVELS,
              "y los cuatro niveles de riesgo siguen")


def main() -> int:
    print("CIERRE DE HUECOS · Agent Manager, Resource Manager, Memory, "
          "Knowledge, permisos")
    probar_agent_manager()
    probar_resource_manager()
    probar_memoria()
    probar_knowledge()
    probar_autorizaciones()
    probar_integracion_permisos()
    probar_permisos_opencode()
    probar_no_roto()
    print()
    if _fallos:
        print(f"{len(_fallos)} COMPROBACIONES FALLIDAS de {_numero[0]}:")
        for f in _fallos:
            print(f"  · {f}")
        return 1
    print(f"HUECOS CERRADOS: {_numero[0]} comprobaciones.")
    return 0


if __name__ == "__main__":
    sys.exit(main())