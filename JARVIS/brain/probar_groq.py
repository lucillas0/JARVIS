"""Comprueba si la clave de Groq sirve de verdad, y con qué modelos.

No imprime la clave ni nada que la contenga. Si la clave no vale, lo dice
claro, porque eso es justo lo que el señor necesita saber y lo que antes solo
podía encontrar en el registro del servidor.

    python probar_groq.py
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BRAIN))
os.environ.setdefault("JARVIS_DATA_DIR", tempfile.mkdtemp(prefix="jarvis-groq-"))

import config  # noqa: E402

CLAVE = config.Config.GROQ_API_KEY
BASE = config.Config.GROQ_BASE_URL or "https://api.groq.com/openai/v1"

fallos: list = []


def comprobar(cond, texto):
    print(f"    {'· OK' if cond else 'FALLA'}  {texto}")
    if not cond:
        fallos.append(texto)
    return bool(cond)


def llamar(ruta: str, cuerpo: dict | None = None, timeout: int = 25,
           clave: str | None = None):
    datos = json.dumps(cuerpo).encode() if cuerpo is not None else None
    peticion = urllib.request.Request(
        f"{BASE}{ruta}", datos,
        {"Authorization": f"Bearer {clave if clave is not None else CLAVE}",
         "Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(peticion, timeout=timeout) as r:
            return 200, json.loads(r.read() or "{}")
    except urllib.error.HTTPError as e:
        crudo = e.read() or b""
        try:
            return e.code, json.loads(crudo)
        except Exception:
            return e.code, {"texto": crudo[:200].decode("utf-8", "replace")}
    except Exception as e:
        return 0, {"texto": f"{type(e).__name__}: {e}"}


def main_() -> int:
    print("CLAVE DE GROQ · sirve de verdad?")
    if not CLAVE:
        print("    FALLA  no hay ninguna clave puesta")
        return 1
    # Solo longitud y principio: suficiente para detectar un recorte sin
    # mostrar el secreto.
    print(f"    (clave de {len(CLAVE)} caracteres, empieza por "
          f"{CLAVE[:4]}...)")

    # --- 1. La clave vale? (modelos disponibles) ------------------------
    codigo, datos = llamar("/models")

    # Antes de culpar a la clave, se compara con una inventada. Un 403 igual
    # con cualquier clave (incluso sin ninguna) NO es un problema de clave: es
    # que desde aquí no se puede llegar a Groq. Es Cloudflare, y es justo lo
    # que pasa en una máquina sin salida, en un contenedor con la DNS mal o
    # detrás de un proxy. Distinguirlo ahorra la mitad de las salidas a por el
    # registro del servidor persiguiendo una clave que no tiene nada malo.
    codigo_falso, cuerpo_falso = llamar("/models", clave="gsk_inventada_000")

    if codigo == 200:
        comprobar(True, "la clave es válida: el servidor responde 200")
        modelos = [m.get("id") for m in (datos.get("data") or [])]
        print(f"    modelos disponibles: {len(modelos)}")
        for m in sorted(x for x in modelos if x)[:10]:
            print(f"      - {m}")
    elif codigo == codigo_falso and codigo != 0:
        print(f"    la clave buena da {codigo} y una clave INVENTADA también "
              f"da {codigo_falso}.")
        cuerpo = str(datos.get("texto", ""))[:120]
        if "1010" in cuerpo or "error code: 1010" in cuerpo:
            comprobar(False,
                      "NO es la clave: Cloudflare bloquea a esta máquina "
                      f"(código 1010). Groq no ha visto la clave todavía.")
            print("    Qué hacer:")
            print("      · si JARVIS corre en Docker, ejecuta esto DENTRO "
                  "del contenedor:")
            print("          python brain/probar_groq.py")
            print("      · si lo ejecutas en el portátil y da 403, el problema "
                  "es la red de esa máquina, no la clave.")
        else:
            comprobar(False,
                      f"NO es la clave: desde aquí no se llega al servicio "
                      f"(HTTP {codigo} idéntico sin clave).")
        return 1
    elif codigo in (401, 403):
        comprobar(False, f"la clave está rechazada ({codigo}) mientras una "
                         f"clave falsa da {codigo_falso}: "
                         f"es la clave, no la red")
        err = (datos.get("error") or {}).get("message", "")
        print(f"    dice: {str(err)[:200]}")
        return 1
    elif codigo == 0:
        comprobar(False, f"no se pudo ni conectar: "
                         f"{str(datos.get('texto'))[:160]}")
        return 1
    else:
        comprobar(False, f"respuesta inesperada ({codigo})")
        return 1

    # --- 2. Y que el modelo configurado esté entre ellos ----------------
    for etiqueta, nombre in (("GROQ_MODEL_FAST", config.Config.GROQ_MODEL_FAST),
                             ("GROQ_DEFAULT_MODEL",
                              getattr(config.Config, "GROQ_DEFAULT_MODEL", "")),
                             ("GROQ_MODEL_LIGHT",
                              getattr(config.Config, "GROQ_MODEL_LIGHT", ""))):
        if not nombre:
            continue
        ok = nombre in modelos
        comprobar(ok, f"{etiqueta} = {nombre} "
                      f"{'está disponible' if ok else 'NO está en la lista'}")

    # --- 3. Y que una llamada funcione de verdad -------------------------
    codigo, datos = llamar("/chat/completions", {
        "model": config.Config.GROQ_MODEL_FAST,
        "messages": [{"role": "user", "content": "Di hola en español."}],
        "max_tokens": 40,
    })
    if codigo == 200:
        texto = (((datos.get("choices") or [{}])[0].get("message") or {})
                 .get("content") or "")
        comprobar(bool(texto.strip()), "una llamada real devuelve texto")
        print(f"    respuesta del modelo: {texto[:90]!r}")
    else:
        err = datos.get("error") or {}
        comprobar(False, f"la llamada falló ({codigo}): "
                         f"{str(err.get('message', datos.get('texto')))[:180]}")

    print()
    if fallos:
        print(f"{len(fallos)} COMPROBACIONES FALLIDAS:")
        for f in fallos:
            print(f"  · {f}")
        return 1
    print("LA CLAVE DE GROQ FUNCIONA.")
    return 0


if __name__ == "__main__":
    sys.exit(main_())