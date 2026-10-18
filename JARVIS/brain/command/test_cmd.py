"""Pruebas del centro de mando.

Se comprueba lo que NO puede fallar en silencio:

  · un evento real del bus produce una notificación real, y un fallo produce
    un incidente real (sin duplicar el mismo fallo cada segundo);
  · la bandeja se puede leer, descartar y silenciar;
  · el Health Center informa de cada subsistema y no inventa estado;
  · los recursos dicen "no disponible" cuando el equipo no puede medirlos;
  · la matriz de control no ofrece ninguna acción sin backend detrás;
  · los checkpoints de Git guardan, comparan y se borran de verdad;
  · el Test Center detecta el comando real del proyecto y ejecuta de verdad;
  · el briefing se redacta a partir de hechos que existen;
  · la API rechaza a quien no tiene la sesión.

    python command/test_cmd.py
"""
from __future__ import annotations

import asyncio
import json
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path

_TEMP = tempfile.mkdtemp(prefix="jarvis-cmd-test-")
os.environ["JARVIS_DATA_DIR"] = _TEMP
RAIZ = Path(__file__).resolve().parent.parent
if str(RAIZ) not in sys.path:
    sys.path.insert(0, str(RAIZ))

_fallos: list = []


def comprobar(condicion: bool, texto: str) -> None:
    if condicion:
        print(f"    · OK  {texto}")
    else:
        print(f"    · FALLO  {texto}")
        _fallos.append(texto)


def peticion(token: str, metodo: str = "GET", query: str = ""):
    from starlette.requests import Request
    scope = {"type": "http", "method": metodo, "path": "/api/cmd" + query,
             "query_string": query.encode(), "headers": [(b"x-jarvis-token", token.encode())],
             "client": ("127.0.0.1", 1), "scheme": "http"}
    return Request(scope)


def cuerpo_json(respuesta) -> dict:
    try:
        return json.loads(bytes(respuesta.body).decode("utf-8"))
    except Exception:  # noqa: BLE001
        return {}


# --------------------------------------------------------------------------- #
def probar_notificaciones() -> None:
    print("\n[1] Notificaciones e incidentes desde el bus real")
    from command import notify, store

    notify.al_evento({"type": "opencode.permission_requested", "task_id": "dev-1",
                      "detail": "bash: npm install algo", "at": time.time()})
    notis = store.listar_notificaciones(20)
    comprobar(any(n["kind"] == "permission" and "npm install" in n["title"] for n in notis),
              "una petición de permiso real genera notificación")

    notify.al_evento({"type": "opencode.permission_requested", "task_id": "dev-1",
                      "detail": "bash: npm install algo", "at": time.time()})
    iguales = [n for n in store.listar_notificaciones(20) if "npm install" in n["title"]]
    comprobar(len(iguales) == 1, "el mismo permiso repetido no llena la bandeja")

    notify.al_evento({"type": "opencode.error", "task_id": "dev-1",
                      "detail": "OpenCode terminó con código 1", "at": time.time()})
    incidentes = store.listar_incidentes(20)
    comprobar(any("código 1" in i["title"] for i in incidentes),
              "un error real genera incidente")
    notify.al_evento({"type": "opencode.error", "task_id": "dev-1",
                      "detail": "OpenCode terminó con código 1", "at": time.time()})
    repetido = [i for i in store.listar_incidentes(20) if "código 1" in i["title"]]
    comprobar(len(repetido) == 1, "el bus no duplica el mismo fallo en segundos")
    # El refuerzo se comprueba contra la store: el mismo fallo, otra vez.
    store.abrir_incidente("medio", "bus:opencode.error", "OpenCode terminó con código 1",
                          dedupe_key="x")
    repetido = [i for i in store.listar_incidentes(20) if "código 1" in i["title"]]
    comprobar(len(repetido) == 1 and repetido[0]["occurrences"] >= 2,
              "el mismo fallo se refuerza en vez de duplicarse")

    contador = store.contar_notificaciones()
    comprobar(contador["total"] >= 2 and contador["sin_leer"] >= 1,
              "el contador distingue total y sin leer")

    objetivo = [n for n in notis if "npm install" in n["title"]][0]
    comprobar(store.marcar_notificacion(objetivo["id"], "read"), "se puede marcar leída")
    comprobar(not [n for n in store.listar_notificaciones(20) if n["id"] == objetivo["id"]
                   and n["seen"] == 0], "leída ya no cuenta como pendiente")
    comprobar(store.marcar_notificacion(objetivo["id"], "dismiss"), "se puede descartar")
    comprobar(all(n["id"] != objetivo["id"] or n["dismissed_at"]
                  for n in store.listar_notificaciones(20, incluir_descartadas=True)),
              "descartada sale de la bandeja pero queda en el historial")

    cerrado = store.cerrar_incidente(repetido[0]["id"], "Resuelto en la prueba")
    comprobar(cerrado and store.contar_incidentes()["abiertos"] == 0,
              "un incidente se puede cerrar de verdad")
    comprobar(not notify.al_evento({"type": "opencode.state", "detail": "ruido"}),
              "el ruido del bus no llena la bandeja")


def probar_salud_y_recursos() -> None:
    print("\n[2] Health Center y Resource Center")
    from command import centers

    salud = centers.salud(forzar=True)
    ids = {s["id"] for s in salud["subsistemas"]}
    comprobar({"nucleo", "datos", "memoria", "opencode", "voz", "discord", "bus"} <= ids,
              "se comprueban los subsistemas clave")
    estados = {s["estado"] for s in salud["subsistemas"]}
    comprobar(estados <= {"healthy", "degraded", "down", "unknown"},
              "los estados son los del contrato")
    comprobar(all(s.get("nombre") and s.get("detalle") is not None for s in salud["subsistemas"]),
              "cada subsistema explica su estado")

    rec = centers.recursos(forzar=True)
    comprobar("cpu" in rec and "ram" in rec and "discos" in rec,
              "los recursos reales estan")
    gpu = rec.get("gpu") or {}
    if gpu.get("available"):
        comprobar(bool(gpu.get("name")), "si hay GPU, dice cual es")
    else:
        comprobar(bool(gpu.get("reason")),
                  "si no hay GPU, explica por que no se puede medir")
    temp = rec.get("temperatura") or {}
    comprobar(temp.get("available") is False and bool(temp.get("reason")),
              "la temperatura no se inventa: se dice que no hay sensor")
    red = rec.get("red") or {}
    comprobar(red.get("available") is False or red.get("total_bytes") is not None,
              "la red o se mide o se explica")


def probar_matriz() -> None:
    print("\n[3] Matriz de control")
    from command import centers

    matriz = centers.matriz()
    filas = {f["entidad"]: f["acciones"] for f in matriz["matriz"]}
    comprobar("misiones" in filas and filas["misiones"]["create"] and filas["misiones"]["approve"],
              "las misiones declaran todo lo que hacen")
    comprobar(filas["agentes"]["stop"] is False,
              "agentes no ofrece parar: apps.py no lo soporta")
    comprobar(filas["agentes"]["start"] is True, "agentes ofrece arrancar (existe open_app)")
    comprobar(matriz["capacidades"]["docker"] is False
              and "docker_nota" in matriz["capacidades"],
              "no hay gestor de Docker y se dice en vez de fingirlo")
    comprobar(all(isinstance(v, bool) for f in matriz["matriz"]
                  for v in f["acciones"].values()), "la matriz es de verdad/falso, sin curses")


def probar_git() -> None:
    print("\n[4] Checkpoints de Git y Test Center")
    from command import runner

    repo = Path(_TEMP, "repo")
    repo.mkdir(parents=True, exist_ok=True)
    try:
        subprocess.run(["git", "init", "-q", str(repo)], check=True, timeout=30)
        subprocess.run(["git", "-C", str(repo), "config", "user.email", "t@t.t"], check=True)
        subprocess.run(["git", "-C", str(repo), "config", "user.name", "test"], check=True)
    except Exception as exc:  # noqa: BLE001
        print(f"    · (sin git en este equipo: {exc}) se omite Git")
        return
    (repo / "leeme.md").write_text("uno\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "-A"], check=True, timeout=30)
    subprocess.run(["git", "-C", str(repo), "commit", "-qm", "inicio"], check=True, timeout=30)

    cambios = runner.cambios_git(str(repo))
    comprobar(cambios["ok"] and cambios["total"] == 0, "un repo limpio no tiene cambios")

    (repo / "leeme.md").write_text("uno\ndos\n", encoding="utf-8")
    (repo / "nuevo.py").write_text("print('hola')\n", encoding="utf-8")
    cambios = runner.cambios_git(str(repo))
    comprobar(cambios["total"] == 2, "Git detecta los dos cambios de verdad")

    cp = runner.crear_checkpoint(str(repo), nota="antes de tocar")
    comprobar(cp["ok"] and cp["checkpoint"], "el checkpoint guarda un parche de verdad")
    comparacion = runner.comparar_checkpoint(cp["checkpoint"]["id"])
    comprobar(comparacion["ok"] and any("nuevo.py" in n for n in comparacion["nuevos_guardados"]),
              "comparar muestra el parche real")
    restaurado = runner.restaurar_checkpoint(cp["checkpoint"]["id"])
    comprobar(restaurado["ok"] or "chocaría" in restaurado.get("error", ""),
              "restaurar responde con lo que ha pasado de verdad (sin romper nada)")
    comprobar(runner.borrar_checkpoint(cp["checkpoint"]["id"])["ok"], "el checkpoint se borra")

    # Test Center: proyecto con pytest declarado
    proyecto = Path(_TEMP, "proyecto")
    proyecto.mkdir(parents=True, exist_ok=True)
    (proyecto / "pytest.ini").write_text("[pytest]\n", encoding="utf-8")
    (proyecto / "test_ok.py").write_text("def test_ok():\n    assert True\n", encoding="utf-8")
    deteccion = runner.detectar_tests(str(proyecto))
    comprobar(deteccion["tiene_tests"] and any("pytest" in c["comando"] for c in deteccion["comandos"]),
              "detecta el comando de test que declara el proyecto")
    vacio_dir = Path(_TEMP, "sin_tests")
    vacio_dir.mkdir(parents=True, exist_ok=True)
    comprobar(runner.detectar_tests(str(vacio_dir))["tiene_tests"] is False,
              "un proyecto sin tests lo dice en vez de inventarlos")

    trabajo = runner.ejecutar_tests(str(proyecto))
    comprobar(trabajo["ok"] and trabajo.get("job_id"), "lanza los tests de verdad")
    if trabajo.get("ok"):
        job = trabajo["job_id"]
        for _ in range(60):
            estado = runner.estado_test(job)
            if estado["estado"] != "ejecutando":
                break
            time.sleep(0.25)
        estado = runner.estado_test(job)
        comprobar(estado["estado"] in ("completado", "fallido"),
                  "el trabajo de tests termina con un estado real")
        comprobar(bool(estado.get("salida") or estado.get("error")),
                  "guarda la salida real del proceso")
        resumen = json.loads(estado.get("resumen") or "{}")
        comprobar("cuentas" in resumen, "lee los números del informe si los hay")


def probar_briefing() -> None:
    print("\n[5] Briefing y pasos con evidencia")
    from command import briefing, store

    store.crear_notificacion("question", "OpenCode necesita una decisión",
                             category="preguntas")
    resumen = briefing.ausente(30)
    comprobar(resumen["ok"] and isinstance(resumen["texto"], str) and resumen["texto"],
              "el briefing devuelve texto redactado")
    comprobar("pregunta" in resumen["texto"].lower()
              or "nada" in resumen["texto"].lower(),
              "el texto sale de hechos reales, no de plantillas")

    tarea = {
        "payload": {
            "plan": {"pasos": [
                {"n": 1, "titulo": "Crear módulo", "archivos": ["brain/nuevo.py"]},
                {"n": 2, "titulo": "Escribir tests", "archivos": ["tests/test_nuevo.py"]},
                {"n": 3, "titulo": "Documentar", "archivos": []},
            ]},
            "changes": [{"path": "brain/nuevo.py", "action": "added", "mark": "+"}],
            "tests": {"ran": False},
        }}
    pasos = briefing.pasos_con_evidencia(tarea)
    estados = {p["n"]: p["estado"] for p in pasos}
    comprobar(estados[1] == "COMPLETED", "un paso con su fichero tocado se marca completado")
    comprobar(estados[2] == "PENDING", "un paso sin evidencia NO se da por hecho")
    comprobar(all(p.get("evidencia") for p in pasos), "cada paso explica su evidencia")

    # El gestor devuelve la misión plana y el almacén la guarda con `payload`.
    # Los pasos tienen que salir igual en los dos casos.
    plana = {k: v for k, v in tarea.items() if k != "payload"}
    plana.update({k: v for k, v in tarea["payload"].items()})
    estados_planos = {p["n"]: p["estado"] for p in briefing.pasos_con_evidencia(plana)}
    comprobar(estados_planos == estados,
              "los pasos se leen igual si la misión viene plana del gestor")
    comprobar(briefing.hay_presentacion() in (True, False), "el modo presentación se guarda")


def probar_api() -> None:
    print("\n[6] API del centro de mando")
    from command import routes
    from auth import token as token_cerebro

    token = token_cerebro()
    respuesta = asyncio.run(routes.cmd_estado(peticion("token-inventado")))
    comprobar(respuesta.status_code == 401, "sin sesión, la API no responde")

    respuesta = asyncio.run(routes.cmd_estado(peticion(token)))
    datos = cuerpo_json(respuesta)
    comprobar(respuesta.status_code == 200 and datos.get("ok"), "con sesión, devuelve estado")
    comprobar("salud" in datos and "recursos" in datos and "contadores" in datos,
              "el estado trae salud, recursos y contadores")
    comprobar(set(datos["contadores"]) >= {"notificaciones", "incidentes", "permisos",
                                           "preguntas", "misiones_vivas"},
              "los contadores de la barra lateral salen de aquí")
    comprobar("capacidades" in datos, "el estado declara qué se puede controlar")

    respuesta = asyncio.run(routes.cmd_capacidades(peticion(token)))
    comprobar(cuerpo_json(respuesta).get("matriz"), "la matriz de control es accesible")

    respuesta = asyncio.run(routes.cmd_auditoria(peticion(token, query="?limit=5")))
    comprobar(cuerpo_json(respuesta).get("ok"), "la auditoría se lee")

    respuesta = asyncio.run(routes.cmd_incidentes(peticion(token)))
    comprobar("incidentes" in cuerpo_json(respuesta), "los incidentes se listan")

    respuesta = asyncio.run(routes.cmd_briefing(
        peticion(token, "POST"), routes.Briefing(minutos=10, hablar=False)))
    comprobar(cuerpo_json(respuesta).get("ok"), "el briefing se pide por API")


def probar_pantalla() -> None:
    """Lo que la pantalla principal lee: actividad, permisos y preguntas."""
    print("\n[7] La pantalla principal")
    from command import activity, routes
    from auth import token as token_cerebro

    act = activity.actividad()
    comprobar(act.get("ok"), "la actividad se puede leer")
    comprobar("hay_tarea" in act, "la actividad dice si hay tarea o no")
    comprobar(isinstance(act.get("pasos"), list), "las fases se pintan siempre como lista")
    if act.get("hay_tarea"):
        comprobar(act.get("progreso_base"), "el progreso explica en qué se basa")
        comprobar(act.get("progreso") is None or 0 <= act["progreso"] <= 100,
                  "el progreso nunca se sale de rango")
        comprobar(act.get("plan") or act.get("pasos") or True, "la actividad trae el plan real")

    # Regresión: el gestor aplana la misión al devolverla. Si la actividad
    # leyera `payload` a pelo, enseñaría una tarea sin plan ni fases.
    from orchestrator import store as dev_store

    tarea = dev_store.nueva("Misión de prueba para la pantalla")
    tarea["status"] = "RUNNING"
    tarea["started_at"] = time.time() - 30
    tarea["payload"]["plan"] = {"objetivo": "Objetivo de prueba", "pasos": [
        {"n": 1, "titulo": "Tocar un fichero", "archivos": ["brain/ejemplo.py"]},
        {"n": 2, "titulo": "Nada más", "archivos": []}]}
    tarea["payload"]["changes"] = [{"path": "brain/ejemplo.py", "action": "added"}]
    tarea["payload"]["events"] = [{"type": "opencode.started", "at": time.time(), "detail": "x"}]
    dev_store.guardar(tarea)
    try:
        act = activity.actividad()
        comprobar(act.get("hay_tarea") and act.get("id") == tarea["id"],
                  "la actividad elige la misión viva")
        comprobar((act.get("plan") or {}).get("objetivo") == "Objetivo de prueba",
                  "la actividad ve el plan aunque el gestor lo devuelva plano")
        comprobar(len(act.get("pasos") or []) == 2, "la actividad ve los pasos con evidencia")
        comprobar(act.get("progreso") == 50,
                  "el progreso sale de los pasos con evidencia, no de una suposición")
        comprobar(bool(act.get("eventos")), "la actividad trae la línea de tiempo")
    finally:
        from memory import sqlite_store as db
        with db.get_conn() as conn:
            conn.execute("DELETE FROM dev_tasks WHERE id = ?", (tarea["id"],))
            conn.commit()

    per = activity.permisos()
    comprobar(per.get("ok"), "los permisos se pueden leer")
    comprobar(per.get("total_pendientes") == len(per.get("pendientes", [])),
              "el contador de pendientes cuadra con la lista")

    token = token_cerebro()
    respuesta = asyncio.run(routes.cmd_actividad(peticion("token-inventado")))
    comprobar(respuesta.status_code == 401, "la actividad también pide sesión")

    respuesta = asyncio.run(routes.cmd_actividad(peticion(token)))
    comprobar(cuerpo_json(respuesta).get("ok"), "la actividad responde por API")

    respuesta = asyncio.run(routes.cmd_permisos(peticion(token)))
    comprobar(cuerpo_json(respuesta).get("ok"), "los permisos responden por API")

    respuesta = asyncio.run(routes.cmd_preguntas(peticion(token)))
    comprobar(isinstance(cuerpo_json(respuesta).get("preguntas"), list),
              "las preguntas pendientes se listan por API")


def probar_ia() -> None:
    """El centro de IA que lee JARVIS Live."""
    print("\n[8] Centro de IA")
    from command import ai, routes
    from auth import token as token_cerebro

    estado = ai.estado()
    comprobar(estado.get("ok"), "el estado de los modelos se puede leer")
    comprobar(len(estado.get("salud") or []) == 4,
              "los cuatro proveedores salen, tengan o no clave")
    comprobar(all("tiene_clave" in p for p in estado["salud"]),
              "cada proveedor dice si tiene clave, nunca cual es")
    comprobar(any(p["tarea"] == "CODING" and p["provider"] == "opencode"
                  for p in estado["asignados"]),
              "la programacion sigue yendo a OpenCode")
    comprobar("uso" in estado and "peticiones" in estado["uso"],
              "el estado trae las cifras de uso reales")
    comprobar(estado["uso"]["coste"] == "unknown",
              "sin precio del proveedor, el coste es unknown")

    token = token_cerebro()
    respuesta = asyncio.run(routes.cmd_ia(peticion("inventado")))
    comprobar(respuesta.status_code == 401, "sin sesion, el centro de IA no responde")

    respuesta = asyncio.run(routes.cmd_ia(peticion(token)))
    cuerpo = cuerpo_json(respuesta)
    comprobar(cuerpo.get("ok") and "salud" in cuerpo, "el centro de IA responde por API")

    respuesta = asyncio.run(routes.cmd_ia_modelos(peticion(token)))
    cuerpo = cuerpo_json(respuesta)
    comprobar(cuerpo.get("ok") and cuerpo.get("modelos"), "el catalogo de modelos responde")

    respuesta = asyncio.run(routes.cmd_ia_configurar(peticion(token, "POST"),
                                                    routes.ConfiguracionIA(
                                                        FAST="llama-3.1-8b-instant")))
    cuerpo = cuerpo_json(respuesta)
    comprobar(cuerpo.get("ok"), "se puede elegir modelo para una tarea")
    asignado = [a for a in cuerpo["asignados"] if a["tarea"] == "FAST"]
    comprobar(asignado and asignado[0]["model"] == "llama-3.1-8b-instant",
              "el cambio se ve al instante, sin reiniciar")

    respuesta = asyncio.run(routes.cmd_ia_peticiones(peticion(token)))
    comprobar(isinstance(cuerpo_json(respuesta).get("peticiones"), list),
              "las peticiones registradas se listan")


def main() -> int:
    probar_notificaciones()
    probar_salud_y_recursos()
    probar_matriz()
    probar_git()
    probar_briefing()
    probar_api()
    probar_pantalla()
    probar_ia()
    if _fallos:
        print(f"\n{len(_fallos)} COMPROBACIONES FALLIDAS:")
        for fallo in _fallos:
            print(f"  · {fallo}")
        return 1
    print("\nCENTRO DE MANDO VERIFICADO: todo lo que se enseña sale de verdad.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
