#!/bin/sh
# =============================================================================
#  Verificacion completa de JARVIS en Docker.
#
#  Levanta la imagen, arranca el contenedor y comprueba, en este orden:
#    1. que la imagen se construye y que el contenedor sale "healthy"
#    2. que /health responde
#    3. que la interfaz (/ui) y la pizarra (/board) se sirven
#    4. que el WebSocket RECHAZA a quien no lleva token   <- lo que se arreglo
#    5. que el WebSocket ACEPTA a quien lo lleva
#    6. que /api/config/exportar-claves da 401 sin token  <- lo que se arreglo
#    7. que las claves sobreviven a recrear el contenedor <- lo que se arreglo
#    8. que los datos quedan en el volumen, no en el contenedor
#    9. que baja privilegios si le dan PUID/PGID
#   10. que el token NO esta horneado en la imagen
#
#  Se limpia todo al terminar, incluido el volumen.
# =============================================================================
set -e

RAIZ="$(cd "$(dirname "$0")" && pwd)"
IMAGEN="jarvis-verificacion:local"
CONT="jarvis-verificacion"
PUERTO="${PUERTO:-8099}"
VOL="jarvis-verificacion-datos"
FALLOS=0

v()   { printf '%s%s%s\n' "$G" "$1" "$N"; }
ok()  { printf '  OK      %s\n' "$1"; }
mal() { printf '  FALLO   %s\n' "$1"; FALLOS=$((FALLOS + 1)); }
chk() { if [ "$1" = "0" ]; then ok "$2"; else mal "$2"; fi; }

if [ -t 1 ]; then V=$(printf '\033[32m'); R=$(printf '\033[31m'); A=$(printf '\033[33m'); N=$(printf '\033[0m'); G=$(printf '\033[36m')
else V=""; R=""; A=""; N=""; G=""; fi

limpiar() {
  docker rm -f "$CONT" >/dev/null 2>&1 || true
  docker volume rm "$VOL" >/dev/null 2>&1 || true
  docker rmi "$IMAGEN" >/dev/null 2>&1 || true
}
trap limpiar EXIT

printf '\n  JARVIS · verificacion en Docker\n'
printf '  --------------------------------\n\n'

# ---------------------------------------------------------------- 1. build
printf '> 1. Construyendo la imagen (la primera vez tard several minutos)...\n'
if docker build -t "$IMAGEN" "$RAIZ"; then
  ok "la imagen se construye"
else
  mal "la imagen NO se construye"
  exit 1
fi

# Que el token no se haya colado en la imagen.
if docker run --rm --entrypoint sh "$IMAGEN" -c 'find / -name "jarvis.token" 2>/dev/null | head -1' | grep -q jarvis.token; then
  mal "hay un jarvis.token DENTRO de la imagen (no deberia)"
else
  ok "no hay ningun token horneado en la imagen"
fi
# Tampoco el .env con las claves.
if docker run --rm --entrypoint sh "$IMAGEN" -c 'ls /app/brain/.env 2>/dev/null' | grep -q '\.env'; then
  mal "hay un .env DENTRO de la imagen (no deberia)"
else
  ok "no hay ningun .env dentro de la imagen"
fi

# ------------------------------------------------------------- 2. arrancar
printf '\n> 2. Arrancando el contenedor...\n'
docker volume create "$VOL" >/dev/null
docker rm -f "$CONT" >/dev/null 2>&1 || true
docker run -d --name "$CONT" \
  -p "$PUERTO:8000" \
  -e JARVIS_DATA_DIR=/data \
  -e JARVIS_SERVER_MODE=1 \
  -e SCREEN_ANALYSIS_MODE=off \
  -v "$VOL:/data" \
  "$IMAGEN" >/dev/null

printf '  esperando a que responda'
i=0
LISTO=0
while [ $i -lt 60 ]; do
  if curl -fsS "http://127.0.0.1:$PUERTO/health" >/dev/null 2>&1; then LISTO=1; break; fi
  printf '.'
  i=$((i + 1)); sleep 2
done
printf '\n'
[ "$LISTO" = "1" ] && ok "/health responde en $((i * 2)) s" || { mal "/health NO responde"; docker logs --tail 40 "$CONT"; exit 1; }

# El healthcheck de Docker deberia ponerse en healthy.
i=0
while [ $i -lt 40 ]; do
  ESTADO="$(docker inspect -f '{{.State.Health.Status}}' "$CONT" 2>/dev/null || echo sin)"
  [ "$ESTADO" = "healthy" ] && break
  [ "$ESTADO" = "unhealthy" ] && break
  i=$((i + 1)); sleep 3
done
printf '  estado del healthcheck de Docker: %s\n' "$ESTADO"

# ------------------------------------------------------------ 3. la interfaz
printf '\n> 3. La interfaz y la pizarra\n'
curl -fsS "http://127.0.0.1:$PUERTO/ui" >/dev/null 2>&1; chk $? "/ui se sirve"
curl -fsS "http://127.0.0.1:$PUERTO/board" >/dev/null 2>&1; chk $? "/board se sirve"
curl -fsS "http://127.0.0.1:$PUERTO/setup" >/dev/null 2>&1; chk $? "/setup se sirve"
curl -fsS "http://127.0.0.1:$PUERTO/api/tools" >/dev/null 2>&1; chk $? "/api/tools responde"
grep -q '"status":"ok"' <(curl -fsS "http://127.0.0.1:$PUERTO/health") 2>/dev/null
chk $? "/health dice status ok"
# Sin CDN: tiene que funcionar en un NAS sin salida a internet.
if curl -fsS "http://127.0.0.1:$PUERTO/ui" | grep -qiE 'cdn\.|unpkg\.com|jsdelivr'; then
  mal "la interfaz tira de un CDN (en un NAS sin internet no cargar)"
else
  ok "la interfaz no depende de ningun CDN"
fi

# ------------------------------------------------------------- 4-6. seguridad
printf '\n> 4. Seguridad (lo que se arreglo)\n'
CODIGO="$(curl -fsS "http://127.0.0.1:$PUERTO/ui" | grep -o 'window.JARVIS_TOKEN="[^"]*"' | head -1 | cut -d'"' -f2)"
if [ -n "$CODIGO" ]; then
  ok "la pagina trae el token"
else
  mal "la pagina NO trae el token: el WebSocket no podra abrirse"
fi

# Exportar claves sin token -> tiene que ser 401.
S1="$(curl -s -o /dev/null -w '%{http_code}' -X POST "http://127.0.0.1:$PUERTO/api/config/exportar-claves")"
[ "$S1" = "401" ] && ok "exportar-claves SIN token -> 401" || mal "exportar-claves SIN token -> $S1 (deberia ser 401)"

# Con token erroneo -> 401.
S2="$(curl -s -o /dev/null -w '%{http_code}' -X POST -H 'X-Jarvis-Token: inventado' "http://127.0.0.1:$PUERTO/api/config/exportar-claves")"
[ "$S2" = "401" ] && ok "exportar-claves con token FALSO -> 401" || mal "con token falso -> $S2 (deberia ser 401)"

# Sin token en el WebSocket -> tiene que rechazar.
if docker exec "$CONT" python - <<'PY' >/dev/null 2>&1
import asyncio, sys
try:
    import websockets
except ImportError:
    sys.exit(2)
async def main():
    try:
        async with websockets.connect("ws://127.0.0.1:8000/ws"):
            pass
    except Exception:
        return
    print("ACEPTO"); sys.exit(1)
asyncio.run(main())
PY
then
  ok "el WebSocket RECHAZA a quien no lleva token"
else
  mal "el WebSocket ACEPTO a quien no lleva token (es el fallo que se arreglo)"
fi

# Con token -> tiene que aceptar.
docker exec -e T="$CODIGO" "$CONT" python - <<'PY' >/dev/null 2>&1
import asyncio, os, sys
import websockets
async def main():
    async with websockets.connect(f"ws://127.0.0.1:8000/ws?token={os.environ['T']}") as ws:
        msg = await asyncio.wait_for(ws.recv(), timeout=15)
        sys.exit(0 if "hello" in msg else 1)
asyncio.run(main())
PY
chk $? "el WebSocket ACEPTA con el token y manda hello"

# -------------------------------------------------------- 7. las claves
printf '\n> 5. Las claves y el volumen\n'
docker exec "$CONT" python -c "import os; os.makedirs('/data', exist_ok=True); open('/data/keys.env','w').write('GROQ_API_KEY=gsk_prueba_contenedor\n')"
# Recrear el contenedor, que es lo que hacia perder la clave.
docker rm -f "$CONT" >/dev/null 2>&1
docker run -d --name "$CONT" -p "$PUERTO:8000" -e JARVIS_DATA_DIR=/data \
  -v "$VOL:/data" "$IMAGEN" >/dev/null
i=0; while [ $i -lt 40 ]; do curl -fsS "http://127.0.0.1:$PUERTO/health" >/dev/null 2>&1 && break; i=$((i+1)); sleep 2; done

docker exec "$CONT" python - <<'PY' >/dev/null 2>&1
import sys, os
sys.path.insert(0, "/app/brain")
os.environ["JARVIS_DATA_DIR"] = "/data"
from config import Config
sys.exit(0 if Config.GROQ_API_KEY == "gsk_prueba_contenedor" else 1)
PY
chk $? "la clave sobrevive a RECREAR el contenedor (era el bug)"

docker exec "$CONT" sh -c 'ls /data/jarvis.token' >/dev/null 2>&1
chk $? "el token se crea en el volumen"

docker exec "$CONT" sh -c 'ls /app/brain/jarvis.token' >/dev/null 2>&1
if [ $? = 0 ]; then mal "el token esta dentro del contenedor (se pierde al recrear)"; else ok "el token NO esta dentro del contenedor"; fi

# ------------------------------------------------------- 8. permisos
printf '\n> 6. Permisos\n'
docker rm -f "$CONT" >/dev/null 2>&1
docker run -d --name "$CONT" -p "$PUERTO:8000" -e JARVIS_DATA_DIR=/data \
  -e PUID=1026 -e PGID=100 -v "$VOL:/data" "$IMAGEN" >/dev/null
i=0; while [ $i -lt 40 ]; do curl -fsS "http://127.0.0.1:$PUERTO/health" >/dev/null 2>&1 && break; i=$((i+1)); sleep 2; done
UIDD="$(docker exec "$CONT" id -u 2>/dev/null || echo '?')"
if [ "$UIDD" = "1026" ]; then ok "baja a PUID=1026 (los ficheros no quedan como root)"
else mal "sigue como uid $UIDD en vez de 1026"; fi

docker exec "$CONT" sh -c 'touch /data/.prueba' >/dev/null 2>&1
chk $? "el usuario puede escribir en /data"

printf '\n'
printf -- '--------------------------------------------------------------\n'
if [ "$FALLOS" = "0" ]; then
  printf '%s  Todo correcto.%s\n' "$V" "$N"
else
  printf '%s  Fallan %s comprobaciones.%s\n' "$R" "$FALLOS" "$N"
fi
exit $FALLOS