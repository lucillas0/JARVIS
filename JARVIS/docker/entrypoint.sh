#!/bin/sh
# =============================================================================
#  JARVIS · arranque del contenedor del servidor
# =============================================================================
#  Esto va antes que uvicorn para resolver los tres motivos por los que un
#  contenedor de JARVIS no levanta en un NAS:
#
#    1. /data es un bind mount y Synology no crea la carpeta que le pongas. Si
#       no existe, Docker falla antes de arrancar. Aqui se avisa con el texto
#       exacto que hay que seguir en File Station, en vez de un error de Docker.
#    2. El contenedor suele correr con el PUID/PGID del usuario del NAS, y /app
#       se copia como root. El unico punto de /app donde el cerebro escribe al
#       arrancar es brain/db/, asi que se deja listo y escribible.
#    3. Sin claves de API el servidor arranca pero no contesta a nada, y desde
#       fuera parece que esta roto. Se avisa al arrancar.
# =============================================================================
set -e

PUID="${PUID:-0}"
PGID="${PGID:-0}"

aviso() { echo "JARVIS: $*" >&2; }

# ── 1. El volumen de datos ───────────────────────────────────────────────────
if ! mkdir -p /data 2>/dev/null; then
  aviso "no existe /data y no se ha podido crear."
fi
if ! touch /data/.jarvis-prueba 2>/dev/null; then
  aviso "/data no es escribible por $(id -u):$(id -g)."
  aviso "En Synology: abre File Station, crea la carpeta del proyecto y en"
  aviso "Propiedades > Permisos dale Leer y Escribir al usuario que uses."
else
  rm -f /data/.jarvis-prueba
fi

# ── 2. Lo unico que se escribe dentro de /app ────────────────────────────────
mkdir -p /app/brain/db 2>/dev/null || true
chmod 0777 /app/brain/db 2>/dev/null || true

# ── 3. Claves de API ─────────────────────────────────────────────────────────
# config.py las lee del entorno, y el .env montado tambien. Si no hay ninguna,
# el proceso vive pero el chat no responde: mejor decirlo claro.
if [ -z "${GROQ_API_KEY}${GEMINI_API_KEY}${OPENROUTER_API_KEY}" ]; then
  if [ -f /app/.env ] || [ -f /app/brain/.env ]; then
    aviso "no veo claves de API en el entorno. Si el .env las tiene, se leeran"
    aviso "al reiniciar el contenedor."
  else
    aviso "SIN CLAVES DE API: el servidor arrancara pero no respondera."
    aviso "Copia configuracion.env.example a .env, rellenalo y reinicia."
  fi
fi

# ── 4. Bajar privilegios, si se piden ────────────────────────────────────────
# Por defecto se queda como root, que en un contenedor sin capacidades ni
# --privileged es lo mas simple y lo que menos falla. Si pones PUID/PGID en el
# .env, se baja a ese usuario, pero antes se le deja /data y brain/db suyos.
#
# La bajada se hace con python y no con su ni con setpriv: python esta siempre
# en la imagen, setpriv no siempre (util-linux no va en slim), y su necesita una
# entrada en /etc/passwd que en un NAS no existe.
if [ "$PUID" != "0" ] || [ "$PGID" != "0" ]; then
  chown -R "$PUID:$PGID" /app/brain/db 2>/dev/null || \
    aviso "no se ha podido hacer chown de /app/brain/db; se queda 0777."
  if [ -d /data ]; then
    # chown -R y NO chown a secas. /data es un volumen: Docker lo crea como
    # root, y si el contenedor arranco alguna vez sin PUID (por ejemplo en una
    # prueba, o si alguien lo levanto a mano antes de ajustar el .env) el
    # jarvis.db de dentro se queda de root. Con chown simple, el usuario nuevo
    # abre su propia base y se topa con
    #     sqlite3.OperationalError: attempt to write a readonly database
    # al arrancar, y el contenedor se muere con codigo 3 sin dar mas pistas.
    chown -R "$PUID:$PGID" /data 2>/dev/null || \
      aviso "no he podido dar /data al usuario $PUID; revisa los permisos del volumen."
  fi

  exec python -c '
import os, sys
os.setgid(int(sys.argv[1]))
os.setgroups([])
os.setuid(int(sys.argv[2]))
os.execvp(sys.argv[3], sys.argv[3:])
' "$PGID" "$PUID" "$@"
fi

exec "$@"