"""Decision Engine: qué hay que hacer con una acción según su riesgo.

**Memoria no es decisión.** Este módulo NO decide si algo es bueno o malo: eso
lo hace el Risk Engine (brain/risk_engine.py). Aquí se traduce ese nivel a una
**política de ejecución**:

    AUTONOMOUS    se hace sola, sin preguntar
    LOW_RISK      se hace, dejando constancia
    USER_APPROVAL hay que preguntar antes
    CRITICAL      no se hace nunca sin confirmación expresa, y se avisa

Por qué existe como módulo aparte y no como un `if` dentro del motor de
riesgos: porque el Risk Engine responde "¿qué tan peligrosa es esta acción?" y
esta pieza responde "¿qué hago con ella?". Son preguntas distintas, y la
segunda también la necesita el planner (para etiquetar los pasos de un plan)
y el orquestador (para decidir si un paso del plan separa al usuario).

No duplica permisos: cuando la decisión no es AUTONOMOUS, delega en el
Permission Manager, que es quien pregunta de verdad. Aquí solo se traduce.

    python brain/decision_engine.py     # ejemplos y comprobaciones
"""
from __future__ import annotations

import logging
from typing import Optional

log = logging.getLogger("brain.decision")

# --------------------------------------------------------------------------- #
# Las cuatro decisiones
# --------------------------------------------------------------------------- #
AUTONOMOUS = "AUTONOMOUS"
LOW_RISK = "LOW_RISK"
USER_APPROVAL = "USER_APPROVAL"
CRITICAL = "CRITICAL"

DECISIONES = (AUTONOMOUS, LOW_RISK, USER_APPROVAL, CRITICAL)

# Qué nivel de riesgo lleva a cada decisión. El orden importa: se recorre de
# más grave a menos, y el primero que encaja gana.
POLITICA = (
    (CRITICAL, "critical"),
    (USER_APPROVAL, "high"),
    (LOW_RISK, "medium"),
    (AUTONOMOUS, "low"),
)

# Niveles que no son del motor de riesgo (el planner usa "alto/medio/bajo").
TRADUCCION = {
    "alto": "high", "media": "medium", "medio": "medium", "bajo": "low",
    "critico": "critical", "crítico": "critical", "sensible": "high",
}


def normalizar_riesgo(riesgo: str) -> str:
    """Deja cualquier forma de escribir el riesgo en low/medium/high/critical.

    Un riesgo que no se reconoce NO se traduce a "low": se queda como
    'desconocido', que la política trata como USER_APPROVAL. Si aqui se
    autoconcediera, bastaría con escribir mal el nivel en una herramienta
    nueva para que esa herramienta se ejecutara sin preguntar.
    """
    r = (riesgo or "").strip().lower()
    if r in ("", "low", "bajo"):
        return "low"
    return TRADUCCION.get(r, r if r in ("low", "medium", "high", "critical")
                          else "desconocido")


# --------------------------------------------------------------------------- #
def decidir(riesgo: str, *, contexto: str = "") -> dict:
    """Qué hacer con una acción de ese riesgo.

    Devuelve un diccionario (no solo la palabra) porque quien lo llama
    necesita saber también si puede seguir solo y por qué:

        {"decision": "USER_APPROVAL", "nivel": "high", "puede_solo": False,
         "motivo": "...", "requiere_confirmacion": True}
    """
    nivel = normalizar_riesgo(riesgo)
    # Un riesgo desconocido no encaja en ninguna fila de la política: se cae
    # directamente a preguntar, que es lo prudente.
    if nivel not in ("low", "medium", "high", "critical"):
        return {"decision": USER_APPROVAL, "nivel": "desconocido", "puede_solo": False,
                "requiere_confirmacion": True,
                "motivo": f"Riesgo no reconocido ({riesgo!r}): se pregunta."}
    for decision, nivel_min in POLITICA:
        if nivel == nivel_min:
            requiere = decision in (USER_APPROVAL, CRITICAL)
            return {
                "decision": decision,
                "nivel": nivel,
                "puede_solo": not requiere,
                "requiere_confirmacion": requiere,
                "motivo": _motivo(decision, nivel, contexto),
            }
    # Aquí solo se llega con un riesgo que no es ninguno de los cuatro.
    return {"decision": USER_APPROVAL, "nivel": nivel, "puede_solo": False,
            "requiere_confirmacion": True,
            "motivo": f"Nivel de riesgo desconocido ({riesgo}): se pregunta."}


def _motivo(decision: str, nivel: str, contexto: str) -> str:
    donde = f" en {contexto}" if contexto else ""
    if decision == AUTONOMOUS:
        return f"Riesgo bajo{donde}: JARVIS puede hacerlo solo."
    if decision == LOW_RISK:
        return f"Riesgo medio{donde}: se hace, pero queda registrado."
    if decision == USER_APPROVAL:
        return f"Riesgo alto{donde}: hay que preguntar antes de hacerlo."
    return (f"Riesgo crítico{donde}: no se hace nada sin que el señor lo confirme "
            f"y se avisa siempre.")


def requiere_aprobacion(riesgo: str) -> bool:
    """True si esta acción no puede ejecutarse sin preguntar."""
    return decidir(riesgo)["requiere_confirmacion"]


def puede_actuar_solo(riesgo: str) -> bool:
    return decidir(riesgo)["puede_solo"]


def etiquetar_plan(riesgos: list[dict]) -> dict:
    """Resume el plan entero: si un solo paso necesita al señor, lo dice arriba.

    Un plan con 8 pasos en los que 1 necesita aprobación no es "autónomo":
    es un plan que necesita una decisión. Por eso se cuenta antes de decidir.
    """
    decisiones = {}
    for riesgo in riesgos or []:
        nivel = riesgo.get("nivel") or riesgo.get("nivel_riesgo") or riesgo.get("riesgo")
        d = decidir(nivel)
        decisiones[d["decision"]] = decisiones.get(d["decision"], 0) + 1
    la_mas_grave = next((d for d in (CRITICAL, USER_APPROVAL, LOW_RISK, AUTONOMOUS)
                        if d in decisiones), AUTONOMOUS)
    return {
        "por_decision": decisiones,
        "total": len(riesgos or []),
        "requiere_al_senor": decisiones.get(CRITICAL, 0) > 0
        or decisiones.get(USER_APPROVAL, 0) > 0,
        "decision_global": la_mas_grave,
    }


# --------------------------------------------------------------------------- #
if __name__ == "__main__":
    print("DECISION ENGINE · comprobaciones")
    fallos = 0
    for riesgo, esperado in (("low", AUTONOMOUS), ("medium", LOW_RISK),
                             ("high", USER_APPROVAL), ("critical", CRITICAL),
                             ("sensitive", USER_APPROVAL), ("bajo", AUTONOMOUS),
                             ("alto", USER_APPROVAL), ("inventado", USER_APPROVAL)):
        d = decidir(riesgo)
        ok = d["decision"] == esperado
        fallos += 0 if ok else 1
        print(f"  {'OK ' if ok else 'MAL'} {riesgo:10} -> {d['decision']:14}"
              f" {d['motivo']}")
    plan = etiquetar_plan([{"nivel": "low"}, {"nivel": "medium"}, {"nivel": "high"}])
    print(f"\n  plan con 3 riesgos -> {plan}")
    print(f"\n{'TODO CORRECTO' if not fallos else f'{fallos} FALLOS'}")