#!/usr/bin/env bash
# =============================================================================
#  JARVIS · instalador del servidor
# =============================================================================
#  Deja el servidor de JARVIS funcionando en Docker. Pensado para servidores
#  Linux y, en concreto, para Synology/Xenology con Container Manager, que es
#  donde el camino se hace con la interfaz en vez de con docker compose.
#
#  Uso:
#     ./instalar.sh                  instala, construye y arranca
#     ./instalar.sh --puerto 9000    con otro puerto
#     ./instalar.sh --sin-clave      arranca sin clave (servidor sin modelos)
#     ./instalar.sh --reconstruir    rehace la imagen desde cero
#     ./instalar.sh --parar          para el contenedor
#     ./instalar.sh --registrar      registra el proyecto en Container Manager
#     ./instalar.sh --estado         dice como va
#     ./instalar.sh --registros      enseña el log
#     ./instalar.sh --ayuda          esta ayuda
# =============================================================================
set -euo pipefail

RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="$RAIZ/.env"
EJEMPLO="$RAIZ/configuracion.env.example"
COMPOSE="$RAIZ/docker-compose.yml"

PUERTO=""
SIN_CLAVE=0
RECONSTRUIR=0
ACCION="instalar"

# --- Colores (se desactivan si la salida no es una terminal) -----------------
if [ -t 1 ]; then
  R=$'\033[31m'; V=$'\033[32m'; A=$'\033[33m'; AZ=$'\033[36m'; N=$'\033[0m'
else
  R=""; V=""; A=""; AZ=""; N=""
fi

info()  { printf '%s\n' "${AZ}>${N} $*"; }
ok()    { printf '%s\n' "${V}OK${N}  $*"; }
aviso() { printf '%s\n' "${A}!${N}  $*"; }
error() { printf '%s\n' "${R}x${N}  $*" >&2; }

ayuda() {
  sed -n '2,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
}

# --- Opciones -----------------------------------------------------------------
while [ $# -gt 0 ]; do
  case "$1" in
    --puerto)      PUERTO="${2:-}"; shift 2 ;;
    --sin-clave)   SIN_CLAVE=1; shift ;;
    --reconstruir) RECONSTRUIR=1; shift ;;
    --parar)       ACCION="parar"; shift ;;
    --registrar)   ACCION="registrar"; shift ;;
    --estado)      ACCION="estado"; shift ;;
    --registros)   ACCION="registros"; shift ;;
    --ayuda|-h)    ayuda ;;
    *) error "Opcion desconocida: $1"; ayuda ;;
  esac
done

# --- Que maquina es esta ------------------------------------------------------
detectar_sistema() {
  if [ -f /etc/synologyrelease ] || [ -d /etc/synology ]; then
    SISTEMA="Synology/Xenology"
    # Container Manager corre sus proyectos como este usuario del DSM.
    PUID_DEF="$(id -u)"
    PGID_DEF="$(id -g)"
  elif [ -f /etc/os-release ]; then
    SISTEMA="$(. /etc/os-release && echo "$NAME $VERSION_ID")"
    PUID_DEF=1000; PGID_DEF=1000
  else
    SISTEMA="Linux"
    PUID_DEF=1000; PGID_DEF=1000
  fi
}

# --- Como se llama a Docker ---------------------------------------------------
detectar_docker() {
  if ! command -v docker >/dev/null 2>&1; then
    error "No encuentro docker."
    case "$SISTEMA" in
      Synology*)
        aviso "En Synology, abre Package Center e instala 'Container Manager'."
        aviso "Tambien hace falta activar SSH (Panel de control > Terminal y SNMP)."
        ;;
      *)
        aviso "Instala Docker: https://docs.docker.com/engine/install/"
        ;;
    esac
    exit 1
  fi

  if docker compose version >/dev/null 2>&1; then
    COMPOSE_CMD=(docker compose)
  elif command -v docker-compose >/dev/null 2>&1; then
    COMPOSE_CMD=(docker-compose)
  else
    error "No encuentro Docker Compose v2."
    aviso "En Synology deberia venir con Container Manager."
    exit 1
  fi
}

# --- Estado del contenedor ---------------------------------------------------
estado() {
  if docker ps --format '{{.Names}}' | grep -qx jarvis; then
    ok "El contenedor esta en marcha."
    if curl -fsS "http://127.0.0.1:${PUERTO_ACTUAL:-8000}/health" 2>/dev/null; then
      echo
    fi
  else
    aviso "El contenedor no esta en marcha."
  fi
}

PUERTO_ACTUAL=""

leer_puerto() {
  if [ -f "$ENV_FILE" ]; then
    p="$(grep -E '^JARVIS_PORT=' "$ENV_FILE" | tail -n1 | cut -d= -f2 | tr -d '[:space:]')"
    PUERTO_ACTUAL="$p"
  fi
  PUERTO_ACTUAL="${PUERTO_ACTUAL:-8000}"
}

# --- Configuracion -----------------------------------------------------------
crear_env() {
  if [ -f "$ENV_FILE" ]; then
    ok "Ya existe .env (no lo toco)."
    return
  fi
  if [ ! -f "$EJEMPLO" ]; then
    error "No encuentro $EJEMPLO"
    exit 1
  fi
  cp "$EJEMPLO" "$ENV_FILE"
  # Detecta el usuario para que los datos no queden como root.
  sed -i.bak "s/^PUID=.*/PUID=${PUID_DEF}/" "$ENV_FILE"
  sed -i.bak "s/^PGID=.*/PGID=${PGID_DEF}/" "$ENV_FILE"
  [ -n "$PUERTO" ] && sed -i.bak "s/^JARVIS_PORT=.*/JARVIS_PORT=${PUERTO}/" "$ENV_FILE"
  rm -f "$ENV_FILE.bak"
  # El .env lleva claves: solo el propietario debe poder leerlo.
  chmod 600 "$ENV_FILE" 2>/dev/null || true
  # Comprobacion: algunos sistemas de ficheros (NTFS) no hacen caso a chmod.
  if [ "$(stat -c '%a' "$ENV_FILE" 2>/dev/null || echo 600)" != "600" ]; then
    aviso "No he podido dejar .env en 600 (sistema de ficheros que no lo soporta)."
    aviso "Restringelo a mano antes de continuar: chmod 600 .env"
  fi
  ok "He creado .env con tus permisos (PUID=$PUID_DEF PGID=$PGID_DEF)."
}

comprobar_claves() {
  if [ "$SIN_CLAVE" -eq 1 ]; then
    aviso "Arrancando sin claves: el servidor levantara pero no respondera."
    return
  fi
  if grep -qE '^(GROQ|GEMINI|OPENROUTER)_API_KEY=.+' "$ENV_FILE" 2>/dev/null; then
    ok "Hay al menos una clave de modelo."
  else
    aviso "No hay ninguna clave de modelo en .env."
    aviso "Abrelo y pega al menos una: GROQ_API_KEY, GEMINI_API_KEY u OPENROUTER_API_KEY."
    aviso "El servidor arrancara igualmente, pero no podra responder."
  fi
}

# --- Carpetas ----------------------------------------------------------------
crear_carpetas() {
  mkdir -p "$RAIZ/data"
  mkdir -p "$RAIZ/assets"
  # Los permisos los pone el contenedor; aqui solo evitamos surprises.
  if [ -n "$PUID_DEF" ]; then
    chmod 0777 "$RAIZ/data" 2>/dev/null || true
  fi
  ok "Carpetas data/ y assets/ listas."
}

comprobar_puerto() {
  if command -v ss >/dev/null 2>&1; then
    if ss -ltn 2>/dev/null | grep -q ":${PUERTO_ACTUAL} "; then
      aviso "El puerto ${PUERTO_ACTUAL} ya esta ocupado."
      aviso "Cambia JARVIS_PORT en .env o pasa --puerto con otro."
      return 1
    fi
  fi
  return 0
}

IP_LOCAL() {
  hostname -I 2>/dev/null | awk '{print $1}'
}

# --- Container Manager (Synology) -------------------------------------------
registrar_proyecto() {
  if [ "$SISTEMA" != "Synology/Xenology" ]; then
    aviso "El registro en Container Manager solo tiene sentido en Synology."
    aviso "Aqui basta con: ${COMPOSE_CMD[*]} up -d"
    return
  fi
  info "Creando el proyecto en Container Manager a mano:"
  echo
  echo "  1. Copia esta carpeta a una carpeta compartida, por ejemplo /volume1/docker/jarvis"
  echo "  2. Container Manager > Proyecto > Crear"
  echo "  3. Ruta del proyecto: la carpeta donde esta este archivo"
  echo "  4. Fuente: 'Usar un archivo docker-compose.yml existente'"
  echo "  5. Pulsa 'Crear' y espera al build (la primera vez tarda)"
  echo
  aviso "El boton Build de Container Manager no lee este script; usa la interfaz."
  aviso "Si prefieres la linea de ordenes, el equivalente es:"
  echo "     ${COMPOSE_CMD[*]} -f $COMPOSE up -d --build"
}

# --- Arranque y espera -------------------------------------------------------
arrancar() {
  info "Construyendo la imagen (la primera vez tarda varios minutos)..."
  if [ "$RECONSTRUIR" -eq 1 ]; then
    "${COMPOSE_CMD[@]}" -f "$COMPOSE" build --no-cache
  else
    "${COMPOSE_CMD[@]}" -f "$COMPOSE" build
  fi
  ok "Imagen construida."

  info "Arrancando..."
  "${COMPOSE_CMD[@]}" -f "$COMPOSE" up -d
  ok "Contenedor arrancado."

  info "Esperando a que responda..."
  for i in $(seq 1 30); do
    if curl -fsS "http://127.0.0.1:${PUERTO_ACTUAL}/health" >/dev/null 2>&1; then
      ok "Responde en http://127.0.0.1:${PUERTO_ACTUAL}"
      IP="$(IP_LOCAL)"
      [ -n "$IP" ] && ok "Y desde la red, en http://${IP}:${PUERTO_ACTUAL}"
      echo
      curl -fsS "http://127.0.0.1:${PUERTO_ACTUAL}/health" || true
      echo
      return 0
    fi
    sleep 4
  done
  aviso "Tarda mas de lo normal. Mira los registros con:"
  echo "     ${COMPOSE_CMD[*]} -f $COMPOSE logs -f jarvis"
  return 1
}

# --- Programa ----------------------------------------------------------------
main() {
  detectar_sistema
  info "Sistema: ${SISTEMA}"

  # Primero la configuracion: si Docker no esta, el usuario al menos se queda
  # con el .env listo para cuando lo instale (en Synology, Container Manager).
  crear_env
  leer_puerto
  [ -n "$PUERTO" ] && PUERTO_ACTUAL="$PUERTO"

  case "$ACCION" in
    estado)
      estado
      exit 0
      ;;
    registros)
      detectar_docker
      "${COMPOSE_CMD[@]}" -f "$COMPOSE" logs -f --tail=100 jarvis
      exit 0
      ;;
    registrar)
      registrar_proyecto
      exit 0
      ;;
  esac

  detectar_docker

  case "$ACCION" in
    parar)
      "${COMPOSE_CMD[@]}" -f "$COMPOSE" down
      ok "Contenedor parado."
      exit 0
      ;;
  esac

  comprobar_claves
  crear_carpetas
  if ! comprobar_puerto; then
    exit 1
  fi

  arrancar
  echo
  info "Siguientes pasos:"
  echo "  - Registros:  ./instalar.sh --registros"
  echo "  - Estado:     ./instalar.sh --estado"
  echo "  - Parar:      ./instalar.sh --parar"
  echo "  - Container Manager (Synology): ./instalar.sh --registrar"
}

main "$@"