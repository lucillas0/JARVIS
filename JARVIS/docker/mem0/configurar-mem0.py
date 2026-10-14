#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Configura Mem0 para usar Gemini, ANTES de que el servidor arranque.

POR QUE ESTE SCRIPT EXISTE
-------------------------
El servidor de Mem0 construye su configuracion en el momento de importarse
(``initialize_state(DEFAULT_CONFIG)``, server/main.py linea 143), y su config
por defecto es OpenAI::

    "llm":      {"provider": "openai", "config": {"api_key": OPENAI_API_KEY, ...}}
    "embedder": {"provider": "openai", "config": {"api_key": OPENAI_API_KEY, ...}}

Sin OPENAI_API_KEY no falla "mas tarde": peta al arrancar con
``openai.OpenAIError: Missing credentials``, el contenedor sale con codigo 1,
y jamas llega a escuchar ningun puerto. No hay ninguna ventana para mandarle un
POST /configure desde fuera.

Pero si hay una ventana por otro lado: ``initialize_state`` llama antes a
``_load_overrides()``, que lee una fila de la tabla ``settings`` con clave
``config_overrides`` y la funde sobre la config por defecto con una fusion
profunda. Si esa fila dice "gemini", la de OpenAI no llega a instanciarse
nunca.

Asi que este script siembra esa fila. No modifica ni una linea del codigo de
Mem0: solo escribe un dato que su propio servidor ya sabe leer.

Idempotente: si la fila ya dice gemini, no se toca. Si el senor ha cambiado
algo a mano desde POST /configure, no se pisa; solo se rellenan huecos.

Ademas cubre una trampa del embedder: lee GOOGLE_API_KEY, no GEMINI_API_KEY.
Con la clave solo en la de GEMINI, guardar funciona y buscar falla con un error
de autenticacion que no menciona ninguna de las dos. Por eso el compose pone la
misma clave en las dos variables.

LA CLAVE QUEDA EN LA BASE
-------------------------
El LLM de Gemini no lee la clave del entorno (a diferencia del embedder, que si
usa GOOGLE_API_KEY), asi que la clave tiene que ir dentro de la config, y la
config se guarda en la tabla settings de postgres. Es el diseno de Mem0: su
propio POST /configure hace exactamente eso. Traducido: la clave esta en el
volumen mem0_postgres y sale en los pg_dump. Si molesta, en el .env poner una
clave de Gemini distinta de la que usa JARVIS para chatear.

EL MODELO, Y POR QUE NO ES EL DE MAS MODA
------------------------------------------
El LLM de mem0 tiene una sola labor: leer un par de frases y sacar de ahi los
hechos que merecen recordarse. No redacta, no razona, no conversa. Asi que
vale cualquier modelo pequeno y barato, y aqui interesa mas que responda.

El defecto es "gemini-flash-lite-latest", y no por ser el mejor, sino porque es
de los pocos que responden con una clave normal. Comprobado con la clave del
señor, uno por uno:

    gemini-2.5-flash              404  no disponible para cuentas nuevas
    gemini-2.0-flash              404  idem
    gemini-3.6-flash              429  cuota agotada
    gemini-3.5 / 3.7 / 3.8 flash  503  no disponible ahora
    gemini-flash-latest           503  no disponible ahora
    gemini-flash-lite-latest      OK
    gemini-3-flash-preview        OK

O sea: casi ninguno. Si mem0 responde con un 502, lo primero que hay que hacer
es cambiar MEM0_GEMINI_LLM por otro de los que responden. No es un fallo de
esto: es que las claves gratuitas de Gemini son muy cambiantes y los limites van por
modelo, no por cuenta.
"""
import json
import os
import sys

MODELO_LLM = os.environ.get("MEM0_GEMINI_LLM", "gemini-flash-lite-latest")
MODELO_EMBEDDER = os.environ.get("MEM0_GEMINI_EMBEDDER", "models/gemini-embedding-001")


def log(msg):
    print(f"[configurar-mem0] {msg}", flush=True)


def _url() -> str:
    return (
        f"postgresql://{os.environ.get('POSTGRES_USER', 'postgres')}"
        f":{os.environ.get('POSTGRES_PASSWORD', 'postgres')}"
        f"@{os.environ.get('POSTGRES_HOST', 'postgres')}"
        f":{os.environ.get('POSTGRES_PORT', '5432')}"
        f"/{os.environ.get('APP_DB_NAME', 'mem0_app')}"
    )


def _conectar(psycopg, intentos=30):
    """Conecta esperando a que postgres este.

    La conexion se devuelve ABIERTA y se cierra con cx.close() al final. No se
    usa "with psycopg.connect(...)" aqui dentro del bucle: al hacer break se
    sale del bucle pero el __exit__ se ejecuta igualmente y cierra la conexion,
    y todo lo que viene despues falla con "the connection is closed", sin decir
    nada de cual fue el problema.
    """
    ultimo = ""
    for intento in range(intentos):
        try:
            return psycopg.connect(_url(), connect_timeout=5, autocommit=True)
        except Exception as e:
            ultimo = str(e)
            import time
            time.sleep(2)
    log(f"no he podido conectar con postgres tras {intentos} intentos: {ultimo}")
    return None


def _dimensiones_de_la_columna(cur,forzado: str = ""):
    """Cuantas dimensiones tiene de verdad la columna de vectores.

    Esto no es cosmetico. Alembic crea la tabla "memories" con la dimension
    que espera el proveedor POR DEFECTO, que es la de OpenAI: 1536. Gemini
    devuelve 3072. Y mem0 pide al embedder lo que pone en la config.

    Con los tres distintos, el error es:

        psycopg.errors.DataException: expected 1536 dimensions, not 768

    que llega al usuario como un 502 de mem0, sin decir nada de dimensiones.
    Se perdia el rato mirando llaves y quota cuando el problema era un numero.

    Asi que en vez de poner un 768 de oido, se PREGUNTA a postgres y se pide
    exactamente lo que cabe. Si alguien cambia la columna, esto se entera solo.
    """
    if forzado:
        return int(forzado), "el .env"
    try:
        cur.execute(
            "SELECT a.atttypmod FROM pg_attribute a "
            "JOIN pg_class c ON c.oid = a.attrelid "
            "JOIN pg_namespace n ON n.oid = c.relnamespace "
            "WHERE c.relname = 'memories' AND a.attname = 'vector' "
            "AND NOT a.attisdropped"
        )
        fila = cur.fetchone()
        if fila and fila[0]:
            return int(fila[0]), "la columna vector de postgres"
    except Exception as e:
        print(f"[configurar-mem0] no he podido leer la dimension: {e}", flush=True)
    return 768, "el valor por defecto (no se ha podido leer la columna)"


def main() -> int:
    clave = (os.environ.get("GEMINI_API_KEY")
             or os.environ.get("GOOGLE_API_KEY")
             or os.environ.get("GOOGLE_GENAI_API_KEY") or "").strip()

    if not clave:
        # Se deja como estaba. El error que dará despues (Missing credentials
        # de OpenAI) es el de siempre y dice algo util; uno nuestro sobre
        # "no hay clave" seria mas corto pero menos claro de por que.
        log("sin GEMINI_API_KEY: se queda el proveedor por defecto (openai).")
        log("Con eso Mem0 no arrancara. Pon la clave en el .env y reinicia.")
        return 0

    try:
        import psycopg
    except ImportError as e:                        # pragma: no cover
        log(f"no hay psycopg ({e}); el requirements lo trae, esto no deberia pasar.")
        return 1

    cx = _conectar(psycopg)
    if cx is None:
        return 1

    try:
        cur = cx.cursor()

        # La tabla la crea alembic, que corre justo antes que este script. Si
        # no esta, es que alembic fallo, y entonces mem0 tampoco va a arrancar.
        cur.execute("SELECT to_regclass('settings')")
        if cur.fetchone()[0] is None:
            cur.close()
            log("la tabla 'settings' no existe. ¿ha fallado alembic?")
            log("Mira: docker compose logs mem0 | head -30")
            return 1

        # Las dimensiones se leen de postgres ANTES de armar la config, y no
        # van escritas en el codigo. Ver _dimensiones_de_la_columna().
        dims, de_donde = _dimensiones_de_la_columna(
            cur, os.environ.get("MEM0_GEMINI_DIM", ""))
        log(f"embeddings de {dims} dimensiones (segun {de_donde})")

        cur.execute("SELECT value FROM settings WHERE key = %s", ("config_overrides",))
        fila = cur.fetchone()
        cur.close()

        actual = {}
        if fila and fila[0]:
            try:
                actual = json.loads(fila[0])
            except Exception:
                log("el valor guardado no es JSON; lo sustituyo.")
                actual = {}

        # Que coincide de verdad. Con lo de antes bastaba con mirar el
        # proveedor, y eso hacia una cosa incomoda: cambiar MEM0_GEMINI_LLM en
        # el .env no hacia NADA, porque el script decia "ya estaba en gemini" y
        # se iba. El .env es la fuente de la verdad del modelo, asi que si el
        # modelo guardado no es el que pide el .env, se actualiza.
        guardado = actual.get("llm", {}).get("config", {}).get("model", "")
        guardado_dims = actual.get("embedder", {}).get("config", {}).get("embedding_dims")
        ya_va_bien = (
            actual.get("llm", {}).get("provider") == "gemini"
            and actual.get("embedder", {}).get("provider") == "gemini"
            and guardado == MODELO_LLM
            and actual.get("embedder", {}).get("config", {}).get("model") == MODELO_EMBEDDER
            # Las dimensiones tambien cuentan. Si no se comprueban, un cambio de
            # embedder o una recreacion de la tabla dejan la config con el
            # numero viejo y el script se va sin avisar, y luego el 502 vuelve
            # sin que nadie sepa por que.
            and guardado_dims == dims
        )
        if ya_va_bien:
            log("ya estaba bien configurado; no toco nada.")
            return 0

        if guardado and guardado != MODELO_LLM:
            log(f"cambio de modelo: {guardado} -> {MODELO_LLM}")
        if guardado_dims and guardado_dims != dims:
            log(f"cambio de dimensiones: {guardado_dims} -> {dims}")

        # La forma que espera mem0: provider + config. La fusion con la base es
        # profunda, asi que lo que no pongas aqui (temperature, top_p...) se
        # queda con el valor que traia la de openai.
        deseado = {
            "llm": {"provider": "gemini",
                    "config": {"api_key": clave, "model": MODELO_LLM,
                               "temperature": 0.2}},
            "embedder": {"provider": "gemini",
                         "config": {"api_key": clave, "model": MODELO_EMBEDDER,
                                    "embedding_dims": dims}},
        }

        if actual:
            # Hay configuracion previa (alguien toco /configure). Se respeta lo
            # suyo, con una excepcion: las claves que dependen de como esta
            # montado ESTE despliegue las pone el .env, siempre.
            #
            # La excepcion importa, y mas de lo que parece. Con setdefault para
            # todo, cambiar MEM0_GEMINI_LLM o el ancho de la columna no hacia
            # NADA: el script anunciaba "cambio de modelo" en el log y luego
            # guardaba el viejo, porque setdefault no pisa lo que ya hay. El log
            # decia una cosa y la base otra, y la que manda es la base. Con eso
            # el 502 volvia sin que nadie supiera por que.
            log("hay configuracion previa; el modelo y las dimensiones los "
                "pone el .env.")
            # Estas dos SIEMPRE se pisan. El resto, solo si falta.
            LAS_MANDA_EL_ENV = ("model", "embedding_dims")
            for seccion in ("llm", "embedder"):
                puesto = actual.get(seccion, {}).get("provider")
                if puesto not in (None, "gemini"):
                    continue
                cfg = actual.setdefault(seccion, {}).setdefault("config", {})
                for k, v in deseado[seccion]["config"].items():
                    if k in LAS_MANDA_EL_ENV:
                        cfg[k] = v
                    else:
                        cfg.setdefault(k, v)
                actual[seccion]["provider"] = "gemini"
            final = actual
        else:
            final = deseado

        cur = cx.cursor()
        cur.execute(
            "INSERT INTO settings (key, value, updated_at) "
            "VALUES (%s, %s, now()) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value, "
            "updated_at = now()",
            ("config_overrides", json.dumps(final)),
        )
        cur.close()
    except Exception as e:
        log(f"error guardando la configuracion: {type(e).__name__}: {e}")
        return 1
    finally:
        cx.close()

    log(f"configurado con gemini: llm={MODELO_LLM}  embedder={MODELO_EMBEDDER}")
    log(f"la clave ({len(clave)} caracteres) queda en la tabla settings,")
    log("que esta en el volumen de postgres, no en el codigo.")
    return 0


if __name__ == "__main__":
    sys.exit(main())