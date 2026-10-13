#!/bin/sh
# =============================================================================
#  JARVIS · preparador del proyecto para Container Manager (Synology / Xpenology)
# =============================================================================
#  Esto NO es un instalador: es el paso previo que hace que lo de despues sea
#  solo abrir Container Manager y pulsar "Crear". No deja nada a medias.
#
#  Que hace, en este orden:
#    1. Comprueba que Docker responde (y lo arranca si esta parado).
#    2. Descarga la imagen ya publicada, para que Container Manager no tenga que
#       compilar nada. Si no se puede descargar, la compila aqui una sola vez.
#    3. Crea el .env con TUS ids de usuario del DSM, que es lo que evita que los
#       ficheros acaben como root y no puedas borrarlos desde File Station.
#    4. Al final te pregunta la clave de IA y la guarda.
#    5. Te dice los 4 clics que quedan en Container Manager.
#
#  Como se ejecuta (una sola vez, por SSH):
#      cd /volume1/docker/jarvis
#      sh instalar-docker.sh
#
#  Y para actualizar a una version nueva, cuando salga:
#      sh instalar-docker.sh --actualizar
# =============================================================================
set -e

RAIZ="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$RAIZ/.env"
EJEMPLO="$RAIZ/configuracion.env.example"
IMAGEN="${JARVIS_IMAGEN:-ghcr.io/lucillas0/jarvis:latest}"
CONTENEDOR="${JARVIS_CONTENEDOR:-jarvis}"

ACCION="preparar"
PREGUNTAR_CLAVE=1
SALTAR_TODO=0

while [ $# -gt 0 ]; do
  case "$1" in
    --actualizar)  ACCION="actualizar" ;;
    --comprobar)   ACCION="comprobar" ;;
    --pulsos)      SALTAR_TODO=1 ;;   # no pregunta nada
    --puerto)      PUERTO_PEDIDO="${2:-}"; shift ;;
    --ayuda|-h)    ACCION="ayuda" ;;
    *) printf 'Opcion desconocida: %s\n' "$1"; ACCION="ayuda" ;;
  esac
  shift
done

# --- Colores -----------------------------------------------------------------
if [ -t 1 ]; then
  V=$(printf '\033[32m'); A=$(printf '\033[33m'); R=$(printf '\033[31m')
  AZ=$(printf '\033[36m'); N=$(printf '\033[0m'); G=$(printf '\033[90m')
else
  V=""; A=""; R=""; AZ=""; N=""; G=""
fi
info() { printf '%s> %s%s\n' "$AZ" "$*" "$N"; }
ok()   { printf '%sOK  %s%s\n' "$V" "$*" "$N"; }
aviso(){ printf '%s!  %s%s\n' "$A" "$*" "$N"; }
error(){ printf '%sx  %s%s\n' "$R" "$*" "$N"; }
nota() { printf '%s   %s%s\n' "$G" "$*" "$N"; }

ayuda() {
  # Del 2 al 23: justo el bloque de comentario de la cabecera, ni una linea mas.
  sed -n '2,23p' "$0" | sed 's/^# \{0,1\}//'
  exit 0
}

# --- Docker ------------------------------------------------------------------
docker_() {
  if docker version >/dev/null 2>&1; then docker "$@"; else sudo docker "$@"; fi
}

arrancar_docker() {
  docker version >/dev/null 2>&1 && return 0
  # En Xpenology el paquete se llama SynoCommunity/ContainerManager; en Synology
  # oficial, ContainerManager. Se prueban los dos y no se pasa de ahi.
  if [ -f /etc/synologyrelease ]; then
    nota "Docker no responde; intento arrancar Container Manager..."
    sudo synopkg start ContainerManager >/dev/null 2>&1 || \
      sudo synopkg start SynoCommunity-ContainerManager >/dev/null 2>&1 || true
    i=0
    while [ $i -lt 20 ]; do
      docker version >/dev/null 2>&1 && return 0
      i=$((i + 1)); sleep 1
    done
  fi
  error "Docker no responde."
  aviso "En Xpenology: Package Center > instalado > Container Manager > Iniciar."
  aviso "O por SSH:  sudo synopkg start ContainerManager"
  exit 1
}

# --- Sistema -----------------------------------------------------------------
PUID_DEF=1026
PGID_DEF=100
detectar_nas() {
  if [ -f /etc/synologyrelease ]; then
    # En DSM el id real del usuario con el que entras por SSH. Es el que debe
    # tener los ficheros del volumen, si no se quedan como root.
    PUID_DEF="$(id -u)"; PGID_DEF="$(id -g)"
    echo "Synology/Xenology  ($(cat /etc/synologyrelease 2>/dev/null | head -1))"
  else
    uname -sr
  fi
}

# --- .env --------------------------------------------------------------------
poner() {  # poner CLAVE VALOR  -> cambia la linea del .env sin duplicarla
  clave="$1"; valor="$2"
  if grep -q "^${clave}=" "$ENV_FILE" 2>/dev/null; then
    grep -v "^${clave}=" "$ENV_FILE" > "$ENV_FILE.tmp" || true
    printf '%s=%s\n' "$clave" "$valor" >> "$ENV_FILE.tmp"
    mv "$ENV_FILE.tmp" "$ENV_FILE"
  else
    printf '%s=%s\n' "$clave" "$valor" >> "$ENV_FILE"
  fi
}

leer_() { grep -E "^$1=" "$ENV_FILE" 2>/dev/null | tail -n1 | cut -d= -f2- | tr -d '\r'; }

crear_env() {
  if [ ! -f "$ENV_FILE" ]; then
    [ -f "$EJEMPLO" ] || { error "No encuentro $EJEMPLO"; exit 1; }
    cp "$EJEMPLO" "$ENV_FILE"
    ok ".env creado a partir del ejemplo."
  else
    ok ".env ya existe (no lo toco)."
  fi

  # PUID/PGID: solo se rellenan si estan en el defecto o vacios. Si ya los
  # cambiaste a mano, no se pisan.
  actual_p="$(leer_ PUID)"; actual_g="$(leer_ PGID)"
  if [ -z "$actual_p" ] || [ "$actual_p" = "1026" ]; then poner PUID "$PUID_DEF"; fi
  if [ -z "$actual_g" ] || [ "$actual_g" = "100" ];  then poner PGID "$PGID_DEF"; fi

  [ -n "$PUERTO_PEDIDO" ] && poner JARVIS_PUERTO "$PUERTO_PEDIDO"

  chmod 600 "$ENV_FILE" 2>/dev/null || true
  modo="$(stat -c '%a' "$ENV_FILE" 2>/dev/null || echo 600)"
  if [ "$modo" != "600" ]; then
    aviso "El .env no ha quedado en 600 (sistema de ficheros que no lo admite)."
    aviso "No lo subas a ningun sitio: lleva tus claves en claro."
  else
    ok ".env en 600, con tus ids: PUID=$(leer_ PUID) PGID=$(leer_ PGID)"
  fi
}

# --- La clave, al final ------------------------------------------------------
preguntar_clave() {
  if [ "$PREGUNTAR_CLAVE" -eq 0 ]; then return 0; fi
  if grep -qE '^(GROQ_API_KEY|GEMINI_API_KEY|OPENROUTER_API_KEY)=.+' "$ENV_FILE" 2>/dev/null; then
    ok "Ya hay una clave de modelo en .env. No la toco."
    return 0
  fi

  printf '\n'
  info "Ultimo paso: la clave de IA. Es lo unico que no puedo poner yo."
  nota "Groq (gratis y rapido):  https://console.groq.com/keys"
  nota "Gemini:                 https://aistudio.google.com/apikey"
  printf '\n'
  printf '  Pega la clave de Groq y pulsa Intro para continuar.\n'
  printf '  (vacio = seguir sin ella; la pones luego en http://IP:%s/setup)\n\n' "$(leer_ JARVIS_PUERTO)"

  printf '  Clave: '
  read -r clave || clave=""
  # Si viene pegada con el raton puede traer espacios o comillas.
  clave="$(printf '%s' "$clave" | tr -d ' \r"')"

  if [ -n "$clave" ]; then
    poner GROQ_API_KEY "$clave"
    chmod 600 "$ENV_FILE" 2>/dev/null || true
    ok "Clave guardada en .env"
  else
    aviso "Sin clave. El proyecto arranca igualmente."
    aviso "La pones en el navegador: http://IP-NAS:$(leer_ JARVIS_PUERTO)/setup"
  fi
}

# --- Imagen ------------------------------------------------------------------
asegurar_imagen() {
  info "Bajo la imagen de JARVIS (${IMAGEN})..."
  if docker_ pull "$IMAGEN" >/dev/null 2>&1; then
    ok "Imagen descargada. Container Manager no va a compilar nada."
    return 0
  fi

  # Si no se puede descargar, se compila aqui una vez. Compilar en el NAS va por
  # SMB y es lentisimo, pero es mejor que un proyecto de Container Manager que
  # no arranca porque no encuentra la imagen.
  if [ ! -f "$RAIZ/Dockerfile" ]; then
    error "No se ha podido descargar $IMAGEN y aqui no hay Dockerfile."
    error "Copia la carpeta JARVIS entera en el NAS y ejecuta esto otra vez."
    return 1
  fi
  aviso "No se ha podido descargar la imagen; la compilo aqui. Tarda."
  if docker_ build -t "$IMAGEN" "$RAIZ"; then
    ok "Imagen compilada: $IMAGEN"
    aviso "Container Manager seguira bajando de ghcr.io. Si vuelve a fallar,"
    aviso "en el proyecto pon la fuente 'Usar una imagen' con:  $IMAGEN"
  else
    error "El build ha fallado. Lo mas comun: falta espacio en el volumen"
    error "donde esta la carpeta, o no esta en un volumen compatible."
    docker_ images | head -5
    return 1
  fi
}

# --- Comprobaciones ----------------------------------------------------------
comprobar_puerto() {
  p="$(leer_ JARVIS_PUERTO)"; p="${p:-8000}"
  if command -v ss >/dev/null 2>&1 && ss -ltn 2>/dev/null | grep -q ":${p} "; then
    aviso "El puerto ${p} ya esta ocupado en este equipo."
    aviso "Cambia JARVIS_PUERTO en .env y vuelve a ejecutar esto."
    return 1
  fi
  return 0
}

resumen() {
  ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
  [ -z "$ip" ] && ip="IP-DEL-NAS"
  p="$(leer_ JARVIS_PUERTO)"; p="${p:-8000}"

  printf '\n'
  ok "Todo listo. En Container Manager solo queda esto:"
  printf '\n'
  printf '    1. Proyecto > Crear\n'
  printf '    2. Ruta del proyecto:  %s\n' "$RAIZ"
  printf '    3. Fuente: "Usar un archivo docker-compose.yml existente"\n'
  printf '    4. Nombre: jarvis   ->  Crear\n'
  printf '\n'
  nota "Espera un par de minutos a que tire la imagen y abre:"
  printf '\n'
  printf '    http://%s:%s/setup     <- pon aqui la clave, si no la pusiste antes\n' "$ip" "$p"
  printf '    http://%s:%s/           <- la interfaz\n' "$ip" "$p"
  printf '\n'
  nota "La clave que pongas se guarda en el volumen, no se pierde al actualizar."
  printf '\n'
}

# --- Programa ----------------------------------------------------------------
main() {
  [ "$ACCION" = "ayuda" ] && ayuda

  printf '\n'
  printf '  J.A.R.V.I.S.  ·  preparador para Container Manager\n'
  printf '  --------------------------------------------------\n\n'

  info "Este equipo: $(detectar_nas)"
  arrancar_docker
  ok "Docker responde (version $(docker_ version --format '{{.Server.Version}}' 2>/dev/null || echo '?'))."

  crear_env
  comprobar_puerto || exit 1

  if [ "$ACCION" = "comprobar" ]; then
    asegurar_imagen || exit 1
    printf '\n'; ok "Todo correcto."; printf '\n'
    exit 0
  fi

  # En actualizar no se vuelve a preguntar la clave: la que hay es la buena.
  [ "$ACCION" = "actualizar" ] && PREGUNTAR_CLAVE=0

  asegurar_imagen || exit 1
  preguntar_clave
  resumen
}

main "$@"