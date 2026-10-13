#!/bin/sh
# =============================================================================
#  JARVIS en Xpenology / Synology, sin docker-compose
# =============================================================================
#  Si el proyecto de Container Manager te ha dado guerra, esto lo evita: no hay
#  .yml, no hay redes propias, no hay claves de despliegue y no hay build en el
#  NAS. Es un unico `docker run` y poco mas.
#
#  Que se ejecuta desde SSH del NAS:
#
#      cd /volume1/docker/jarvis
#      sh instalar-nas.sh
#
#  Y para actualizar, cuando haya version nueva:
#
#      sh instalar-nas.sh --actualizar
#
#  Todo lo que hace:
#    · comprueba que este dentro de Docker/Container Manager
#    · crea las carpetas de los bind mount ANTES de arrancar (Synology no las
#      crea solo, y esa es la causa numero uno de "no funciona")
#    · copia el .env de ejemplo si no existe
#    · tira de la imagen ya publicada, y si no hay, construye aqui
#    · arranca el contenedor y espera a que /health responda
#    · dice la direccion para abrirla
# =============================================================================
set -e

IMAGEN="${JARVIS_IMAGEN:-ghcr.io/lucillas0/jarvis:latest}"
CONTENEDOR="${JARVIS_CONTENEDOR:-jarvis}"
PUERTO="${JARVIS_PUERTO:-8000}"
DATOS="${JARVIS_DATOS:-$PWD/data}"
ASSETS="${JARVIS_ASSETS:-$PWD/assets}"
PUID="${PUID:-1026}"
PGID="${PGID:-100}"
PLATAFORMA="${JARVIS_PLATAFORMA:-}"

verde() { printf '\033[32m%s\033[0m\n' "$*"; }
rojo() { printf '\033[31m%s\033[0m\n' "$*"; }
nota() { printf '\033[90m%s\033[0m\n' "$*"; }

docker_() { if docker version >/dev/null 2>&1; then docker "$@"; else sudo docker "$@"; fi; }

actualizar=0
[ "$1" = "--actualizar" ] && actualizar=1

# ── ¿Estamos en Docker? ──────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1 && ! command -v sudo >/dev/null 2>&1; then
  rojo "No encuentro Docker en este equipo."
  exit 1
fi
if ! docker_ version >/dev/null 2>&1; then
  rojo "Docker no responde. En Xpenology: SSH -> sudo synopkg start ContainerManager"
  exit 1
fi
nota "Docker: $(docker_ version --format '{{.Server.Version}}' 2>/dev/null || echo '?')"

# ── Carpetas de los bind mount ───────────────────────────────────────────────
# Synology no crea el punto de montaje. Si ./data no existe, el arranque falla
# con "Bind mount failed" y no hay nada mas que hacer que crearlo antes.
mkdir -p "$DATOS"
if [ ! -d "$ASSETS" ]; then
  # Los assets solo sirven para el audio de aviso. Si no se montan, el servidor
  # funciona igual; se crea la carpeta vacia para que el bind mount no falle.
  mkdir -p "$ASSETS"
fi

# ── Configuracion ────────────────────────────────────────────────────────────
if [ ! -f .env ]; then
  if [ -f configuracion.env.example ]; then
    cp configuracion.env.example .env
    rojo "He creado .env a partir del ejemplo. Rellena al menos una clave de"
    rojo "API (GROQ_API_KEY, GEMINI_API_KEY u OPENROUTER_API_KEY) y ejecuta"
    rojo "otra vez este script."
    exit 1
  fi
  rojo "No hay .env ni configuracion.env.example en $(pwd)."
  exit 1
fi

if grep -qE '^(GROQ_API_KEY|GEMINI_API_KEY|OPENROUTER_API_KEY)=.+' .env; then
  verde ".env tiene al menos una clave de API."
else
  rojo "Ojo: en .env no hay ninguna clave de API rellenada. El servidor"
  rojo "arrancara pero no respondera a nada."
fi

# ── Imagen ───────────────────────────────────────────────────────────────────
# Se tira la imagen ya construida si existe: compilar en el NAS va por SMB y es
# lentisimo, y en un DS220+ puede ser la causa de que el build se quede
# colgado. Solo se construye si no se puede tirar.
if docker_ pull "$IMAGEN" >/dev/null 2>&1; then
  verde "Imagen descargada: $IMAGEN"
else
  if [ ! -f Dockerfile ]; then
    rojo "No se ha podido descargar $IMAGEN y no hay Dockerfile aqui para"
    rojo "construirla. Copia la carpeta JARVIS entera en el NAS."
    exit 1
  fi
  nota "No se pudo descargar $IMAGEN; se construye aqui. Tarda, se patienta."
  if docker_ build -t "$IMAGEN" . ; then
    verde "Imagen construida: $IMAGEN"
  else
    rojo "El build ha fallado. Lo mas comun en un NAS: falta espacio en"
    rojo "/volume1/docker o la carpeta no esta en un volumen compatible."
    exit 1
  fi
fi

# ── Contenedor ───────────────────────────────────────────────────────────────
docker_ rm -f "$CONTENEDOR" >/dev/null 2>&1 || true

PLAT_ARG=""
[ -n "$PLATAFORMA" ] && PLAT_ARG="--platform $PLATAFORMA"

nota "Arrancando $IMAGEN en el puerto $PUERTO..."
# shellcheck disable=SC2086
docker_ run -d \
  --name "$CONTENEDOR" \
  --restart unless-stopped \
  $PLAT_ARG \
  -p "$PUERTO:8000" \
  -e JARVIS_SERVER_MODE=1 \
  -e JARVIS_DATA_DIR=/data \
  -e TZ="${TZ:-Europe/Madrid}" \
  -e PUID="$PUID" \
  -e PGID="$PGID" \
  -e SCREEN_ANALYSIS_MODE="${SCREEN_ANALYSIS_MODE:-off}" \
  --env-file .env \
  --memory "${JARVIS_MEMORIA:-2g}" \
  --security-opt no-new-privileges:true \
  --cap-drop ALL \
  --stop-timeout 30 \
  --log-opt max-size=10m \
  --log-opt max-file=3 \
  -v "$DATOS:/data" \
  -v "$ASSETS:/app/assets:ro" \
  "$IMAGEN" >/dev/null

verde "Contenedor arrancado: $CONTENEDOR"

# ── Esperar a que conteste ───────────────────────────────────────────────────
nota "Esperando al cerebro..."
i=0
while [ "$i" -lt 60 ]; do
  if docker_ exec "$CONTENEDOR" python -c \
      "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health',timeout=3).status==200 else sys.exit(1)" \
      >/dev/null 2>&1; then
    break
  fi
  i=$((i + 1))
  sleep 2
done

if [ "$i" -ge 60 ]; then
  rojo "El contenedor no ha contestado en 2 minutos. Logs:"
  docker_ logs --tail 40 "$CONTENEDOR" >&2
  exit 1
fi

IP=$(hostname -I 2>/dev/null | awk '{print $1}')
[ -z "$IP" ] && IP="IP-DEL-NAS"

verde ""
verde "  JARVIS esta en marcha."
verde ""
verde "    Interfaz:  http://$IP:$PUERTO/"
verde "    Salud:     http://$IP:$PUERTO/health"
verde "    Pizarra:   http://$IP:$PUERTO/board"
verde ""
verde "  Datos en:  $DATOS"
verde "  Logs:      docker logs -f $CONTENEDOR"
verde ""
nota "Recuerda: /health, /api/... y /ws no piden contrasena. Que no llegue a"
nota "internet sin poner algo delante."