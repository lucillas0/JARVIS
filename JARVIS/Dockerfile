# =============================================================================
#  JARVIS · imagen del servidor
# =============================================================================
#  Solo el cerebro: sin Electron, sin audio de escritorio, sin modelos locales.
#  Pensada para Docker y para Synology/Xenology con Container Manager.
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
# que pone TZ en el .env, en vez de ir a UTC.
RUN apt-get update \
 && apt-get install -y --no-install-recommends tzdata curl \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Primero las dependencias, para que la capa se reutilice entre versiones.
COPY brain/requirements-server.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt

# El cerebro va en /app/brain porque config.py deduce los assets del directorio
# padre del modulo: si se copiase plano en /app, ASSETS_DIR apuntaria a "/".
COPY brain/ /app/brain/
COPY assets/ /app/assets/

RUN mkdir -p /data

EXPOSE 8000
VOLUME ["/data"]

HEALTHCHECK --interval=30s --timeout=10s --start-period=45s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=5).status==200 else sys.exit(1)"

CMD ["python", "-m", "uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]