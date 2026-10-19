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
import time
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


def probar_modelo_alterno():
    """Si Groq dice «sin cuota», se prueba OTRO modelo suyo antes de rendirse.

    Por qué: el límite de Groq es POR MODELO, no de la cuenta entera. Medido
    con la clave real: con `openai/gpt-oss-120b` ya limitado por tokens por
    minuto, `qwen/qwen3.8-27b` contestaba sin problema en el mismo instante.

    Y la lista de modelos hay que pedírsela a Groq, porque la escrita a mano
    está caduca: esta clave ya no tiene `llama-3.3-70b-versatile` ni
    `llama-3.1-8b-instant`, y sí `qwen/qwen3.8-27b`.
    """
    print("\n5. Sin cuota se prueba otro modelo del mismo proveedor")

    import model_router as mr

    router = mr.ModelRouter()

    from ai_gateway.providers import groq as _groq_api
    original_listar = _groq_api.listar_modelos
    original_completar = _groq_api.completar
    _groq_api.listar_modelos = lambda timeout=8.0: [
        {"id": "openai/gpt-oss-120b"}, {"id": "qwen/qwen3.8-27b"},
        {"id": "openai/gpt-oss-20b"}, {"id": "whisper-large-v3"},
        {"id": "meta-llama/llama-prompt-guard-2-22m"},
    ]

    try:
        alterno = router._modelo_alterno_groq("openai/gpt-oss-120b")
        comprobar(alterno == "qwen/qwen3.8-27b",
                  f"elige el más grande que no sea de voz: {alterno!r}")
        comprobar("whisper" not in alterno and "guard" not in alterno,
                  "y no escoge ni el de voz ni el de vigilancia")
        comprobar(router._modelo_alterno_groq("qwen/qwen3.8-27b") != "",
                  "y si el alternativo también está ocupado, propone otro")

        from ai_gateway.errors import AIError, RATE_LIMITED

        pedidos = []

        def _completar(mensajes, modelo, **kw):
            pedidos.append(modelo)
            if modelo == "openai/gpt-oss-120b":
                raise AIError(RATE_LIMITED, "sin cuota", "groq")
            return {"texto": "Respuesta del modelo alterno."}

        _groq_api.completar = _completar
        mr._GROQ_COOLDOWN = 0.0
        mr._GROQ_PENALTY = 0
        r = router._chat_groq([{"role": "user", "content": "hola"}])
        comprobar(r == "Respuesta del modelo alterno.",
                  f"responde con el otro modelo: {r!r}")
        comprobar(len(pedidos) >= 2,
                  f"y lo intenta: {pedidos[:3]}")
        comprobar("qwen/qwen3.8-27b" in pedidos,
                  f"y el segundo intento es el alterno: {pedidos[:3]}")
    finally:
        _groq_api.listar_modelos = original_listar
        _groq_api.completar = original_completar
        mr._GROQ_COOLDOWN = 0.0
        mr._GROQ_PENALTY = 0


def probar_espera_enfriamiento():
    """Si Groq está enfriándose pero hay tiempo, se espera y se reintenta.

    Por qué: medido en el escritorio, con el enfriamiento puesto la cuarta
    pregunta seguida se rendía con «Groq y Gemini fallaron», y un minuto
    después Groq contestaba sin problema. Es peor tardar medio minuto que no
    contestar nada.
    """
    print("\n6. Con Groq enfriándose pero con tiempo, se espera y se reintenta")

    import model_router as mr

    router = mr.ModelRouter()
    from ai_gateway.providers import groq as _groq_api

    original_completar = _groq_api.completar
    original_listar = _groq_api.listar_modelos
    _groq_api.listar_modelos = lambda timeout=8.0: [
        {"id": "openai/gpt-oss-120b"}, {"id": "qwen/qwen3.8-27b"}]

    try:
        # Groq está en enfriamiento y a punto de liberarse.
        mr._GROQ_COOLDOWN = time.time() + 1.0
        mr._GROQ_PENALTY = 0
        mr._DEADLINE = time.time() + 30.0

        llamadas = []

        def _completar(mensajes, modelo, **kw):
            llamadas.append(modelo)
            return {"texto": "Respuesta tras esperar."}

        _groq_api.completar = _completar
        router._groq_esperado = False
        r = router._chat_groq([{"role": "user", "content": "hola"}])
        comprobar(r == "Respuesta tras esperar.",
                  f"contesta tras esperar al enfriamiento: {r!r}")
        comprobar(len(llamadas) == 1, f"y lo intenta una vez: {llamadas}")

        # Sin reloj (o sin tiempo) NO espera: se rinde, que es lo de antes.
        mr._DEADLINE = 0.0
        mr._GROQ_COOLDOWN = time.time() + 60.0
        router._groq_esperado = False
        llamadas.clear()
        r = router._chat_groq([{"role": "user", "content": "hola"}])
        comprobar(r is None, "sin reloj no espera: se rinde")
        comprobar(llamadas == [], f"y ni siquiera lo intenta: {llamadas}")
    finally:
        _groq_api.completar = original_completar
        _groq_api.listar_modelos = original_listar
        mr._DEADLINE = 0.0
        mr._GROQ_COOLDOWN = 0.0
        mr._GROQ_PENALTY = 0


def main_() -> int:
    print("FALLO DE MODELOS · el mensaje dice la causa y hay red de seguridad")
    probar_el_mensaje()
    probar_que_no_inventa_causas()
    probar_red_de_seguridad()
    probar_no_toca_el_exito()
    probar_modelo_alterno()
    probar_espera_enfriamiento()

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
