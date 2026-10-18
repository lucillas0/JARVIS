"""Cuando los modelos fallan, hay que decir POR QUÉ, no solo que fallaron.

Por qué existe este fichero: un mensaje de error decía

    "He intentado con groq y los tres han fallado [...] Mire el registro del
     servidor y ya lo veré."

y esa frase tenía tres defectos que solo se ven cuando se repite en casa:

  1. Decía "los tres" con la palabra escrita a mano, pero `probados` es una
     lista dinámica. Con una sola clave puesta la frase era falsa.
  2. "groq-light" y "groq" son el MISMO servicio, y salían como dos.
  3. El motivo estaba a una línea de distancia: cada proveedor lo escribía en
     el log y luego se guardaba solo el nombre. Por eso la única cosa que
     podía decir era "mire el registro", que no es un diagnóstico sino
     devolverle el problema al que acaba de preguntar.

Y además: si el AI Gateway devuelve un fallo en vez de lanzar excepción, el
camino viejo —que existe para eso— no se probaba nunca.

    python test_fallo_modelos.py
"""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BRAIN))
os.environ.setdefault("JARVIS_DATA_DIR", tempfile.mkdtemp(prefix="jarvis-modelos-"))

fallos: list = []


def comprobar(cond, texto):
    print(f"    {'· OK' if cond else 'FALLA'}  {texto}")
    if not cond:
        fallos.append(texto)
    return bool(cond)


def probar_el_mensaje():
    from model_router import _mensaje_sin_modelos as mensaje

    print("1. El mensaje dice la verdad")

    # Una sola clave: el mensaje viejo decía "los tres".
    r = mensaje(["groq-light", "groq"], {"Groq": "sin cuota (límite de la API)"}, True)
    comprobar("tres" not in r, f"no inventa un número de proveedores: {r[:80]!r}")
    comprobar("groq-light" not in r,
              f"y no cuenta groq-light como otro proveedor: {r[:80]!r}")
    comprobar("Groq" in r and "Groq" in r.count("Groq") * "Groq",
              f"nombra el proveedor: {r[:80]!r}")

    # Con clave mala tiene que decirlo y sugerir qué hacer.
    r = mensaje(["groq"], {"Groq": "la clave no es válida o está caducada"}, True)
    comprobar("clave" in r, f"dice que es la clave: {r[:90]!r}")
    comprobar("/setup" in r, "y dice dónde se arregla")

    # Sin cuota: otra causa, otra pista.
    r = mensaje(["groq"], {"Groq": "sin cuota (límite de la API), esperando 30 s"}, True)
    comprobar("cuota" in r, f"distingue la cuota de la clave: {r[:90]!r}")
    comprobar("/setup" not in r, "y no manda a /setup si no es cosa de claves")

    # Los tres de verdad: cuenta bien y da el detalle de cada uno.
    r = mensaje(["groq", "gemini", "openrouter"],
                {"Groq": "sin cuota (límite de la API)",
                 "Gemini": "la clave no es válida o está caducada"}, True)
    comprobar("Groq, Gemini y OpenRouter" in r,
              f"los enumera con comas y «y»: {r[:110]!r}")
    comprobar("Groq:" in r and "Gemini:" in r,
              "y da el motivo de cada uno por separado")

    # Sin red, lo dice.
    r = mensaje(["groq", "gemini"], {}, False)
    comprobar("internet" in r, f"avisa de que no hay salida a internet: {r[:100]!r}")

    # Sin la lista de claves de API: el camino corto, que no es este.
    r = mensaje([], {}, True)
    comprobar("ningún modelo" in r, f"caso sin proveedores: {r[:80]!r}")


def probar_que_no_inventa_causas():
    from model_router import _mensaje_sin_modelos as mensaje

    print("\n2. No inventa nada que no sepa")
    # Sin motivo conocido: no debe inventar uno.
    r = mensaje(["groq"], {}, True)
    comprobar("Groq:" not in r,
              f"sin motivo registrado no inventa ninguno: {r[:90]!r}")
    comprobar("registro" in r,
              "y dice dónde mirar en vez de suponer")


def probar_red_de_seguridad():
    """Si el gateway falla, tiene que probarse el camino viejo."""
    print("\n3. El gateway no es el final del camino")

    import model_router as mr

    router = mr.ModelRouter()

    class _Gateway:
        def instancia(self, _router=None):
            return self

        def chat(self, *a, **kw):
            # Lo que devuelve el gateway cuando los proveedores fallan: un
            # resultado con ok=False y SIN texto.
            return {"ok": False, "reply": "", "provider": "none",
                    "fallback": True}

    # Se falsea el import del gateway para que devuelva el fallo.
    import ai_gateway.gateway as gw_real
    original = gw_real.instancia
    gw_real.instancia = _Gateway().instancia
    try:
        llamado = {"n": 0}

        def _legacy(*a, **kw):
            llamado["n"] += 1
            return {"reply": "Respuesta por el camino viejo.",
                    "provider": "legacy", "fallback": True}

        router._respond_legacy = _legacy
        r = router.respond([{"role": "user", "content": "hola"}])
        comprobar(llamado["n"] == 1,
                  f"un gateway con ok=False SÍ pasa al camino viejo "
                  f"(lo llamó {llamado['n']} vez/veces)")
        comprobar(r.get("reply") == "Respuesta por el camino viejo.",
                  f"y la respuesta es la del camino viejo: {r.get('reply')!r}")
    finally:
        gw_real.instancia = original


def probar_no_toca_el_exito():
    """Con buena respuesta, nada cambia: no se llama al camino viejo."""
    print("\n4. Cuando va bien, no se toca nada")

    import ai_gateway.gateway as gw_real
    import model_router as mr

    router = mr.ModelRouter()

    class _Gateway:
        def instancia(self, _router=None):
            return self

        def chat(self, *a, **kw):
            return {"ok": True, "reply": "Va bien.", "provider": "groq"}

    original = gw_real.instancia
    gw_real.instancia = _Gateway().instancia
    try:
        llamado = {"n": 0}

        def _legacy(*a, **kw):
            llamado["n"] += 1
            return {"reply": "no debería llegar aquí"}

        router._respond_legacy = _legacy
        r = router.respond([{"role": "user", "content": "hola"}])
        comprobar(r.get("reply") == "Va bien.",
                  f"con ok=True devuelve la del gateway: {r.get('reply')!r}")
        comprobar(llamado["n"] == 0, "y no llama al camino viejo por nada")
    finally:
        gw_real.instancia = original


def main_() -> int:
    print("FALLO DE MODELOS · el mensaje dice la causa y hay red de seguridad")
    probar_el_mensaje()
    probar_que_no_inventa_causas()
    probar_red_de_seguridad()
    probar_no_toca_el_exito()

    print()
    if fallos:
        print(f"{len(fallos)} COMPROBACIONES FALLIDAS:")
        for f in fallos:
            print(f"  · {f}")
        return 1
    print("FALLO DE MODELOS CORRECTAMENTE EXPLICADO.")
    return 0


if __name__ == "__main__":
    sys.exit(main_())