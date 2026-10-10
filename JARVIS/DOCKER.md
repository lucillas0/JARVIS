# JARVIS en Docker / Synology

Este directorio monta el **cerebro** de JARVIS como servidor: todo lo de
escritorio (Electron, el notch, las manos, la voz) se queda en tu PC y el
servidor se encarga de pensar, recordar y responder.

## Qué hay aquí

| Fichero | Para qué |
|---|---|
| `instalar.sh` | Instala, construye y arranca. Detecta Synology, comprueba puertos y espera a que el servidor responda. |
| `configuracion.env.example` | Todas las opciones, comentadas y con los valores por defecto reales. Se copia a `.env`. |
| `docker-compose.yml` | El proyecto. Es el que lee Container Manager al crear el proyecto. |
| `Dockerfile` | Imagen mínima: Python + el cerebro, sin nada de escritorio. |

## Lo más rápido

```sh
cp configuracion.env.example .env
# Edita .env y pega al menos una clave: Groq, Gemini u OpenRouter
./instalar.sh
```

El script hace el resto: crea `data/` y `assets/`, ajusta los permisos, construye
la imagen, arranca el contenedor y espera a que `/health` responda. Al final te
da la dirección: `http://IP_DEL_EQUIPO:8000`.

## Synology / Xenology (Container Manager)

Container Manager no ejecuta scripts: se instala desde su interfaz. El script
sigue siendo útil para crear la configuración.

1. Copia esta carpeta a una carpeta compartida del NAS, por ejemplo
   `/volume1/docker/jarvis`.
2. En **Container Manager → Proyecto → Crear**, elige esa carpeta como ruta.
3. Fuente: *usar un archivo `docker-compose.yml` existente*.
4. Container Manager lee el `.env` de la misma carpeta, así que no repitas nada
   en la interfaz.
5. Pulsa **Crear**. La primera vez construye la imagen y tarda.

Detalles que importan en Synology:

- **Permisos.** El `.env` lleva `PUID` y `PGID` con los tuyos. Sin eso Docker
  crea los ficheros como `root` y luego no puedes borrarlos desde DSM.
- **Construcción local.** Si el NAS no puede construir la imagen (falta
  memoria), constrúyela en otro equipo y cárgala; o usa la imagen de la arquitectura
  correcta (`x86_64` en la mayoría, `arm64` en los modelos con Realtek).
- **Abrir el puerto.** Container Manager → Proyecto → jarvis → Puerto. El
  compose lo publica en `JARVIS_PORT` (8000 por defecto).

Para repasar los pasos desde la consola:

```sh
./instalar.sh --registrar
```

## Comandos

```sh
./instalar.sh              # instala y arranca
./instalar.sh --puerto 9000
./instalar.sh --reconstruir  # rehace la imagen desde cero
./instalar.sh --estado       # como va
./instalar.sh --registros    # log en vivo
./instalar.sh --parar
```

## Configuración

Todo vive en `.env`, que es el mismo archivo para Docker y para Container
Manager. Las secciones:

1. **Claves de modelos** — hace falta al menos una. Sin ellas el servidor
   arranca pero no contesta.
2. **Modelos** — qué modelo se usa para cada tipo de tarea.
3. **Red** — puerto, `BRAIN_HOST` (debe ser `0.0.0.0` dentro del contenedor) y
   la interfaz de Docker.
4. **Contenedor** — `PUID`, `PGID`, `TZ` y límite de memoria.
5. **Memoria** — ventana de contexto y retención de registros.
6. **Integraciones** — Discord, Spotify, Calendar, tiempo, VirusTotal.
7. **Fallback local** — Ollama. Ojo: dentro de un contenedor `localhost` es el
   propio contenedor; usa la IP del host.
8-10. **Equipos remotos** — Raspberry, Nextcloud, campus, VM.
11. **Voz y visión** — apagadas por defecto: un NAS no tiene ni pantalla ni micro.

El `.env` contiene claves. `instalar.sh` lo deja en `chmod 600`; no lo subas a
ningún repositorio.

## Datos

Todo persiste en `./data` (SQLite, memoria, registros) y sobrevive a las
actualizaciones. Para hacer copia: para el contenedor y copia la carpeta.

## Qué NO incluye la imagen

Electron, audio de escritorio, drivers de Windows y los modelos locales de voz
(torch pesa más que la propia imagen). El cerebro los importa dentro de
funciones, así que no molestan hasta que se usan; si en tu NAS los necesitas,
añádelos a `brain/requirements-server.txt`.

## Seguridad

- El contenedor corre sin root y sin capabilities (`cap_drop: ALL`,
  `no-new-privileges`).
- La configuración se monta **solo lectura**.
- **El servidor no tiene autenticación.** Los endpoints `/api/*` (incluidos
  `/api/apps` y `/api/recap`) responden a cualquiera que llegue. No lo publiques
  en internet: ponlo solo en LAN o delante un proxy con contraseña.
