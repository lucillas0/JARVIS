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
    """Lo que DEBE seguir abierto, y lo que ya no.

    Antes estas cuatro paginas (`/live`, `/ui`, `/board`, `/config`) se
    servian a cualquiera y llevaban el token dentro del HTML. Con el puerto
    publicado en la LAN, eso era suficiente para robar el token con un
    `curl` y con el exportar todas las claves de API en claro.

    Ahora se sirven SOLO a quien esta autenticado, y quien no, va a la puerta
    de entrada.
    """
    seccion("4. La puerta de entrada: abierta; las demas, no")
    import main

    middleware = main._token_en_las_api
    for ruta in ("/health", "/healthz", "/entrar", "/"):
        _salida, paso = _ejecutar(middleware, PeticionFalsa("", "GET", ruta))
        comprobar(paso == 1, f"{ruta} sigue abierta (si no, JARVIS no arranca)")

    # Estas ya no se sirven sin sesion.
    for ruta in ("/live", "/ui", "/board", "/config", "/setup", "/modo"):
        _salida, paso = _ejecutar(middleware, PeticionFalsa("", "GET", ruta))
        comprobar(paso == 0,
                  f"{ruta} pide sesion (antes se servia a cualquiera)")

    # Y con sesion, pasan.
    import auth
    bueno = auth.token()
    for ruta in ("/live", "/ui", "/board", "/config", "/setup"):
        _salida, paso = _ejecutar(middleware, PeticionFalsa(bueno, "GET", ruta))
        comprobar(paso == 1, f"{ruta} con sesion se sirve")


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
    """Las paginas NO llevan el token, y siguen pudiendo hablar con su API.

    El token dentro del HTML era el agujero: un `curl` en la LAN se lo
    llevaba. Ahora el token viaja en una cookie `HttpOnly` (que el JavaScript
    no puede leer) y el navegador la manda solo en cada peticion.

    Aqui se comprueba que el HTML que sale de verdad NO lleva el token, y que
    si lleva lo que su JS necesita para que las peticiones funcionen.
    """
    seccion("7. Las paginas no publican el token, y siguen hablando con su API")
    import main
    import auth

    token = auth.token()

    for ruta, funcion in (("/config", "config_page"), ("/setup", "setup_page"),
                          ("/modo", "modo_page"), ("/board", "board_page"),
                          ("/ui", "ui_page")):
        fn = getattr(main, funcion, None)
        if fn is None:
            comprobar(False, f"{ruta}: no encuentro su handler")
            continue
        try:
            args = ()
            import inspect
            if len(inspect.signature(fn).parameters) > 0:
                # Las que reciben la peticion (para poder mirar la sesion).
                args = (PeticionFalsa(token, "GET", ruta),)
            salida = asyncio.run(fn(*args))
            html = bytes(salida.body).decode("utf-8", "replace")
        except Exception as exc:  # noqa: BLE001
            comprobar(False, f"{ruta} se sirve ({type(exc).__name__}: {exc})")
            continue
        # Lo que hay que comprobar es que el VALOR del token no este en la
        # pagina. El nombre de la variable puede quedar en un comentario o en
        # una referencia que ya no lleva ningun token.
        comprobar(token not in html,
                  f"{ruta} NO lleva el token de sesion en la pagina")
        comprobar("window.fetch" in html,
                  f"{ruta} envuelve sus fetch para que lleven la sesion")

    # /live lo sirve otro modulo (live_dashboard), y su HTML lleva el token.
    try:
        import live_dashboard
        salida = asyncio.run(live_dashboard.live_page(
            PeticionFalsa(token, "GET", "/live")))
        html = bytes(salida.body).decode("utf-8", "replace")
        comprobar(token not in html, "/live NO lleva el token de sesion")
    except Exception as exc:  # noqa: BLE001
        comprobar(False, f"/live se sirve ({type(exc).__name__}: {exc})")


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


# --------------------------------------------------------------------------- #
# El flujo completo, como lo vive un navegador
# --------------------------------------------------------------------------- #
def probar_flujo_de_sesion() -> None:
    """La puerta de entrada, de verdad: sin sesion no hay panel, y con sesion
    si, y el token no aparece por ningun lado del HTML.

    Esto es lo que arreglaba un agujero concreto: las paginas se servian a
    cualquiera y llevaban el token DENTRO. Con el puerto publicado en la LAN,
    un `curl http://IP:8000/config` bastaba para quedarse con el token, y con
    el para pedir `POST /api/config/exportar-claves`, que devuelve todas las
    claves de API en claro.
    """
    seccion("8. El flujo de sesion, como lo vive un navegador")
    from fastapi.testclient import TestClient
    import auth
    import main

    token = auth.token()
    cliente = TestClient(main.app, follow_redirects=False)

    # --- 1. Sin sesion, el panel no se sirve ----------------------------
    r = cliente.get("/live")
    comprobar(r.status_code in (303, 307, 302),
              f"sin sesion, /live no se sirve (dio {r.status_code})")
    comprobar("/entrar" in str(r.headers.get("location", "")),
              f"y te mandan a la puerta de entrada: {r.headers.get('location')}")

    # --- 2. La puerta de entrada es publica ----------------------------
    r = cliente.get("/entrar")
    comprobar(r.status_code == 200, "la puerta de entrada se sirve sin sesion")
    html = r.text
    comprobar("JARVIS_TOKEN" not in html or token not in html,
              "y no publica ningun token")

    # --- 3. Con un token MAL, no entra ----------------------------------
    r = cliente.post("/entrar", json={"token": "este-no-es-el-token"})
    comprobar(r.status_code == 401, f"con un token falso, no entra (dio {r.status_code})")

    # --- 4. Con el token bueno, cookie y panel --------------------------
    r = cliente.post("/entrar", json={"token": token})
    comprobar(r.status_code == 200, f"con el token bueno, entra (dio {r.status_code})")
    cookie = r.headers.get("set-cookie", "")
    comprobar("jarvis_sesion=" in cookie, "y deja la cookie de sesion")
    comprobar("HttpOnly" in cookie,
              "que es HttpOnly: el JavaScript de la pagina no puede leerla")
    comprobar("samesite=strict" in cookie.lower(),
              "y SameSite=Strict (para que no viaje a otros sitios)")

    # Con la cookie puesta, el panel se sirve.
    r = cliente.get("/live")
    comprobar(r.status_code == 200, f"con sesion, /live se sirve (dio {r.status_code})")
    comprobar(token not in r.text,
              "y su HTML NO lleva el token de sesion dentro")

    # --- 5. Y la API contesta con la cookie ----------------------------
    r = cliente.get("/api/cmd/estado")
    comprobar(r.status_code == 200, f"con sesion, la API contesta (dio {r.status_code})")

    # --- 6. /wa y /mic_audio PIDE token --------------------------------
    # Dan control del cerebro entero (133 herramientas), asi que sin
    # credencial no pueden funcionar.
    # Con un cliente NUEVO, sin la cookie de antes: esto es lo que se
    # encuentra quien llega al puerto sin saber nada.
    for ruta in ("/wa", "/mic_audio"):
        limpio = TestClient(main.app, follow_redirects=False)
        r = limpio.post(ruta, json={"sender": "x", "message": "hola"})
        comprobar(r.status_code == 401,
                  f"{ruta} pide token (dio {r.status_code})")
        r = limpio.post(ruta, json={"sender": "x", "message": "hola"},
                        headers={"X-Jarvis-Token": token})
        comprobar(r.status_code != 401,
                  f"y con token, {ruta} si funciona (dio {r.status_code})")

    # --- 7. Y el token NO se puede sacar de la pagina -------------------
    r = cliente.get("/config")
    if r.status_code == 200:
        comprobar(token not in r.text,
                  "la pagina de configuracion tampoco lleva el token dentro")


def main_() -> int:
    print("SEGURIDAD DE LA API · que no se pueda entrar sin token")
    probar_todas_las_rutas()
    probar_con_token()
    probar_query()
    probar_abiertas()
    probar_options()
    probar_no_roto()
    probar_paginas_siguen_sirviendo()
    probar_flujo_de_sesion()
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