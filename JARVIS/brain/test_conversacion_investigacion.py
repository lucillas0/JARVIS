"""Prueba conversaciones de investigacion de verdad, sin red y sin modelo.

No simula que el codigo este bien: llama a `_handle` de verdad y lo que
importa es que salte una excepcion o que la conversacion se rompa. Las
llamadas a internet y al modelo se sustituyen por dobles que DEVUELVEN algo
(falla si un doble devuelve algo que el codigo no espera) o LANZAN un error
como lo haria la red.

    python test_conversacion_investigacion.py
"""
from __future__ import annotations

import os
import sys
import tempfile
import traceback
import types
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BRAIN))
os.environ.setdefault("JARVIS_DATA_DIR", tempfile.mkdtemp(prefix="jarvis-conv-"))

_fallos: list = []
_caso_num = [0]


def comprobar(condicion, texto):
    print(f"    · {'OK' if condicion else 'FALLA'}  {texto}")
    if not condicion:
        _fallos.append(texto)
    return bool(condicion)


# --------------------------------------------------------------------------- #
# Dobles
# --------------------------------------------------------------------------- #
class _Respuesta:
    """Lo que devuelve la web de mentira: una pagina real de 5000 caracteres."""

    def __init__(self, largo=5000):
        self.largo = largo

    def get(self, k, default=None):
        return {"content": ("Detalle del tema. " * (self.largo // 20))}.get(k, default)


def _instalar_dobles(fallo_web=False):
    """Sustituye web_tools y el router por dobles sin red."""
    import tools.web_tools as wt

    llamadas = {"search": 0, "fetch": 0, "wiki": 0, "router": 0}

    def search(params):
        llamadas["search"] += 1
        if fallo_web:
            raise RuntimeError("red caida (simulado)")
        return {"results": [
            {"url": "https://a.example/1", "title": "Fuente A", "snippet": "sobre el tema"},
            {"url": "https://b.example/2", "title": "Fuente B", "snippet": "mas del tema"},
            {"url": "https://c.example/3", "title": "Fuente C", "snippet": "y mas"},
        ]}

    def fetch(params):
        llamadas["fetch"] += 1
        if fallo_web:
            raise RuntimeError("no se pudo leer (simulado)")
        return _Respuesta()

    def quick(params):
        llamadas["search"] += 1
        if fallo_web:
            raise RuntimeError("plazo agotado (simulado)")
        return {"results": [{"url": "https://q.example/1", "title": "R",
                             "snippet": "rapido"}]}

    def wiki(topic):
        llamadas["wiki"] += 1
        return [{"url": "https://es.wikipedia.org/x", "title": "Wiki"}]

    wt.web_search_results_handler = search
    wt.web_fetch_handler = fetch
    wt.quick_web_search_handler = quick
    wt._search_wiki = wiki

    class _Router:
        # Deliberadamente permisivo: si el doble exigiera una firma concreta,
        # un fallo del DOBLE parecería un fallo de JARVIS.
        def respond(self, *a, **kw):
            llamadas["router"] += 1
            mensajes = a[0] if a else kw.get("messages") or []
            if isinstance(mensajes, list):
                for m in mensajes:
                    if not isinstance(m, dict):
                        raise AssertionError(
                            f"mensaje que NO es un diccionario: {type(m).__name__}")
                    if "content" in m and not isinstance(m.get("content"), str):
                        raise AssertionError(
                            f"content que no es texto: {type(m['content']).__name__}")
            return {"reply": "Resumen de prueba, señor."}

    return llamadas, _Router


# --------------------------------------------------------------------------- #
# Conversaciones completas
# --------------------------------------------------------------------------- #
def _conversa(nombre, pasos, fallo_web=False):
    """Aplica una lista de mensajes en orden, como una conversación."""
    import main

    llamadas, router = _instalar_dobles(fallo_web=fallo_web)
    main.brain.router = router
    main.brain._pending_deep = None
    main.brain._pending_scan = None
    outs = []
    errores = []
    for paso in pasos:
        _caso_num[0] += 1
        try:
            r = main._handle(paso, "text")
            outs.append(r if isinstance(r, str) else f"<{type(r).__name__}>")
        except Exception:
            errores.append((paso, traceback.format_exc()))
    print(f"  [{nombre}]")
    for paso, err in errores:
        print(f"      ENTRADA: {paso!r}")
        # Solo las últimas líneas del traceback: el archivo y la línea dicen
        # dónde está el fallo de verdad.
        utiles = [x.strip() for x in err.strip().splitlines() if x.strip()]
        for linea in utiles[-4:]:
            print(f"      {linea}")
    return outs, errores, llamadas


def probar_investigar_y_profundizar():
    outs, errs, _ = _conversa("investigar -> si -> profundizar", [
        "investiga la energia nuclear",
        "si",
    ])
    comprobar(not errs, "investigar y decir «si» no revienta")
    comprobar(bool(outs) and all(isinstance(o, str) and o.strip() for o in outs),
               "y devuelve texto en los dos turnos")
    if len(outs) >= 2:
        comprobar("?" in outs[0] or "quiere" in outs[0].lower(),
                  f"el primer turno ofrece profundizar: {outs[0][:70]!r}")


def probar_leer_fuentes():
    outs, errs, _ = _conversa("investigar -> lee las fuentes", [
        "investiga la energia nuclear",
        "lee las fuentes",
    ])
    comprobar(not errs, "pedir las fuentes enteras no revienta")
    if len(outs) >= 2:
        comprobar("http" in outs[1], f"el segundo turno trae enlaces: {outs[1][:70]!r}")


def probar_no():
    outs, errs, _ = _conversa("investigar -> no", [
        "investiga la energia nuclear",
        "no",
    ])
    comprobar(not errs, "decir «no» no revienta")
    if len(outs) >= 2:
        comprobar("servido" in outs[1].lower() or "otra" in outs[1].lower(),
                  f"contesta cerrando el tema: {outs[1][:70]!r}")


def probar_sin_red():
    outs, errs, _ = _conversa("sin red: investigar y profundizar", [
        "investiga la energia nuclear",
        "si",
    ], fallo_web=True)
    comprobar(not errs, "sin red, no revienta (solo debe decirlo)")


def probar_variantes():
    """Las formas que de verdad escribe la gente al contestar."""
    for sufijo in ["sí", "si", "claro", "adelante", "dale", "vale", "más",
                   "sí por favor", "lee las fuentes", "lee los enlaces",
                   "cuéntame más", "sí, las fuentes enteras", "ok",
                   "no", "basta", "suficiente", "gracias", "con eso me vale"]:
        _conversa(f"contestar: {sufijo!r}", ["investiga la energia nuclear", sufijo])


def probar_sale_en_espanol():
    """La respuesta se la lee el señor: si se le cuela una palabra en otro
    idioma, lo ve. Antes el primer turno de una investigación empezaba por
    'He investigated', en inglés, dentro de una frase en español."""
    outs, errs, _ = _conversa("investigar: el idioma de la respuesta", [
        "investiga la energia nuclear",
    ])
    comprobar(not errs, "la investigación responde sin reventar")
    if outs:
        r = outs[0]
        for mala in ("He investigated", "I've", "I have", "Here is", "Sorry"):
            comprobar(mala not in r, f"no dice {mala!r} en inglés: {r[:60]!r}")
        comprobar("Sí," in r or "Sí ," not in r,
                  f"y no pierde el espacio tras «Sí,»: {r[:60]!r}")


def probar_contestar_con_numero():
    """Contestar con un número a una pregunta pendiente.

    La expresión se llamaba `_numero` pero se había declarado `_lock_numero`.
    Respondiendo "2" salía un NameError que el llamante se comía en silencio:
    el permiso se quedaba pendiente para siempre y JARVIS no contestaba nada.
    """
    from orchestrator import inbox

    pregunta = {"question": "¿Sigo?",
                "options": [{"label": "Sí", "value": "si"},
                            {"label": "No", "value": "no"},
                            {"label": "Cancelar", "value": "cancelar"}]}
    for texto, esperado in (("2", "no"), ("1", "si"), ("3", "cancelar"),
                            ("2)", "no"), (" 2 ", "no")):
        try:
            ok, valor = inbox._coincide(texto, pregunta)
        except Exception as exc:  # noqa: BLE001
            comprobar(False, f"contestar {texto!r} no revienta "
                             f"({type(exc).__name__}: {exc})")
            continue
        comprobar(ok is True and valor == esperado,
                  f"contestar {texto!r} elige {esperado!r} (dio {valor!r})")


def probar_sin_investigacion_previa():
    """Contestar «si» sin que haya investigación previa: no debe romper."""
    outs, errs, _ = _conversa("si sin nada pendiente", ["si", "claro", "no"])
    comprobar(not errs, "un «si» suelto, sin investigación pendiente, no revienta")


def main_() -> int:
    print("CONVERSACION DE INVESTIGACION · sin red y sin modelo")
    probar_investigar_y_profundizar()
    probar_leer_fuentes()
    probar_no()
    probar_sin_red()
    probar_sale_en_espanol()
    probar_contestar_con_numero()
    probar_sin_investigacion_previa()
    probar_variantes()

    print()
    if _fallos:
        print(f"{len(_fallos)} COMPROBACIONES FALLIDAS:")
        for f in _fallos:
            print(f"  · {f}")
        return 1
    print("CONVERSACIONES DE INVESTIGACION ESTABLES.")
    return 0


if __name__ == "__main__":
    sys.exit(main_())