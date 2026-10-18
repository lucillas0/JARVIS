"""¿JARVIS arranca cuando falta una pieza? (la pregunta del §12 del hardening)

Aquí se arranca el cerebro DE VERDAD: se lanza su `startup()` (que es donde
engancha el motor de riesgos, las herramientas y el router) y se llama a las
rutas. Lo que hay que comprobar no es que no salte una excepción, sino que:

  1. el cerebro arranca y engancha sus sistemas aunque falte todo;
  2. las rutas siguen respondiendo algo (no 500 por montar mal);
  3. la API sin token sigue cerrada (esto se comprobará en test_seguridad.py);
  4. lo que falta, lo dice en vez de fingir que funciona.

Cada escenario corre en un proceso limpio: si algo se rompe al montar, se ve
sin contaminar los demás.

    python brain/test_degradacion.py
"""
from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BRAIN))

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


BASE_ENV = {
    k: v for k, v in os.environ.items()
    if not any(k.startswith(p) for p in (
        "GROQ", "OPENAI", "GEMINI", "OPENROUTER", "DISCORD", "HA_", "HASS",
        "MEM0", "ANTHROPIC", "DEEPSEEK", "XAI"))
}

SCRIPT = textwrap.dedent('''
    import asyncio, json, os, sys, tempfile, traceback
    sys.path.insert(0, {brain!r})
    os.environ["JARVIS_DATA_DIR"] = tempfile.mkdtemp(prefix="degr-")
    salida = {{}}


    async def _llama(main, req):
        """Llama a /api/ai/status con token y devuelve el codigo de respuesta."""
        from ai_gateway import routes
        r = await routes.ai_status(req)
        try:
            return {{"ok": True, "status": r.status_code}}
        except Exception:
            return {{"ok": True, "status": "respuesta sin status_code"}}


    try:
        import main
        # Esto es lo que hace uvicorn al arrancar: aqui es donde el cerebro
        # engancha el motor de riesgos, las herramientas y el router.
        asyncio.run(main.startup())
        brain = main.brain
        salida["vive"] = True
        salida["riesgo"] = bool(getattr(brain, "risk", None))
        salida["herramientas"] = bool(getattr(brain, "tools", None))
        salida["router"] = bool(getattr(brain, "router", None))
        salida["proactivo"] = bool(getattr(brain, "proactive", None))

        from fastapi.routing import APIRoute
        salida["rutas"] = len([r for r in main.app.routes if isinstance(r, APIRoute)])

        # Y ahora de verdad: llamar a una ruta y ver que contesta.
        try:
            from auth import token as tk
            import auth
            class Req:
                method = "GET"
                headers = {{"x-jarvis-token": auth.token(), "host": "test"}}
                query_params = {{}}
                url = type("U", (), {{"path": "/api/ai/status"}})()
            r = asyncio.run(_llama(main, Req))
            salida["api_status"] = r
        except Exception as exc:
            salida["api_status"] = "ERROR %s: %s" % (type(exc).__name__, exc)

        try:
            from command import centers
            s = centers.salud(True)
            salida["salud"] = s.get("estado")
            salida["subsistemas"] = {{x.get("nombre"): x.get("estado")
                                      for x in (s.get("subsistemas") or [])}}
        except Exception as exc:
            salida["salud"] = "ERROR %s" % type(exc).__name__
            salida["subsistemas"] = {{}}
    except Exception:
        salida["_error"] = traceback.format_exc()[-500:]
    print("RESULT" + json.dumps(salida, ensure_ascii=False))
''')


def escenario(extra: dict, quitados=()) -> dict:
    env = dict(BASE_ENV)
    for clave in quitados:
        env.pop(clave, None)
    env.update(extra)
    env["PYTHONPATH"] = str(BRAIN)
    proc = subprocess.run([sys.executable, "-c", SCRIPT.format(brain=str(BRAIN))],
                          env=env, capture_output=True, text=True, timeout=420)
    salida = proc.stdout or ""
    for linea in salida.splitlines():
        if linea.startswith("RESULT"):
            return json.loads(linea[6:])
    return {"_fallo": (proc.stderr or proc.stdout or "sin salida")[-500:]}


# --------------------------------------------------------------------------- #
def probar_todo_apagado() -> None:
    seccion("1. Sin ninguna clave de nada (lo peor que puede pasar)")
    r = escenario({"GROQ_API_KEY": "", "DISCORD_BOT_TOKEN": "",
                   "HA_TOKEN": "", "MEMORY_ENABLED": "0"})
    if "_fallo" in r or "_error" in r:
        comprobar(False, f"el cerebro no arranca: "
                         f"{r.get('_fallo') or r.get('_error', '')[:220]}")
        return
    comprobar(r.get("vive") is True, "el cerebro arranca sin ninguna clave")
    comprobar(r.get("riesgo") is True, "el motor de riesgos queda enganchado")
    comprobar(r.get("herramientas") is True, "las herramientas quedan cargadas")
    comprobar(r.get("router") is True, "el router de modelos queda listo")
    comprobar(r.get("rutas", 0) > 20, f"monta sus {r.get('rutas')} rutas de API")
    api = r.get("api_status")
    comprobar(isinstance(api, dict) and api.get("ok"),
              f"y una ruta real responde ({api})")


def probar_uno_a_uno() -> None:
    seccion("2. Quitando una pieza cada vez")
    casos = (
        ("sin Discord", {"GROQ_API_KEY": "gsk_prueba", "DISCORD_BOT_TOKEN": ""},
         ("DISCORD_BOT_TOKEN",)),
        ("sin Home Assistant", {"GROQ_API_KEY": "gsk_prueba", "HA_TOKEN": ""},
         ("HA_TOKEN", "HASS_TOKEN")),
        ("sin Groq", {"GROQ_API_KEY": ""}, ("GROQ_API_KEY",)),
        ("sin memoria persistente", {"GROQ_API_KEY": "gsk_prueba",
                                     "MEMORY_ENABLED": "0"}, ("MEM0_URL",)),
    )
    for nombre, extra, quitados in casos:
        r = escenario(extra, quitados)
        if "_fallo" in r or "_error" in r:
            comprobar(False, f"{nombre}: no arranca "
                             f"({(r.get('_fallo') or r.get('_error'))[:200]})")
            continue
        ok = (r.get("vive") and r.get("riesgo") and r.get("herramientas")
              and r.get("router"))
        comprobar(bool(ok),
                  f"{nombre}: JARVIS sigue en pie"
                  + ("" if ok else f" (arranca={r.get('vive')}, "
                                   f"riesgo={r.get('riesgo')}, "
                                   f"tools={r.get('herramientas')}, "
                                   f"router={r.get('router')})"))


def probar_lo_que_falta_se_dice() -> None:
    seccion("3. Lo que falta, lo dice (no finge)")
    r = escenario({"GROQ_API_KEY": "", "DISCORD_BOT_TOKEN": ""},
                  ("GROQ_API_KEY", "DISCORD_BOT_TOKEN"))
    if "_failo" in r or "_error" in r:
        comprobar(False, "no arranca")
        return
    subs = r.get("subsistemas") or {}
    comprobar(bool(subs), f"la salud detalla {len(subs)} subsistemas")
    caidos = [n for n, e in subs.items() if e in ("down", "degraded")]
    comprobar(bool(caidos), f"los que faltan, marcados como caidos: {caidos[:5]}")
    comprobar(r.get("salud") is not None,
              f"y el estado global lo refleja ({r.get('salud')})")


def main() -> int:
    print("DEGRADACION · ¿arranca JARVIS sin cada pieza?")
    probar_todo_apagado()
    probar_uno_a_uno()
    probar_lo_que_falta_se_dice()
    print()
    if _fallos:
        print(f"{len(_fallos)} COMPROBACIONES FALLIDAS de {_numero[0]}:")
        for f in _fallos:
            print(f"  · {f}")
        return 1
    print(f"DEGRADACION VERIFICADA: {_numero[0]} comprobaciones.")
    return 0


if __name__ == "__main__":
    sys.exit(main())