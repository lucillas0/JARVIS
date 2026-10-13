# JARVIS en Docker / Synology / Xpenology

Este directorio monta el **cerebro** de JARVIS como servidor: todo lo de
escritorio (Electron, el notch, las manos, la voz) se queda en tu PC y el
servidor se encarga de pensar, recordar y responder. Por eso no hay ni Electron
ni dependencias de audio en la imagen: en un NAS no hay ni pantalla ni tarjeta
de sonido, y lo que falta se avisa en `/health` como `"sin hardware"` en vez de
tirar el arranque.

## Tres vías, de menos a más fights

| Vía | Cuándo |
|---|---|
| [1. Un solo script](#1-un-solo-sin-compose) | **La recomendada.** Un `docker run` y se acabó. Sin `.yml`. |
| [2. Imagen publicada](#2-imagen-publicada-sin-compilar) | Lo mismo, pero sin compilar en el NAS. |
| [3. Container Manager (compose)](#3-container-manager-compose) | Solo si ya usas proyectos de Container Manager. |

## Ficheros

| Fichero | Para qué |
|---|---|
| `instalar-nas.sh` | **La vía recomendada en NAS.** Crea lo que falta, tira la imagen y arranca, todo con `docker run`. |
| `instalar-docker.sh` | Lo mismo para un Linux normal. |
| `docker-compose.yml` | El proyecto para Container Manager, ya sin las trampas de Synology. |
| `Dockerfile` | Imagen mínima: Python + el cerebro. Multi-arch (amd64 y arm64) sin compilar nada. |
| `docker/entrypoint.sh` | Lo que va antes de uvicorn: comprueba permisos, avisa de lo que falta y baja a PUID/PGID. |
| `configuracion.env.example` | Todas las opciones, comentadas y con los valores por defecto reales. Se copia a `.env`. |

Si lo que quieres es instalarlo en tu PC, usa `instalar.bat` / `instalar.sh` y
mira `INSTALAR.md`.

---

## 1. Un solo, sin compose

Desde SSH del NAS:

```sh
cd /volume1/docker/jarvis
cp configuracion.env.example .env
nano .env          # pega al menos una clave: Groq, Gemini u OpenRouter
sh instalar-nas.sh
```

Y para actualizar cuando haya versión nueva:

```sh
sh instalar-nas.sh --actualizar
```

El script se encarga de lo que suele fallar:

- **Crea las carpetas de los montajes antes de arrancar.** Synology no las crea:
  si `./data` no existe, Docker responde `Bind mount failed` y ahí se acabó.
- **Tira la imagen ya construida** y, si no está, construye en el NAS.
- **Espera a que `/health` conteste** y, si no contesta en 2 minutos, saca los
  logs y dice por qué.
- **Te da la dirección** al final.

Variables que puedes cambiar en el `.env` o en la llamada:

```sh
JARVIS_PUERTO=8080 sh instalar-nas.sh          # otro puerto
JARVIS_DATOS=/volume2/jarvis sh instalar-nas.sh # datos en otro volumen
PUID=1026 PGID=100 sh instalar-nas.sh           # tu usuario del NAS
```

## 2. Imagen publicada, sin compilar

Compilar en el NAS va por SMB y es lentísimo; en un DS220+ con 1 GB de RAM puede
quedarse colgado y DSM acaba diciendo que el proyecto falló aunque esté bien.

Por eso la imagen se publica ya construida en cada release, para `amd64` y
`arm64`:

```
ghcr.io/lucillas0/jarvis:latest
```

El script de arriba ya la usa sola. Si quieres hacerlo a mano:

```sh
docker pull ghcr.io/lucillas0/jarvis:latest
docker run -d --name jarvis --restart unless-stopped \
  -p 8000:8000 \
  -e JARVIS_SERVER_MODE=1 -e JARVIS_DATA_DIR=/data \
  -e PUID=1026 -e PGID=100 -e SCREEN_ANALYSIS_MODE=off \
  --env-file .env \
  -v /volume1/docker/jarvis/data:/data \
  ghcr.io/lucillas0/jarvis:latest
```

La imagen sale multi-arch, así que el mismo comando vale para un DS918+ (Realtek,
`arm64`) y para un DS923+ (AMD Ryzen, `amd64`). Si tu NAS es de otra cosa, mira
`uname -m` y añade `--platform`.

## 3. Container Manager (compose)

1. Copia esta carpeta a una carpeta compartida, por ejemplo
   `/volume1/docker/jarvis`.
2. **Antes de nada**, en File Station, crea dentro la carpeta `data`. Es
   obligatoria: Synology no crea los puntos de montaje.
3. Copia `configuracion.env.example` a `.env` y pon al menos una clave de API.
4. En **Container Manager → Proyecto → Crear**, elige esa carpeta como ruta.
5. Fuente: *usar un archivo `docker-compose.yml` existente*.
6. **Crear**.

El `docker-compose.yml` de este repositorio viene ya con lo que rompía en
Container Manager resuelto. Por si te lo encuentras en otra parte, esto es lo que
importa y por qué:

- **No declara `networks:`.** Docker Compose 5.1.0, el que lleva DSM 7.1 y 7.2,
  falla con `Container cannot be connected to network endpoints` contra cualquier
  daemon con API menor de 1.44 en cuanto un servicio declara su propia red. Sin
  declarar nada va a la red por defecto y desaparece el problema.
- **No usa `user:`.** El contenedor arranca como root y el entrypoint ya baja a
  `PUID`/`PGID` solo. Con `user:` el entrypoint corría sin permisos y no podía
  arreglar nada, que es como `/app/brain/db` se quedaba sin permisos y el
  arranque moría con un `PermissionError`.
- **No lleva `deploy:`.** Fuera de Swarm se ignora, y hay versiones de Container
  Manager que se atascan al leerlo.
- **No monta `./.env` en `/app/.env`.** El sitio donde lo lee `brain/config.py` es
  `/app/brain/.env`, así que ese montaje era decorativo. La configuración llega
  por `env_file`, que sí funciona.

---

## Por qué antes no levantaba

Tres cosas, en orden de probabilidad:

1. **`PermissionError` al arrancar.** `brain/memory/sqlite_store.py` creaba
   `brain/db/` con un `os.makedirs` sin `try`. Como `.dockerignore` quita
   `brain/db/` de la imagen y el contenedor corre con el usuario del NAS, `/app`
   es de root y ahí no se puede crear. Ahora el `makedirs` está protegido y el
   Dockerfile deja esa carpeta creada y con permisos de escritura.
2. **`Bind mount failed`.** Synology no crea las carpetas que aparecen en un
   bind mount. El entrypoint lo avisa con el texto a seguir, y `instalar-nas.sh`
   las crea antes.
3. **La red del compose**, con el fallo de Compose 5.1.0 sobre daemons viejos
   descrito más arriba.

## Qué hay dentro

- **Solo el cerebro.** Sin Electron, sin `sounddevice`, sin `piper`, sin `torch`,
  sin `openwakeword`. De las ~200 dependencias del escritorio se queda una
  docena. Los imports de audio están todos dentro de `try/except` o de funciones,
  así que su ausencia no tumba nada.
- **La interfaz no sale a internet.** `brain/ui/index.html` lleva `three.js`
  dentro y todos sus endpoints son del mismo origen (`location.origin`). No hay
  ni un CDN: funciona con el NAS sin salida a la nube.
- **Modo LITE por defecto.** En un contenedor sin Electron, la pizarra web no
  puede enseñarte lo que hace el escritorio, así que JARVIS arranca en lite la
  primera vez y lo recuerda en `/data/modo.txt`. Se cambia en `/modo`.

## Configuración

Todo se configura en `.env`, que se copia de `configuracion.env.example`. Lo
mínimo para que conteste algo:

```env
GROQ_API_KEY=...
```

Sin ninguna clave el contenedor **arranca pero no responde**: es un aviso, no un
fallo, y el entrypoint lo dice al arrancar y en los logs.

Dos cosas que conviene poner en un NAS:

```env
# En un NAS no hay pantalla, y por defecto el análisis continuo está en ON.
SCREEN_ANALYSIS_MODE=off
PUID=1026
PGID=100
```

## Seguridad

`/health`, `/api/*` y `/ws` **no piden contraseña**. El compose abre el puerto
solo en la LAN, y conviene que siga así. Si necesitas llegar desde fuera, pon
delante algo que autentique (Caddy, nginx con basic auth, Cloudflare Tunnel) y
no expongas el 8000 a internet.

## Comandos

```sh
docker logs -f jarvis          # ver lo que pasa
docker restart jarvis          # reiniciar
docker stop jarvis             # parar
docker rm -f jarvis            # borrar el contenedor (los datos se quedan)
```

Los datos viven en `data/` (o donde pongas `JARVIS_DATOS`): base de datos,
memoria y registros. Borrar el contenedor no los toca.

## Comprobar que va

```sh
curl http://IP_DEL_NAS:8000/health
```

Debería salir algo con `"status":"ok"`. Si `"voice"` sale en `false`, es lo
normal en un NAS: no hay tarjeta de sonido.