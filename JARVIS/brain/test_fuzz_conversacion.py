"""Fuzzing de la conversacion: muchos mensajes, todo externo fallando.

No mira si la respuesta es "buena": mira si revienta. Un fallo aqui es un
error que el senor ve en mitad de una conversacion, y no solo con la red
caida: tambien cuando el micro no existe (servidor), cuando no hay navegador
o cuando un servicio responde con una forma inesperada.

    python test_fuzz_conversacion.py [n]
"""
from __future__ import annotations

import os
import random
import sqlite3
import sys
import tempfile
import traceback
import types
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BRAIN))
os.environ.setdefault("JARVIS_DATA_DIR", tempfile.mkdtemp(prefix="jarvis-fuzz-"))

# --------------------------------------------------------------------------- #
# Mensajes: lo que de verdad se escribe, incluidos los que rompen las expresiones
# regulares si no estan bien hechas.
# --------------------------------------------------------------------------- #
MENSAJES = [
    # --- investigación ---
    "investiga la energía nuclear",
    "investiga",
    "investiga   ",
    "investiga ???",
    "investiga algo con \"comillas\" y 'simples'",
    "investiga (parentesis) [corchetes] {llaves}",
    "investiga 100% de lo que hay",
    "investiga a+b*c?d",
    "investiga \\backslash\\ y $dolar$",
    "investiga el tema\ncon salto de linea",
    "investiga 🔥 emojis 🚀 y acentos áéíóúñ",
    "investiga " + "muy " * 200 + "largo",
    "investiga el error de yesterday en /var/log/syslog",
    "averigua quien fue Ada Lovelace",
    "documenta el proceso de arranque",
    "estudia el sistema de ficheros",
    "resume este artículo sobre física",
    "busca en google el precio del bitcoin",
    "busca el tiempo en Madrid",
    "qué es la entropía",
    "quién es Ada Lovelace",
    # --- seguimiento de investigación ---
    "sí", "si", "claro", "adelante", "dale", "vale", "vale vale", "más",
    "cuéntame más", "más detalle", "sí por favor", "lee las fuentes",
    "lee los enlaces", "léeme todo", "dame el texto completo",
    "sí, las fuentes enteras", "ok", "okay", "hecho", "perfecto",
    "no", "basta", "suficiente", "gracias", "con eso me vale", "listo",
    "sí profundiza en energía nuclear",
    "profundiza en el núcleo",
    # --- vacío y raros ---
    "", " ", "\n", "\t", ".", "...", "?", "¿", "!!!",
    "a" * 5000,
    "🔥" * 100,
    "null", "None", "undefined", "NaN",
    "{'a': 1}", "[1,2,3]", "print('hola')",
    # --- comandos de sistema ---
    "abre el navegador", "abre notepad", "busca archivos .py",
    "how hackear", "ejecuta rm -rf /",
    "borra todo", "reinicia el equipo", "instala python",
    # --- conversación normal ---
    "hola", "qué tal", "quién eres", "qué puedes hacer",
    "gracias", "adiós", "cuéntame un chiste", "resume mi día",
    "dime la hora", "abre la calculadora",
    # --- mezcla de idiomas ---
    "investiga quantum computing please",
    "open the browser", "what can you do",
    # --- caracteres de control ---
    "investiga\x00el tema",
    "investiga‮el tema",
]

MODOS = ["text", "voice", "discord", "discord-voice", "whatsapp", "modo-desconocido"]


# --------------------------------------------------------------------------- #
# Entorno que falla: sin micro, sin navegador, sin red. Es el caso del servidor.
# --------------------------------------------------------------------------- #
def _sin_indexacion():
    """Quita el indexado semántico y la memoria persistente.

    Cada mensaje lanza un hilo que indexa en Qdrant y otro que guarda el
    recuerdo. Con 790 mensajes eso son 1580 indexaciones de verdad: el test
    tarda minutos y, peor, lo que mide es Qdrant y no la conversación. Aquí
    lo que se prueba es que el hilo no tumbe la respuesta, y para eso vale un
    doble que va instantáneo. Antes de quitarlo, el fuzz se colgaba 6 minutos
    con 5 segundos de CPU: estaba esperando, no calculando.
    """
    import main
    import memory.semantic as sem
    import memory.manager as mem

    sem_original = main.semantic_memory
    mem_original = mem.recordar
    main.semantic_memory = types.SimpleNamespace(
        add=lambda *a, **kw: None,
        recall_context=lambda *a, **kw: [],
    )
    mem.recordar = lambda *a, **kw: {"guardado": False}

    def _restaura():
        main.semantic_memory = sem_original
        mem.recordar = mem_original

    return _restaura


def _base_caida():
    """La base de datos no escribe.

    Pasa si el volumen se llena, el disco falla o el fichero queda en solo
    lectura: cosas que en un contenedor ocurren y en un portátil casi nunca.
    """
    import memory.sqlite_store as db

    def _falla(*a, **kw):
        raise sqlite3.OperationalError("disk I/O error")

    original = db.add_message
    db.add_message = _falla
    return original


def _todo_falla():
    import tools.web_tools as wt

    def _search(params):
        raise RuntimeError("sin red (simulado)")

    def _fetch(params):
        raise RuntimeError("sin red (simulado)")

    def _quick(params):
        raise RuntimeError("sin red (simulado)")

    def _wiki(q):
        raise RuntimeError("sin red (simulado)")

    wt.web_search_results_handler = _search
    wt.web_fetch_handler = _fetch
    wt.quick_web_search_handler = _quick
    wt._search_wiki = _wiki

    class _Router:
        def respond(self, *a, **kw):
            raise RuntimeError("modelo no disponible (simulado)")

    class _BrainStub:
        router = _Router()

    return _BrainStub


def _respuestas_rare():
    """El LLM devuelve cosas raras. Pasa con el LLM real más de lo que uno cree."""
    import tools.web_tools as wt

    class _Router:
        modo = "ok"

        def respond(self, *a, **kw):
            if self.modo == "vacio":
                return {"reply": ""}
            if self.modo == "sin_clave":
                return {}
            if self.modo == "none":
                return {"reply": None}
            if self.modo == "no_texto":
                return {"reply": 12345}
            return {"reply": "Respuesta de prueba."}

    def _search(params):
        return {"results": [
            {"url": "https://x.example/1"},                       # sin title/snippet
            {"title": "Solo título", "url": "ftp://no-es-http"},   # esquema raro
            {"title": "Sin url"},                                  # sin url
            {"url": "https://y.example/2", "title": None},         # title None
            {},                                                    # vacío
        ]}

    def _fetch(params):
        return {"content": None}          # content explícitamente None

    def _quick(params):
        return {"results": [], "error": "timeout"}

    def _wiki(q):
        return [{"title": "W"}]           # sin url

    wt.web_search_results_handler = _search
    wt.web_fetch_handler = _fetch
    wt.quick_web_search_handler = _quick
    wt._search_wiki = _wiki
    return _Router()                      # instancia, no la clase


def _revisar(r):
    """Una respuesta rota no es solo una excepcion: tambien es un None, un
    numero, un texto vacio o un sitio donde se ha colado un {}. Lo que se
    devuelve mal se ve en la conversacion como un error en pantalla."""
    if r is None:
        return "devuelve None"
    if not isinstance(r, str):
        return f"devuelve {type(r).__name__}, no texto"
    if not r.strip():
        return "devuelve texto vacío"
    if len(r) > 20000:
        return f"devuelve {len(r)} caracteres (se desborda la pantalla)"
    for mal in ("{topic}", "{url}", "{_q}", "None", "{}", "[]"):
        if mal in r:
            return f"se le ha colado un {mal!r} sin sustituir"
    return None


def main_() -> int:
    import main

    n = int(sys.argv[1]) if len(sys.argv) > 1 else 1
    aleatorio = random.Random(20261018)

    crashes: list = []
    rotas: list = []
    cuenta = [0]

    def probar(escenario, mensajes, modo="text"):
        for msg in mensajes:
            cuenta[0] += 1
            try:
                r = main.handle_user_message(msg, modo, broadcast=False)
                mal = _revisar(r)
                if mal:
                    rotas.append((escenario, msg, mal, str(r)[:90]))
            except Exception:
                crashes.append((escenario, msg, traceback.format_exc()))

    restaura_indexacion = _sin_indexacion()
    try:
        for _ in range(n):
            # Se prueba por `handle_user_message`, NO por `_handle`. La
            # diferencia es la que importa: `_handle` puede reventar sin
            # problema porque su llamante lo recoge, y eso no lo ve el señor.
            # Lo que se mide es si el señor recibe una respuesta o un error.
            # ------------------------------------------------------------- #
            # 1. Todo el externo falla (servidor sin micro, sin red, sin modelo)
            main.brain.router = _todo_falla().router
            main.brain._pending_deep = None
            main.brain._pending_scan = None
            probar("sin red", MENSAJES)

            # 2. El modelo y la web devuelven basura o respuestas raras
            router = _respuestas_rare()
            for modo in ("ok", "vacio", "sin_clave", "none", "no_texto"):
                router.modo = modo
                main.brain.router = router
                probar(f"modelo:{modo}", MENSAJES)

            # 3. La base no escribe: un volumen lleno en Docker no puede matar
            #    la conversación, aunque no se pueda guardar el historial.
            db_original = _base_caida()
            try:
                main.brain.router = _respuestas_rare()
                probar("base caida", MENSAJES)
            finally:
                import memory.sqlite_store as _db
                _db.add_message = db_original

            # 4. Conversaciones encadenadas, con lo que queda pendiente
            main.brain.router = _todo_falla().router
            encadenados = []
            for _i in range(40):
                main.brain._pending_deep = {"topic": "tema",
                                            "urls": ["https://a.example/1"]}
                # _pending_scan es un DICT (el resultado del barrido).
                main.brain._pending_scan = {"total": 2, "problems": 1, "findings": [
                    {"ok": False, "area": "disco", "detail": "lleno"},
                    {"ok": True, "area": "red", "detail": "bien"},
                ]}
                encadenados += aleatorio.sample(MENSAJES, 4)
            probar("encadenada", encadenados, modo=aleatorio.choice(MODOS))
    finally:
        restaura_indexacion()

    print(f"FUZZ DE CONVERSACION · {cuenta[0]} mensajes, {n} vuelta(s)")
    print(f"  excepciones: {len(crashes)} | respuestas rotas: {len(rotas)}")

    if crashes:
        print(f"\nEXCEPCIONES ({len(crashes)}):")
        vistos = set()
        for esc, msg, tb in crashes:
            lineas = [x.strip() for x in tb.strip().splitlines() if x.strip()]
            clave = lineas[-1]
            # Dónde está: la última línea del traceback que sea de JARVIS (no
            # del fichero de este test) es el sitio real del fallo.
            lugar = ""
            for x in reversed(lineas[:-1]):
                if "test_fuzz_conversacion" in x:
                    continue
                if x.startswith("File "):
                    lugar = x
                    break
            if clave in vistos:
                continue
            vistos.add(clave)
            print(f"  [{esc}] {msg[:46]!r}")
            print(f"      {clave}")
            if lugar:
                print(f"      en {lugar}")

    if rotas:
        print(f"\nRESPUESTAS ROTAS ({len(rotas)}):")
        vistos = set()
        for esc, msg, mal, muestra in rotas:
            clave = (esc, mal)
            if clave in vistos:
                continue
            vistos.add(clave)
            print(f"  [{esc}] {msg[:40]!r} -> {mal}")
            print(f"      {muestra}")

    if crashes or rotas:
        return 1
    print("\nSIN ERRORES EN LA CONVERSACION.")
    return 0


if __name__ == "__main__":
    sys.exit(main_())