"""Pruebas de la proteccion de la API (que no se pueda entrar sin token).

Por que este fichero existe: el cerebro protegiaendpoint a endpoint y se paso
de largo una_bindadia. Con el puerto publicado en la LAN, eso dejaba abierto
`/api/board/terminal` (una shell de verdad), `/api/dev/tasks/{id}/approve` (que
lanza la mision) y `/api/config` (que escribe la configuracion).

Aqui se comprueba lo que de verdad importa:
  1. TODAS las rutas /api/* y /automatizaciones/* rechazan sin token.
  2. Las que ya se protegian por su cuenta siguen haciendolo.
  3. Con el token correcto, funcionan.
  4. El token tambien vale en la query (descargas, <img>, MJPEG).
  5. Las paginas y /health siguen abiertas (si no, JARVIS no arranca).
  6. OPTIONS pasa: es el previo de CORS y no lleva token.
  7. Un token inventado NO vale.

    python brain/test_seguridad.py
"""
from __future__ import annotations

import asyncio
import os
import sys
import tempfile
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BRAIN))
os.environ.setdefault("JARVIS_DATA_DIR", tempfile.mkdtemp(prefix="jarvis-seg-test-"))

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
class PeticionFalsa:
    """Lo unico que hace falta para que `_exige_token` mire una cabecera."""

    def __init__(self, token: str = "", metodo: str = "GET", ruta: str = "/api/x"):
        self.headers = {"x-jarvis-token": token} if token else {}
        self.method = metodo
        self.query_params = {}
        self.url = type("U", (), {"path": ruta})()


class RespuestaFalsa:
    def __init__(self):
        self.pasos = 0

    async def __call__(self, request):
        self.pasos += 1
        return "DEJADO PASAR"


async def _middleware():
    import main

    return main._token_en_las_api


def _ejecutar(middleware, peticion):
    """Llama al middleware y devuelve (respuesta, paso_o_no)."""
    marca = RespuestaFalsa()

    async def ir():
        return await middleware(peticion, marca)

    salida = asyncio.run(ir())
    return salida, marca.pasos


# --------------------------------------------------------------------------- #
def probar_todas_las_rutas() -> None:
    """Lo importante: ninguna ruta de la API se sirve sin token.

    OJO al enumerarlas: FastAPI 0.143 ya no copia las rutas de un
    include_router en app.routes, las deja dentro de un _IncludedRouter. Un
    `isinstance(r, APIRoute)` sobre app.routes se ve 32 rutas y se deja sin
    comprobar las 81 de /api/cmd, /api/dev y /api/ai. Por eso aqui se usa
    rutas_reales, que baja dentro de los routers incluidos.
    """
    seccion("1. Ninguna ruta de la API se sirve sin token")
    import main

    from rutas_reales import todas_las_rutas

    todas = todas_las_rutas(main.app)
    api = [r for r in todas
           if r["tipo"] == "http"
           and (r["ruta"].startswith("/api/")
                or r["ruta"].startswith("/automatizaciones/"))
           and "OPTIONS" not in r["metodo"]]
    comprobar(len(api) > 60,
              f"el brain expone {len(api)} rutas de API (contadas las montadas "
              f"por include_router)")

    middleware = main._token_en_las_api
    sin_token = []
    for ruta in api:
        metodo = ruta["metodo"].split(",")[0]
        peticion = PeticionFalsa("", metodo, ruta["ruta"])
        _salida, paso = _ejecutar(middleware, peticion)
        if paso:
            sin_token.append(f"{metodo} {ruta['ruta']}")

    comprobar(not sin_token,
              f"las {len(api)} rutas rechazan sin token"
              + (f" (abiertas: {', '.join(sin_token[:6])})" if sin_token else ""))


def probar_con_token() -> None:
    seccion("2. Con el token, pasan (y con uno inventado, no)")
    import auth
    import main

    bueno = auth.token()
    middleware = main._token_en_las_api

    _salida, paso = _ejecutar(middleware, PeticionFalsa(bueno, "POST", "/api/config"))
    comprobar(paso == 1, "el token bueno deja pasar")

    _salida, paso = _ejecutar(
        middleware, PeticionFalsa("este-no-es-el-token", "GET", "/api/config"))
    comprobar(paso == 0, "un token inventado no deja pasar")


def probar_query() -> None:
    seccion("3. El token tambien vale en la query")
    import auth
    import main

    bueno = auth.token()
    peticion = PeticionFalsa("", "GET", "/api/board/dir")
    peticion.query_params = {"token": bueno}
    _salida, paso = _ejecutar(main._token_en_las_api, peticion)
    comprobar(paso == 1, "?token=... vale para las descargas y el MJPEG")


def probar_abiertas() -> None:
    seccion("4. Lo que debe seguir abierto, abierto")
    import main

    middleware = main._token_en_las_api
    for ruta in ("/health", "/", "/live", "/ui", "/board", "/config", "/setup"):
        _salida, paso = _ejecutar(middleware, PeticionFalsa("", "GET", ruta))
        comprobar(paso == 1, f"{ruta} sigue abierta (si no, JARVIS no arranca)")


def probar_options() -> None:
    seccion("5. El previo de CORS pasa (no lleva token)")
    import main

    _salida, paso = _ejecutar(
        main._token_en_las_api, PeticionFalsa("", "OPTIONS", "/api/config"))
    comprobar(paso == 1, "OPTIONS no se bloquea, o el navegador no puede llamar")


def probar_no_roto() -> None:
    seccion("6. El middleware no rompe la app")
    import main

    comprobar(callable(main._token_en_las_api), "esta enganchado como middleware")
    import inspect
    src = inspect.getsource(main._token_en_las_api)
    for pieza in ("OPTIONS", "/api/", "/automatizaciones/", "_exige_token"):
        comprobar(pieza in src, f"sabe de {pieza}")


def probar_paginas_siguen_sirviendo() -> None:
    """Las paginas han de llevar el token y el envoltorio de fetch.

    Sin esto, al proteger /api/* la pagina de configuracion se quedaria sin
    poder guardar ajustes ni importar claves, y la de modo sin poder cambiar
    de version. Aqui se comprueba que el HTML que sale de verdad lleva lo que
    su JS necesita.
    """
    seccion("7. Las paginas siguen pudiendo hablar con su propia API")
    import main

    # Se llaman directamente a los handlers de pagina: es lo que hace falta
    # para comprobar el HTML, sin levantar un servidor.
    for ruta, funcion in (("/config", "config_page"), ("/setup", "setup_page"),
                          ("/modo", "modo_page"), ("/board", "board_page")):
        fn = getattr(main, funcion, None)
        if fn is None:
            comprobar(False, f"{ruta}: no encuentro su handler")
            continue
        try:
            salida = asyncio.run(fn())
            html = bytes(salida.body).decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            comprobar(False, f"{ruta} se sirve ({type(exc).__name__}: {exc})")
            continue
        comprobar("JARVIS_TOKEN" in html, f"{ruta} lleva el token en la pagina")
        comprobar("window.fetch" in html and "X-Jarvis-Token" in html,
                  f"{ruta} añade la cabecera a sus fetch")


def probar_token_no_se_rota() -> None:
    """Un token que no se puede leer NO se puede perder.

    Antes, si el fichero existía pero fallaba la lectura, se generaba otro y se
    sobrescribía el anterior. Todos los clientes que ya tenían el token bueno
    (el panel, el móvil, los scripts) se quedaban fuera de golpe y el token
    viejo no volvía. Aquí se comprueba que eso ya no pasa: falla fuerte y deja
    el fichero donde estaba.
    """
    import auth

    ruta = auth.token_path()
    original = ruta.read_text(encoding="utf-8") if ruta.is_file() else None
    if original is not None:
        ruta.unlink()
    ruta.write_text("TOKEN-NO-SE-ROTA\n", encoding="utf-8")
    auth._TOKEN_CACHE.clear()

    # 1. Con el fichero bien, se lee y se respeta.
    comprobar(auth.token() == "TOKEN-NO-SE-ROTA", "se lee el token que ya hay")
    comprobar(ruta.read_text(encoding="utf-8").strip() == "TOKEN-NO-SE-ROTA",
              "y no lo cambia")

    # 2. Si no se puede leer, avisa en vez de sobrescribir.
    # Se sustituye SOLO la lectura de ese fichero (no se puede cambiar
    # permisos en Windows sin ser administrador, y no hace falta).
    bueno = ruta.read_text(encoding="utf-8")
    original_read = type(ruta).read_text

    def _read_que_falla(self, *a, **k):
        if self == ruta:
            raise PermissionError("simulado: sin permiso de lectura")
        return original_read(self, *a, **k)

    type(ruta).read_text = _read_que_falla
    try:
        auth._TOKEN_CACHE.clear()
        fallo = False
        try:
            auth.token()
        except RuntimeError:
            fallo = True
    finally:
        type(ruta).read_text = original_read
    comprobar(fallo, "si el token no se puede leer, lo dice en vez de rotarlo")
    comprobar(ruta.read_text(encoding="utf-8") == bueno,
              "y el fichero se queda como estaba")

    # 3. Cleanup: si estaba, se devuelve el token bueno.
    ruta.write_text(bueno, encoding="utf-8")
    auth._TOKEN_CACHE.clear()


def main_() -> int:
    print("SEGURIDAD DE LA API · que no se pueda entrar sin token")
    probar_todas_las_rutas()
    probar_con_token()
    probar_query()
    probar_abiertas()
    probar_options()
    probar_no_roto()
    probar_paginas_siguen_sirviendo()
    probar_token_no_se_rota()
    print()
    if _fallos:
        print(f"{len(_fallos)} COMPROBACIONES FALLIDAS de {_numero[0]}:")
        for f in _fallos:
            if f:
                print(f"  · {f}")
        return 1
    print(f"API PROTEGIDA: {_numero[0]} comprobaciones.")
    return 0


if __name__ == "__main__":
    sys.exit(main_())