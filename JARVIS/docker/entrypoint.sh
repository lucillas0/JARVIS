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

# ── 3bis. Una HOME escribible ────────────────────────────────────────────────
# Sin esto, el proceso baja a PUID pero se queda con HOME=/root, que es 0700 y
# de root: cualquier os.path.expanduser("~") da Permission denied. Por ahi
# empiezan las exploraciones de ficheros de la pizarra, que reventan antes de
# devolver nada en vez de devolver una lista vacia.
#
# Se hace antes de bajar privilegios porque crear el directorio como root es
# lo unico que funciona: el usuario final no puede crear /home/jarvis.
if [ "$PUID" != "0" ]; then
  mkdir -p /home/jarvis 2>/dev/null || true
  chown "$PUID:$PGID" /home/jarvis 2>/dev/null || true
  export HOME=/home/jarvis
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

  # Bajar privilegios SIEMPRE, pero si no se puede, avisar y seguir como root.
  #
  # Antes esto fallaba y el contenedor se moria con codigo 1, y Docker lo
  # reiniciaba, y otra vez: un bucle infinito con un traceback de tres lineas
  # en el log y ninguna pista de cual era el problema. El motivo real era
  # "cap_drop: ALL" en el compose, que se lleva CAP_SETUID y CAP_SETGID, pero
  # el sintoma no decia nada de capacidades.
  #
  # Bajar privilegios es una mejora (los ficheros de /volume1 salen del usuario
  # del DSM y se pueden borrar desde File Station), NO un requisito para que
  # el servidor arranque. Un contenedor de JARVIS corriendo como root, con las
  # capacidades recortadas, es peor en permisos pero infinitamente mejor que un
  # contenedor que no arranca. Asi que si esto falla, se dice claro y se sigue.
  #
  # PRIMERO SE PRUEBA, y solo despues se ejecuta de verdad. La prueba es un
  # hijo desechable que hace la misma bajada y se muere. Es lo que permite
  # mantener el exec del camino bueno: si la prueba falla, no se llega a
  # exec, y sin exec el shell se quedaria como PID 1 sin reenviar SIGTERM, y
  # "docker stop" pasaria de 2 segundos a los 10 del plazo de gracia.
  if python -c '
import os, sys
try:
    os.setgid(int(sys.argv[1]))
    os.setgroups([])
    os.setuid(int(sys.argv[2]))
except (OSError, ValueError):
    sys.exit(1)
' "$PGID" "$PUID" 2>/dev/null; then
    # exec y nada mas: PID 1 es uvicorn y las senales le llegan a el.
    exec python -c '
import os, sys
os.setgid(int(sys.argv[1]))
os.setgroups([])
os.setuid(int(sys.argv[2]))
os.execvp(sys.argv[3], sys.argv[3:])
' "$PGID" "$PUID" "$@"
  fi

  aviso "NO he podido bajar a $PUID:$PGID; sigo como root (uid $(id -u))."
  aviso "Revisa el compose: con cap_drop ALL hacen falta cap_add SETUID y SETGID,"
  aviso "y no hay que poner 'user:' (eso se lleva las capacidades)."
  aviso "El servidor arranca igual; solo los ficheros de /data quedaran como root"
  aviso "y habra que borrarlos desde una consola de DSM, no desde File Station."
  exec "$@"
fi

exec "$@"