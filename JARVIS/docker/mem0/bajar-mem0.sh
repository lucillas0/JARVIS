#!/bin/sh
# =============================================================================
#  Bajar el codigo del servidor de Mem0 para poder compilarlo
# =============================================================================
#  Por que hace falta esto
#  ----------------------
#  La imagen oficial "mem0/mem0-api-server" solo existe para linux/arm64, asi
#  que en un Synology x86 (un DS220+ o un DS920+, que es lo normal) el pull
#  falla con "no matching manifest for linux/amd64". No hay alternativa
#  oficial: es la unica imagen que publican, y tiene 5 estrellas.
#
#  Asi que se compila el servidor desde su codigo. Y ese codigo no va en el
#  repo (son 13,8 MB y 185 ficheros de otro proyecto), se baja de aqui.
#
#  Que hace
#  --------
#  Descarga el zip de github y copia solo la carpeta server/, que es la que
#  lleva el FastAPI. El resto del repo (los tests, la documentacion, el
#  ejemplo de openmemory) no hace falta para arrancar.
#
#  Como se llama
#  -------------
#      bash docker/mem0/bajar-mem0.sh
#
#  Y luego, construir y levantar:
#      docker compose up -d
#
#  Para fijar una version concreta (recomendado en un servidor que no se toca):
#      MEM0_REF=v1.0.0 bash docker/mem0/bajar-mem0.sh
#  Con "main" se trae lo ultimo, que puede cambiar sin avisar. En produccion
#  conviene fijar el tag: una actualizacion de Mem0 no deberia cambiarle el
#  comportamiento a un asistente que esta funcionando.
# =============================================================================
set -e

REF="${MEM0_REF:-main}"
AQUI="$(cd "$(dirname "$0")" && pwd)"
DESTINO="$AQUI/mem0-server"
ZIP="${TMPDIR:-/tmp}/mem0-$REF.zip"

echo "==> Bajando mem0ai/mem0  (ref: $REF)"

if command -v curl >/dev/null 2>&1; then
  curl -fsSL -o "$ZIP" "https://codeload.github.com/mem0ai/mem0/zip/refs/heads/$REF"
elif command -v wget >/dev/null 2>&1; then
  wget -q -O "$ZIP" "https://codeload.github.com/mem0ai/mem0/zip/refs/heads/$REF"
else
  echo "ERROR: hace falta curl o wget" >&2
  exit 1
fi

if command -v unzip >/dev/null 2>&1; then
  TMP="$(mktemp -d)"
  unzip -q "$ZIP" -d "$TMP"
  # La carpeta se llama mem0-main, o mem0-v1.0.0 si se fijo un tag.
  RAIZ="$(find "$TMP" -maxdepth 1 -mindepth 1 -type d | head -1)"
  rm -rf "$DESTINO"
  mkdir -p "$DESTINO"
  cp -R "$RAIZ/server/." "$DESTINO/"
  rm -rf "$TMP"
else
  echo "ERROR: hace falta unzip" >&2
  echo "En Debian/Ubuntu:  apt-get install -y unzip" >&2
  exit 1
fi

rm -f "$ZIP"

# ---------------------------------------------------------------- comprobar
FALTA=0
for f in main.py alembic.ini requirements.txt server_state.py models.py; do
  if [ ! -f "$DESTINO/$f" ]; then
    echo "  FALTA  $f" >&2
    FALTA=1
  fi
done
if [ "$FALTA" != "0" ]; then
  echo "ERROR: la copia no esta completa. ¿Ha cambiado la estructura del repo?" >&2
  exit 1
fi

echo "==> Listo: $DESTINO"
echo "    $(find "$DESTINO" -type f | wc -l) ficheros"
echo
echo "Ahora:  docker compose up -d"