# =============================================================================
#  JARVIS · imagen del servidor
# =============================================================================
#  Solo el cerebro: sin Electron, sin audio de escritorio, sin modelos locales.
#  Pensada para Docker y para Synology/Xenology con Container Manager.
#
#  Lo que se ha tenido en cuenta para el NAS, porque son los motivos por los que
#  un contenedor de esto no arrancaba:
#
#   · El contenedor corre con el PUID/PGID de tu usuario del NAS, y /app se
#     copia como root. El unico sitio de /app donde el cerebro escribe al
#     arrancar es brain/db/, asi que se deja creado y con permisos de escritura.
#   · Synology no crea las carpetas de un bind mount: si no existen, el arranque
#     falla con "Bind mount failed". Lo comprueba y avisa antes de arrancar.
#   · multi-arch (amd64 y arm64) sin nada que compilar: todo son ruedas de
#     Python, asi que el mismo Dockerfile sirve para un DS918+ y para un DS923+.
# =============================================================================
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    JARVIS_SERVER_MODE=1 \
    JARVIS_DATA_DIR=/data \
    BRAIN_HOST=0.0.0.0 \
    BRAIN_PORT=8000 \
    TZ=Europe/Madrid

# tzdata hace que las fechas del resumen semanal y los horarios usen la zona
# que pone TZ en el .env, en vez de ir a UTC. curl no se usa: la comprobacion de
# salud va en python, que ya esta, y meter mas paquetes engorda la imagen.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Primero las dependencias, para que la capa se reutilice entre versiones.
COPY brain/requirements-server.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt

# El cerebro va en /app/brain porque config.py deduce los assets del directorio
# padre del modulo: si se copiase plano en /app, ASSETS_DIR apuntaria a "/".
COPY brain/ /app/brain/
COPY assets/ /app/assets/

# El .dockerignore quita brain/db/, y con el usuario del NAS /app no deja
# crearla: si faltara, el arranque muere en un PermissionError. Se crea aqui y
# se deja abierta para que la cree quien quiera correr con PUID distinto.
RUN mkdir -p /data /app/brain/db \
 && chmod 0777 /data /app/brain/db \
 && chmod 0775 /app /app/brain

COPY docker/entrypoint.sh /usr/local/bin/jarvis-entrypoint
RUN chmod +x /usr/local/bin/jarvis-entrypoint

EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=60s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5).status==200 else sys.exit(1)"

ENTRYPOINT ["jarvis-entrypoint"]
# --app-dir /app/brain NO es un detalle: es lo que hace que uvicorn encuentre
# main.py. El WORKDIR es /app y el modulo esta en /app/brain, asi que sin esto
# el contenedor responde
#     "Error loading ASGI app. Could not import module \"main\""
# y se va con codigo 3. En un NAS eso es un proyecto que se queda reiniciando
# sin decir por que, y por eso hace falta --app-dir y no solo cambiar el
# WORKDIR (que dejaria en /app/brain rutas que el entrypoint usa absolutas).
CMD ["python", "-m", "uvicorn", "main:app", "--app-dir", "/app/brain", "--host", "0.0.0.0", "--port", "8000"]