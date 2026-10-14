# JARVIS · servidor Mem0 OSS, compilado desde el código oficial.
#
# POR QUÉ SE COMPILA Y NO SE TIRA DE LA IMAGEN
# ----------------------------------------------
# La imagen oficial "mem0/mem0-api-server" solo existe para linux/arm64
# (comprobado: su manifiesto no tiene amd64) y su último push es de
# septiembre de 2025. En una Synology x86 —un DS220+ o un DS920+, que es lo
# más común— el pull falla con:
#
#     no matching manifest for linux/amd64/v3 in the manifest list entries
#
# No es un problema de la configuración ni de la plataforma de Docker: el
# manifiesto no existe. Y con una imagen de 5 estrellas no hay alternativa
# oficial que_valga.
#
# Así que se compila el servidor oficial desde el código, sobre la misma base
# python:3.12-slim. El resultado es identico a lo que publicaron, con dos
# diferencias deliberadas:
#
#   · SIN --reload. El CMD oficial arranca uvicorn con --reload, que es de
#     desarrollo: reinicia el proceso al tocar un fichero y se come CPU. En
#     un NAS eso no.
#
#   · Version fijada (ARG MEM0_REF) y sin --upgrade pip, para que dos
#    esseraciones del mismo repositorio den lo mismo.
#
# Lo que este Dockerfile NO hace: cambiar una linea del servidor. Va el
# codigo de Mem0 tal cual. Si algún dia actualizan el servidor y aquí no se
# nota, se sube MEM0_REF y ya esta.
#
# Construir esto en un NAS ARM va rápido. En un x86 tarda: es un pip install
# grande. Se hace una vez; luego la imagen se queda en el disco.

ARG PYTHON=3.12-slim
FROM python:${PYTHON}

# Sin --reload: esto corre en un NAS, no en un portátil.
# Y no es --no-cache para este pip: es la primera y unica construccion, y
# quitando la cache se ahorra casi nada.
WORKDIR /app

ARG MEM0_REF=main

# El servidor de Mem0 es un subdirectorio del repo. Se copia el repo entero y
# luego se entra en server/; no hay forma de copiar "solo server/" sin el
# context completo, porque el server/ importa de mem0ai, que esta en el
# requirements y en la carpeta de al lado.
#
# ---------------------------------------------------------------------
# google-genai, y no es opcional ni cosmetico
# ---------------------------------------------------------------------
# El requirements.txt de upstream pone "google-generativeai", que es el SDK
# viejo. Pero mem0/llms/gemini.py y mem0/embeddings/gemini.py importan:
#     from google import genai
# que es el SDK NUEVO, el de google-genai. Con solo el viejo, importar el
# proveedor da:
#     ImportError: The 'google-genai' library is required.
# Y como gemini esta declarado en BUNDLED_LLM_PROVIDERS y
# BUNDLED_EMBEDDER_PROVIDERS del propio servidor, el servicio anuncia que lo
# trae y a la vez es incapaz de cargarlo. Bug de upstream, medido.
COPY mem0-server/requirements.txt ./
RUN pip install --no-cache-dir --disable-pip-version-check -r requirements.txt \
 && pip install --no-cache-dir --disable-pip-version-check google-genai

COPY mem0-server/ ./
COPY configurar-mem0.py ./

# HISTORY_DB_PATH. El servidor lo abre al instanciar Memory.from_config(), con
# lo que es OBLIGATORIO que exista: sqlite3 no crea el directorio, solo el
# fichero. Con la ruta por defecto (/app/history/history.db) y sin esta linea,
# el servidor peta con
#     sqlite3.OperationalError: unable to open database file
# y entra en bucle de reinicios. Es la cuarta forma de que este servicio no
# arranque, y la mas tonta de todas: un directorio que falta.
#
# Que es este fichero: un registro de las operaciones sobre recuerdos (que se
# creo, que se actualizo). NO son los recuerdos, que viven en postgres. Se
# puede perder sin perder nada; esta en la imagen y se regenera.
RUN mkdir -p /app/history

ENV PYTHONUNBUFFERED=1 \
    POSTGRES_HOST=postgres \
    POSTGRES_PORT=5432 \
    APP_DB_NAME=mem0 \
    HISTORY_DB_PATH=/app/history/history.db

EXPOSE 8888

# ---------------------------------------------------------------------
# El CMD, que es donde me equivoque las dos veces
# ---------------------------------------------------------------------
# El compose de upstream arranca asi:
#     alembic upgrade head && uvicorn main:app --host 0.0.0.0 --port 8000
# Yo lo deje en "uvicorn" a secas. Sin las migraciones no se crea ninguna
# tabla: "psql -c \dt" responde "Did not find any relations", y mem0 se come
# un KeyError en cuanto toca la base. Sin la migracion, el servicio arranca y
# luego revienta, o directamente ni arranca.
#
# El orden importa y va en este orden exacto:
#
#   1. alembic upgrade head   crea las tablas. Tarda la primera vez.
#   2. configurar-mem0.py    siembra la config con Gemini (ver el fichero).
#      Tiene que ir entre las dos porque el servidor lee esa fila al
#      importarse, y si no esta, se cae con "Missing credentials" de OpenAI.
#   3. uvicorn               por fin, a escuchar.
#
# EL PUERTO ES 8888, no el 8000 de upstream. Por dentro del proyecto no hay
# hay nada publicado, asi que el puerto es solo una convencion entre mem0 y JARVIS,
# y tiene que ser el mismo en los tres sitios: aqui, en el healthcheck y en
# MEM0_URL. Con mem0 en el 8000 y MEM0_URL en el 8888 (que es el defecto que
# trae el .env.example) no se hablaban, y el sintoma era solo
# "alcanzable: false" en /health, sin decir ni de quien era el fallo.
#
# --workers 1 a proposito: con varios workers cada uno abriria su propia
# conexion y las migraciones podrian correr a la vez. Y sin --reload, que es
# de desarrollo y en un NAS se come CPU.
CMD ["sh", "-c", "alembic upgrade head && python configurar-mem0.py && exec uvicorn main:app --host 0.0.0.0 --port 8888 --workers 1"]