"""JARVIS debe elegir un proveedor que OpenCode pueda usar DE VERDAD.

Todo lo de aquí sale de medir el contenedor con OpenCode 1.19.0, no de
suponer. Los tres peticiones que-costó-dinero:

1. OpenCode NO estaba sin proveedores. Lo seemed porque se preguntaba a
   `/api/provider`, que devuelve los proveedores *activos* y sin el
   parámetro `location` contesta lista vacía. La lista entera está en
   `/provider`: 232 proveedores.

2. «Conectado» NO quiere decir «con clave». Medido:

       proveedor  clave que pide        conectado  con clave  resultado
       opencode   OPENCODE_API_KEY       sí         NO         HTTP 403
       meta       META_MODEL_API_KEY      no         NO         -
       groq       GROQ_API_KEY            sí         SÍ         Unsupported API

3. Con Groq, aunque la clave esté bien, el turno NO se ejecuta. En el log de
   OpenCode:
       SessionRunnerModel.UnsupportedApiError: Unsupported API for
       groq/llama-3.3-70b-versatile: aisdk:@ai-sdk/groq
   El encargo se acepta y se queda mudo para siempre.

    python test_proveedores_opencode.py
"""
from __future__ import annotations

import sys
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BRAIN))

fallos: list = []


def comprobar(cond, texto):
    print(f"    {'· OK' if cond else 'FALLA'}  {texto}")
    if not cond:
        fallos.append(texto)
    return bool(cond)


# Catálogo tal y como lo contesta `GET /provider` en el contenedor. Se usa
# esta copia en vez de llamar a un servidor para que la prueba no dependa de
# que haya red ni de que haya un OpenCode arrancado.
CATALOGO = {
    "all": [
        {"id": "opencode", "key": False,
         "env": ["OPENCODE_API_KEY"],
         "models": {"space-bunny-free": {"cost": {"input": 0, "output": 0}},
                    "glm-5.3-flash-free": {"cost": {"input": 0, "output": 0}}}},
        {"id": "groq", "key": True,
         "env": ["GROQ_API_KEY"],
         "models": {"llama-3.3-70b-versatile": {"cost": {"input": 0.59, "output": 0.79}},
                    "allam-2-7b": {"cost": {"input": 0, "output": 0}}}},
        {"id": "meta", "key": False,
         "env": ["META_MODEL_API_KEY"],
         "models": {"muse-spark-1.3": {"cost": {"input": 1.25, "output": 4.25}},
                    "muse-spark-1.3-contributor": {"cost": {"input": 0.1, "output": 0.2}}}},
        {"id": "anthropic", "key": False,
         "env": ["ANTHROPIC_API_KEY"], "models": {}},
    ],
    "connected": ["opencode", "groq", "google"],
    "default": {"opencode": "big-pickle"},
}


class _ClienteFalso:
    def __init__(self, url="", username="", password="", timeout=0.0, **kwargs):
        self.url = url

    def get(self, ruta, params=None, timeout=None):
        # Importante: si alguien pregunta a `/api/provider` se devuelve la
        # lista VACÍA, como hace el OpenCode de verdad sin `location`.
        if ruta == "/api/provider":
            return []
        return CATALOGO


def _ClienteFalso_que_devuelve(catalogo):
    """Un cliente igual, pero que contesta el catálogo que se le pase."""
    def fabrica(url="", username="", password="", timeout=0.0, **kwargs):
        c = _ClienteFalso(url, username, password, timeout, **kwargs)
        c._cat = catalogo

        def get(ruta, params=None, timeout=None):
            return [] if ruta == "/api/provider" else c._cat

        c.get = get
        return c
    return fabrica


def main_() -> int:
    print("PROVEEDORES DE OPENCODE · que elija uno que funcione de verdad")

    import orchestrator.service as svc
    cliente_original = svc.OpenCodeClient
    try:
        return _comprobaciones(svc)
    finally:
        # Aunque reviente algo, OpenCodeClient se queda como estaba: si no,
        # el resto de la batería se encontraría un cliente de mentira.
        svc.OpenCodeClient = cliente_original


def _comprobaciones(svc) -> int:
    svc.OpenCodeClient = _ClienteFalso

    # --- 1. El catálogo se lee entero, no la lista de activos ---------
    cat = svc.proveedores("http://x")
    comprobar(cat.get("ok") is True, "lee el catálogo")
    comprobar(cat.get("total") == 4,
              f"y salen los 4 proveedores del catálogo (no 0): {cat.get('total')}")
    comprobar(sorted(cat.get("conectados") or []) == ["google", "groq", "opencode"],
              f"y sabe cuáles están conectados: {cat.get('conectados')}")

    # --- 2. Con la realidad de este contenedor: solo Groq tiene clave --
    el = svc.elegir_modelo("http://x")
    comprobar(el.get("ok") is False,
              "con SOLO la clave de Groq no elige nada (ese no funciona)")
    comprobar("OPENCODE_API_KEY" in str(el.get("error", "")),
              "y dice exactamente qué clave falta")
    comprobar("groq" in svc._SIN_SOPORTE,
              "Groq NO está descartado por ser popular, sino por estar medido "
              "que OpenCode 1.19 lo rechaza")

    # --- 3. Con la clave del Zen, elige el suyo y gratis ---------------
    cat_zen = {**CATALOGO,
               "all": [p if p["id"] != "opencode" else {**p, "key": True}
                       for p in CATALOGO["all"]]}
    svc.OpenCodeClient = _ClienteFalso_que_devuelve(cat_zen)
    el = svc.elegir_modelo("http://x")
    comprobar(el.get("ok") is True, "con OPENCODE_API_KEY sí elige")
    comprobar(el.get("proveedor") == "opencode",
              f"y elige el de OpenCode: {el.get('proveedor')}")
    comprobar(el.get("gratis") is True, "y elige uno gratis")
    comprobar((el.get("ref") or {}).get("providerID") == "opencode"
              and (el.get("ref") or {}).get("id"),
              f"y lo devuelve con la forma que quiere OpenCode: {el.get('ref')}")

    # --- 4. Con clave de Meta, elige Muse Spark ------------------------
    cat_meta = {**CATALOGO,
                "all": [p if p["id"] != "meta" else {**p, "key": True}
                        for p in CATALOGO["all"]]}
    svc.OpenCodeClient = _ClienteFalso_que_devuelve(cat_meta)
    el = svc.elegir_modelo("http://x")
    comprobar(el.get("proveedor") == "meta",
              f"con META_MODEL_API_KEY elige Meta (Muse Spark): {el.get('proveedor')}")
    comprobar(str(el.get("modelo", "")).startswith("muse-spark"),
              f"y el modelo es de Muse Spark: {el.get('modelo')}")

    # --- 5. Aunque haya clave de Groq, no la elige por delante ---------
    cat_ambos = {**CATALOGO,
                 "all": [p if p["id"] != "opencode" else {**p, "key": True}
                         for p in CATALOGO["all"]]}
    svc.OpenCodeClient = _ClienteFalso_que_devuelve(cat_ambos)
    el = svc.elegir_modelo("http://x")
    comprobar(el.get("proveedor") == "opencode",
              "con Groq y Zen puestos, manda el de OpenCode, no el de Groq")

    # --- 6. La forma del modelo ---------------------------------------
    print()
    for texto, esperado in (
            ("opencode/space-bunny-free",
             {"id": "space-bunny-free", "providerID": "opencode"}),
            ("groq/llama-3.3-70b-versatile",
             {"id": "llama-3.3-70b-versatile", "providerID": "groq"}),
            ("big-pickle", {"id": "big-pickle"}),
            ("", {}), (None, {})):
        got = svc.formatear_modelo(texto)
        comprobar(got == esperado, f"formatear_modelo({texto!r}) -> {got}")

    print()
    if fallos:
        print(f"{len(fallos)} COMPROBACIONES FALLIDAS:")
        for f in fallos:
            print(f"  · {f}")
        return 1
    print("JARVIS ELIGE UN PROVEEDOR QUE OPENCODE PUEDA USAR.")
    return 0


if __name__ == "__main__":
    sys.exit(main_())
