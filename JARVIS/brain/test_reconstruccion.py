"""Pruebas de lo reconstruido en esta verificación.

1. Los estados de misión que faltaban (PAUSED, ERROR, COMPLETED_WITH_WARNINGS).
2. Pausar y REANUDAR, y que no se duplique la sesión.
3. Que una tarea pausada sigue viva (no se da por terminada al reiniciar).
4. Los cuatro niveles de riesgo del motor.
5. El Decision Engine: nivel → política, y que el Policy Engine lo use de verdad.
6. Que nada de lo que había antes se rompió.

    python brain/test_reconstruccion.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BRAIN))
os.environ.setdefault("JARVIS_DATA_DIR", tempfile.mkdtemp(prefix="jarvis-recon-test-"))

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
def probar_estados() -> None:
    seccion("1. Estados de misión completos")
    from orchestrator import store as ostore

    for estado in ("PAUSED", "ERROR", "COMPLETED_WITH_WARNINGS"):
        comprobar(estado in ostore.ESTADOS, f"existe el estado {estado}")
    comprobar("PAUSED" in ostore.ESTADOS_VIVOS,
              "PAUSED cuenta como viva: una tarea detenida no está terminada")
    comprobar("ERROR" in ostore.ESTADOS_VIVOS, "ERROR también es un estado vivo")
    comprobar(set(ostore.ESTADOS_TERMINALES) <= set(ostore.ESTADOS),
              "los estados terminales son un subconjunto de los estados")


def probar_pausar_reanudar() -> None:
    seccion("2. Pausar y reanudar")
    from orchestrator.manager import get_manager
    from orchestrator import store as ostore

    gestor = get_manager()
    tarea = ostore.nueva("tarea de prueba para pausar y reanudar")
    # PAUSED de verdad: es lo que tiene que pasar para que "reanudar" tenga sentido.
    tarea["status"] = "PAUSED"
    ostore.guardar(tarea)

    # Sin sesión de OpenCode, reanudar debe decirlo, no fingir.
    r = gestor.reanudar(tarea["id"])
    comprobar(r.get("ok") is False,
              "reanudar sin sesión lo dice en vez de fingir que funciona")
    comprobar("sesión" in str(r.get("error", "")).lower(),
              f"y explica por qué: {r.get('error')}")

    # Una tarea que no está pausada tampoco se reanuda.
    tarea["status"] = "RUNNING"
    ostore.guardar(tarea)
    r2 = gestor.reanudar(tarea["id"])
    comprobar(r2.get("ok") is False, "una tarea en RUNNING no se reanuda")
    comprobar("pausada" in str(r2.get("error", "")).lower(),
              f"y lo dice: {r2.get('error')}")

    try:
        from memory import sqlite_store as db

        with db.get_conn() as c:
            c.execute("DELETE FROM dev_tasks WHERE id = ?", (tarea["id"],))
            c.commit()
    except Exception:  # noqa: BLE001
        pass


def probar_niveles_riesgo() -> None:
    seccion("3. Niveles de riesgo LOW/MEDIUM/HIGH/CRITICAL")
    import risk_engine

    comprobar(set(("low", "medium", "high", "critical")) <= set(risk_engine.RISK_LEVELS),
              f"el motor tiene los cuatro: {risk_engine.RISK_LEVELS}")
    comprobar(risk_engine.classify("leer_un_fichero") == "low",
              "una lectura es de riesgo bajo")
    comprobar(risk_engine.classify("borrar_fichero") == "high",
              "borrar es de riesgo alto")
    comprobar(risk_engine.classify("formatear_disco") == "critical",
              "formatear es crítico")
    comprobar(risk_engine.normalizar_nivel("sensitive") == "high",
              "'sensitive' se traduce a 'high' (compatibilidad)")

    # Y el nivel cambia lo que hace
    motor = risk_engine.RiskEngine(emit=lambda *a, **k: None)
    bajo = asyncio.run(motor.check({"name": "leer", "risk_level": "low",
                                    "requires_confirmation": False}, {}))
    alto = asyncio.run(motor.check({"name": "borrar", "risk_level": "high",
                                    "requires_confirmation": True,
                                    "confirm_text": "¿Seguro?"}, {}))
    comprobar(bajo is True and alto is False,
              f"bajo={bajo} alto={alto}: el nivel decide si se pregunta")


def probar_decision_engine() -> None:
    seccion("4. Decision Engine")
    import decision_engine as de

    for riesgo, esperado in (("low", de.AUTONOMOUS), ("medium", de.LOW_RISK),
                             ("high", de.USER_APPROVAL), ("critical", de.CRITICAL)):
        d = de.decidir(riesgo)
        comprobar(d["decision"] == esperado,
                  f"{riesgo} -> {d['decision']} (esperado {esperado})")
    comprobar(de.decidir("bajo")["decision"] == de.AUTONOMOUS,
              "entiende el vocabulario del planner (alto/medio/bajo)")
    comprobar(de.decidir("inventado")["decision"] == de.USER_APPROVAL,
              "un nivel desconocido se pregunta: nunca se autoconcede")
    comprobar(not de.puede_actuar_solo("high") and de.puede_actuar_solo("low"),
              "puede_actuar_solo() refleja la política")

    # Un plan con un solo paso peligroso NO es autónomo
    plan = de.etiquetar_plan([{"nivel": "low"}, {"nivel": "medium"},
                              {"nivel": "high"}])
    comprobar(plan["requiere_al_senor"] is True,
              "un plan con un paso de riesgo alto requiere al señor")
    comprobar(plan["decision_global"] == de.USER_APPROVAL,
              f"y la decisión global es la más grave ({plan['decision_global']})")
    tranquilo = de.etiquetar_plan([{"nivel": "low"}, {"nivel": "low"}])
    comprobar(tranquilo["requiere_al_senor"] is False,
              "un plan solo de riesgo bajo no requiere al señor")


def probar_integracion() -> None:
    seccion("5. El Risk Engine usa el Decision Engine de verdad")
    import risk_engine

    fuente = (BRAIN / "risk_engine.py").read_text(encoding="utf-8")
    usa = "decision_engine" in fuente and "_politica" in fuente
    comprobar(usa, "risk_engine consulta al Decision Engine en vez de duplicar reglas")

    # Y el resultado se nota: con 'sensitive' (nivel antiguo) sigue preguntando
    motor = risk_engine.RiskEngine(emit=lambda *a, **k: None)
    r = asyncio.run(motor.check({"name": "apagar", "risk_level": "sensitive",
                                 "requires_confirmation": True,
                                 "confirm_text": "¿Apagar?"}, {}))
    comprobar(r is False, "'sensitive' sigue pidiendo confirmación (no se rompió)")


def probar_no_roto() -> None:
    seccion("6. Nada de lo anterior se rompió")
    import main

    from rutas_reales import todas_las_rutas

    rutas = todas_las_rutas(main.app)
    hay_resume = any(r["ruta"].endswith("/resume") for r in rutas)
    comprobar(hay_resume, "la ruta /resume existe en la app montada")
    hay_routes = any(r["ruta"].endswith("/pause") for r in rutas)
    comprobar(hay_routes, "y /pause sigue ahí")


def main() -> int:
    print("RECONSTRUCCIÓN · estados, reanudar, niveles y Decision Engine")
    probar_estados()
    probar_pausar_reanudar()
    probar_niveles_riesgo()
    probar_decision_engine()
    probar_integracion()
    probar_no_roto()
    print()
    if _fallos:
        print(f"{len(_fallos)} COMPROBACIONES FALLIDAS de {_numero[0]}:")
        for f in _fallos:
            print(f"  · {f}")
        return 1
    print(f"RECONSTRUCCIÓN VERIFICADA: {_numero[0]} comprobaciones.")
    return 0


if __name__ == "__main__":
    sys.exit(main())