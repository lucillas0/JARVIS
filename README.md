# JARVIS

**Asistente personal con IA, voz y una interfaz holográfica.** Funciona como
aplicación de escritorio en Windows, Linux y macOS, y como servidor en Docker,
NAS o cualquier servidor con Python 3.10.

> JARVIS combina una interfaz de escritorio construida con **Electron** y un
> cerebro de IA escrito en **Python + FastAPI**. Ambos se comunican por un
> WebSocket local. El escritorio es opcional: el cerebro funciona solo, y en
> servidor es lo que se despliega.

---

## Índice

- [Qué es](#qué-es)
- [Cómo funciona](#cómo-funciona)
- [Instalación](#instalación)
- [Dejarlo al día](#dejarlo-al-día)
- [El modo servidor](#el-modo-servidor)
- [La interfaz](#la-interfaz)
- [El centro de mando](#el-centro-de-mando)
- [Groq como proveedor de modelos](#groq-como-proveedor-de-modelos)
- [Desarrollo con OpenCode](#desarrollo-con-opencode)
- [La isla de Coucou](#la-isla-de-coucou)
- [Voz](#voz)
- [Integraciones](#integraciones)
- [Configuración](#configuración)
- [Estructura del proyecto](#estructura-del-proyecto)
- [Compilación](#compilación)
- [Desarrollo](#desarrollo)
- [Licencia](#licencia)

---

## Qué es

JARVIS es un asistente que vive en tu equipo. Escucha, piensa, responde hablando
y puede usar herramientas para actuar: leer ficheros, buscar en la web, mirar la
pantalla, controlar ventanas o hablarte por Discord.

Tres cosas lo definen:

- **Siempre local.** El cerebro corre en tu equipo. Solo salen los datos de
  la pregunta que le haces al proveedor de IA que configures.
- **Dos piezas separadas.** La interfaz y el cerebro se hablan por un socket.
  Puedes usar el cerebro sin escritorio, y el escritorio sin voz.
- **Una interfaz que se aparta.** La isla de la barra superior se pliega sola
  cuando no la usas y aparece cuando hay algo que contar.

---

## Cómo funciona

```
┌──────────────────────────────────────────────┐
│  Interfaz (Electron)                         │
│    isla · HUD · chat · overlay Win+J         │
└───────────────────┬──────────────────────────┘
                    │  ws://127.0.0.1:8000/ws
┌───────────────────▼──────────────────────────┐
│  Cerebro (Python + FastAPI)                  │
│    router de modelos · memoria · herramientas │
│    voz · Discord · web · sistema              │
└──────────────────────────────────────────────┘
```

El **cerebro** decide qué modelo usar para cada consulta (Groq, Gemini u
OpenRouter), recuerda lo que importa, expone unas cien herramientas y habla.
La **interfaz** es una capa fina: dibuja, escucha al usuario y pinta lo que el
cerebro dice. Si el escritorio se cierra, el cerebro sigue funcionando y
responde por Discord o por HTTP.

---

## Instalación

### Windows

1. Descarga `JARVIS-<versión>-win-x64.exe` de la [página de
   releases](https://github.com/lucillas0/JARVIS/releases).
2. Ejecútalo. Pide al menos una clave de IA.
3. Listo.

También puedes instalarlo desde el código:

```bat
instalar.bat
```

### Linux

Descarga el `AppImage`, dale permiso y ábrelo:

```sh
chmod +x JARVIS-4.0.0-x64.AppImage
./JARVIS-4.0.0-x64.AppImage
```

También hay paquetes `.deb` y `.rpm`, y un `tar.gz` para los que prefieran
descomprimir. Desde el código:

```sh
chmod +x instalar.sh
./instalar.sh
```

### macOS

Hay dos compilaciones distintas, porque son máquinas distintas:

| Tu Mac | Fichero |
|---|---|
| Apple Silicon (M1, M2, M3, M4) | `JARVIS-<versión>-mac-arm64.dmg` |
| Intel | `JARVIS-<versión>-mac-x64.dmg` |

Abre el `.dmg` y arrastra a *Aplicaciones*. Desde el código:

```sh
chmod +x instalar.sh
./instalar.sh
```

> La primera vez, macOS pediré permiso para usar el micrófono. Sin él, JARVIS
> funciona igual, pero solo de viva voz.

### Qué hace falta

- **Python 3.10 o superior.** Es lo único imprescindible: el instalador lo
  comprueba y te dice cómo instalarlo si falta.
- Alrededor de 1,5 GB de disco.
- Una clave de al menos un proveedor de IA: [Groq](https://console.groq.com/keys)
  (gratis), [Gemini](https://aistudio.google.com/apikey) o
  [OpenRouter](https://openrouter.ai/keys).

---

## El modo servidor

El modo servidor es el cerebro sin escritorio. Es lo que se despliega en un NAS
o en un servidor, y lo que se usa cuando quieres que JARVIS esté siempre
encendido.

### Con Docker

```sh
cp configuracion.env.example .env
# Edita .env y pega al menos una clave
./instalar-docker.sh
```

Levanta un contenedor con el cerebro, deja los datos en `./data` y espera a que
responda antes de darse por terminado.

### En Synology / Xenology

Lo normal es hacerlo con Container Manager, y son **cuatro clics**. El
`docker-compose.yml` viene hecho para eso: no compila nada, no usa carpetas
(a los datos van a volúmenes que crea Docker solo) y no necesita un `.env`.

1. Copia la carpeta `JARVIS` a una compartida, por ejemplo
   `/volume1/docker/jarvis`. Es lo único que hay que hacer a mano, porque DSM
   siempre pide una ruta de proyecto.
2. Por SSH, dentro de esa carpeta:

   ```sh
   sh instalar-docker.sh
   ```

   Baja la imagen, crea el `.env` con tus ids del DSM y te pregunta la clave
   de IA. Si la clave no la tienes a mano, pulsa Intro y ponla después en el
   navegador.
3. En **Container Manager → Proyecto → Crear**, apunta a esa carpeta, fuente
   *usar un archivo `docker-compose.yml` existente*, nombre `jarvis`, ydale a
   **Crear**.
4. Abre `http://IP-DEL-NAS:8000/setup` para la clave, o `http://IP-DEL-NAS:8000/`
   para la interfaz.

Para actualizar a una versión nueva: `sh instalar-docker.sh --actualizar`, y
luego *Detener* y *Construir* en el proyecto. **Los datos y las claves no se
pierden**: viven en un volumen, no en el contenedor.

> El `PUID` y el `PGID` los pone el script con los ids de tu usuario del DSM. Si
> no los tiene, Docker deja los ficheros como `root` y luego no puedes
> borrarlos desde File Station.

Si prefieres no usar la interfaz nunca, `sh instalar-nas.sh` hace lo mismo con
un solo `docker run`.

Más detalle en [DOCKER.md](JARVIS/DOCKER.md).

### Desde código

```sh
cd JARVIS/brain
python -m venv venv
venv/bin/pip install -r requirements-server.txt
venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

## Dejarlo al día

JARVIS sabe qué versión tiene puesta y la compara con la del código. Si no
cuadran, se arregla solo; y siempre tienes una forma de forzarlo.

| Cómo | Cuándo |
|---|---|
| **Nada** | La app lo comprueba al arrancar. Si se ha actualizado y el cerebro no, rehace las dependencias solo. |
| Menú de la bandeja → **Reparar instalación** | Cuando quieras forzarlo sin salir de la app. |
| `reparar.bat` (doble clic) | Lo mismo, desde el explorador. |
| `instalar.bat --reparar` | Lo mismo, con salida por consola. |
| `instalar.bat --estado` | Dice qué versión hay puesta. **No toca nada.** |

Qué arregla, en orden:

- **El entorno roto.** El caso normal: se ha actualizado Python y el entorno
  virtual sigue apuntando al intérprete viejo, que ya no existe. Tiene todas sus
  carpetas y su `python.exe`, así que parece sano, pero no arranca. Esto lo
  detecta y lo rehace.
- **La versión vieja.** Compara contra `JARVIS/VERSION`.
- **Las dependencias cambiadas.** Si el código pide un paquete nuevo, lo
  instala. Solo compara los paquetes: añadir un comentario al fichero de
  requisitos no dispara una reinstalación.

No toca nunca tus claves, ni la base de datos, ni el historial.

---

## La interfaz

Con el servidor en marcha, la interfaz vive en `http://<servidor>:8000/ui`.

Es un orbe de partículas que cambia de estado según lo que está pasando:

| Estado | Cuándo |
|---|---|
| **En espera** | No hay nada pendiente |
| **Escuchando** | El micrófono está abierto |
| **Pensando** | Hay una consulta en curso |
| **Hablando** | Está respondiendo |

El panel lateral muestra los procesos, el número de herramientas disponibles,
los proveedores activos y un registro en vivo de lo que va pasando.

Todos los botones hacen algo real: el micrófono dicta y manda el nivel de voz
al orbe, el interruptor de voz activa o silencia la locución, la tecla `M`
abre y cierra el micrófono y la barra lateral enlaza a la pizarra, la
configuración y el estado del servidor.

La página **no depende de internet**: el three.js va incluido y no usa ninguna
fuente externa. En un NAS sin conexión funciona igual.

---

## El centro de mando

`http://<servidor>:8000/live` no es un panel de tonos: es donde se manda.

- **Cabecera**: marca, recursos medidos de verdad (CPU, RAM, GPU, disco y red
  cuando existen), estado global, reloj, notificaciones y configuracion.
- **Sidebar**: Dashboard, Nueva tarea, Tareas, Agentes, Cambios, Permisos,
  Preguntas, Tests, Historial, Sistema y Asistente. Los contadores son reales y
  se ocultan cuando no hay nada.
- **Principal**: en el Dashboard, la tarea protagonista con su progreso
  explicable y sus fases, el plan de JARVIS con sus riesgos, los cambios
  recientes, la linea de tiempo y los agentes. Cada seccion tiene ademas su
  propia vista.
- **Panel derecho**: lo que te necesita ahora mismo (permisos, incidentes,
  preguntas), los permisos concedidos y denegados, los medidores del sistema y
  el historial reciente.
- **Barra de orden**: «¿Que quieres que haga JARVIS?». Va por el mismo camino
  que la voz.

Cada pantalla que se enseña se puede actuar: crear una tarea, aprobar o
modificar su plan, responder a OpenCode, autorizar con `una vez` / `siempre` /
`denegar`, programar tareas, pausarlas, adelantarlas, abrir agentes, ejecutar
tests, guardar y restaurar checkpoints, confirmar cambios, pedir un resumen de
lo que paso mientras no estabas, diagnosticar, optimizar, limpiar disco o
reiniciar el servidor de OpenCode… Todo pasa por el backend real y, si es
delicado, por el Permission Manager.

El progreso de una mision no es inventado: es *pasos con evidencia ÷ pasos del
plan*, y debajo se escribe en cuantos pasos se basa. El agente no dice «voy por
el paso 3», asi que el paso en curso se marca como el siguiente del plan.

Dos ejemplos de lo que **no** se hace: no hay gestor de Docker en este
sistema, asi que no se pintan contenedores; y `apps.py` solo detecta procesos,
asi que no se ofrece parar un agente. Lo que no tiene backend no aparece.

La documentacion completa esta en
[`JARVIS/CENTRO-MANDO.md`](JARVIS/CENTRO-MANDO.md).

---

## Groq como proveedor de modelos

Groq no es un parche: es el camino que JARVIS sigue para pensar. Por detrás hay
un **AI Gateway** (`brain/ai_gateway`) que decide qué modelo toca, reintenta
cuando tiene sentido, avisa al bus de eventos, mide lo que ha costado de verdad
y, si un proveedor falla, cambia al siguiente sin quedarse mudo.

```
conversación · voz · Discord · /api/ai/chat
        ↓
ModelRouter.respond()      ← la misma puerta de siempre
        ↓
AI Gateway                 ← modelo, reintentos, bus, métricas
        ├── Groq (API oficial por HTTP, con streaming)
        ├── Gemini · OpenRouter · Ollama (los que ya había)
        └── OpenCode         ← la programación sigue siendo suya
```

- **Un modelo por tipo de tarea**: rápido, general, razonamiento, visión y
  programación (esta última va a OpenCode, no a Groq).
- **Streaming de verdad**: por texto, cada trozo sale por el bus y JARVIS Live
  la escribe mientras se genera. Por voz no: el TTS solo recibe la respuesta
  final, nunca el razonamiento interno.
- **Errores controlados**: 401, 403, 404, 429, 5xx, timeout, red y respuesta
  malformada tienen cada uno su tratamiento, y el 429 espera lo que dice el
  proveedor antes de reintentar.
- **Permisos intactos**: el gateway no ejecuta nada. Si el modelo pide una
  herramienta, la pide el Permission Manager de siempre.
- **Métricas reales**: tokens que declara el proveedor, latencia, reintentos y
  errores. El coste sale como `unknown` porque Groq no lo devuelve y aquí no se
  inventa ningún precio.
- **En JARVIS Live**: vista **IA** con el estado de cada proveedor, el modelo
  por tarea (cambiable), prueba de conexión, catálogo y las últimas peticiones.

La clave se pone en `/setup` (se guarda en `keys.env`, fuera de la instalación)
o en el entorno. En Docker, `docker-compose.yml` ya la pasa al contenedor.
Nunca se escribe en el código, ni se imprime, ni sale por la API.

La documentación completa está en [`JARVIS/IA-GROQ.md`](JARVIS/IA-GROQ.md).

```bash
python brain/ai_gateway/test_ai.py   # 83 comprobaciones contra un doble HTTP
```

---

## Desarrollo

Las pruebas del cerebro son dos scripts que no necesitan nada instalado:

```bash
python brain/orchestrator/test_ciclo.py   # 46 comprobaciones: ciclo con OpenCode
python brain/command/test_cmd.py          # 66 comprobaciones: centro de mando
```

JARVIS no solo programme: dirige. En `http://<servidor>:8000/live`, con una idea
nueva, se abre el ciclo completo:

```
IDEA → ANÁLISIS → PLAN + RIESGOS → APROBACIÓN → OPENCODE →
EJECUCIÓN → PERMISOS → PREGUNTAS → TESTS → VALIDACIÓN → RESULTADO
```

1. **Idea.** Escribes qué quieres en *Nueva tarea* y pulsas *Crear tarea*. No se
   ejecuta nada todavía.
2. **Análisis.** JARVIS lee el proyecto de verdad (estructura, stack,
   dependencias, Git, tests) y propone un plan, sus riesgos con mitigación y una
   estimación aproximada.
3. **Aprobación.** Puedes *Modificar plan* («usa SQLite», «no uses Docker»…)
   y, cuando quieras, *Aprobar plan*.
4. **Ejecución.** JARVIS manda a OpenCode un prompt con todo el contexto y se
   queda mirando su sesión: llamadas a herramientas, ficheros que toca y tests
   que ejecuta.
5. **Permisos.** Si OpenCode pide permiso, JARVIS Live lo enseña con las tres
   opciones que el propio OpenCode entiende: *Denegar*, *Una vez*, *Siempre*.
   JARVIS no se salta su sistema de permisos.
6. **Preguntas.** Si OpenCode necesita una decisión, aparece su tarjeta con las
   opciones y un campo de respuesta libre. La respuesta se le devuelve a
   OpenCode y sigue trabajando.
7. **Resultado.** Comprobaciones, ficheros tocados, tests leídos de su salida y
   tiempos separados: total, ejecutando y esperando a ti.

También funciona hablando: di «JARVIS, quiero que añadas autenticación con
Google» y se crea la tarea; si no estás delante, los avisos y las respuestas van
por Discord.

Si OpenCode no está instalado o su servidor no está en marcha, el panel lo dice
con todas las letras. Nunca hay progreso inventado.

Todo el código está en `brain/orchestrator/`:

| Módulo | De qué se ocupa |
|---|---|
| `service.py` | Localiza el servidor real de OpenCode (registro, `service start`, `serve`) |
| `client.py` | Cliente HTTP de su API, sin dependencias nuevas |
| `adapter.py` | `OpenCodeAdapter`: sesión, prompt, permisos, preguntas, cambios |
| `planner.py` | Análisis del proyecto, diseño, riesgos y prompt final |
| `manager.py` | El orquestador: estados, tiempos, validación y resumen |
| `store.py` | Las tareas en SQLite (sobreviven al reinicio) |
| `routes.py` | API `/api/dev` que consume JARVIS Live |
| `test_ciclo.py` | Prueba del ciclo entero contra un doble de la API |

Para comprobar que el cableado sigue bien:

```bash
python brain/orchestrator/test_ciclo.py
```

---

## La isla de Coucou

La barra superior no es un invento: es el Coucou, importado del proyecto de
[Louis-CFM](https://github.com/Louis-CFM/coucou) y conectado al cerebro de
JARVIS.

Lo que se ha traído tal cual:

- La **máquina de estados** de cuatro estados (`hidden`, `petit`, `home`,
  `coucou`) con sus tiempos: 15 s para plegarse, 60 s para ocultarse.
- El **motor de dibujo de Mochi**, con sus 11 estados y sus 13 atuendos,
  incluido el modo automático por estaciones.
- La **hoja de estilo completa**, byte a byte.
- Los textos en español del catálogo original.

Lo que se ha adaptado: el puente, que en Coucou habla con Rust y aquí habla con
el WebSocket del cerebro, y las pastillas, que en Coucou se llenan con los hooks de
Claude Code y aquí se llenan con los procesos que el servidor detecta.

Comprobado con la matriz de los 11 estados × 13 atuendos, sin un solo fallo.

---

## Voz

- **Transcripción**: Whisper, con palabra de activación configurable.
- **Síntesis**: Piper, con una voz española por defecto.
- **Control**: silencio de conversación, velocidad y modelo, todo ajustable.

En servidor la voz no suele hacer falta y se puede apagar sin que afecte a
nada más.

---

## Integraciones

| Integración | Estado |
|---|---|
| Discord | DM privado con el propietario, con puente de voz |
| Búsqueda web | DuckDuckGo con reintentos y caché, y Wikipedia primero en preguntas enciclopédicas |
| Campus | Índice de apuntes y herramientas por asignatura |
| Máquinas virtuales | Control por SSH |
| Spotify | Reproducción y estado de la música |
| Cámaras | Instantánea y vídeo en directo |
| Telegram, móvil | Avisos y control |

---

## Configuración

Todo se configura por entorno, en un solo archivo.

- **Escritorio y modo lite:** `JARVIS/brain/.env`.
- **Servidor:** `.env` en la carpeta del proyecto, con el ejemplo en
  [configuracion.env.example](JARVIS/configuracion.env.example).

Las claves que se pueden poner están documentadas en ese ejemplo, agrupadas y
con los valores por defecto reales del código. Para ponerlas en cualquier
momento sin reinstalar:

```sh
python instalar.py --solo-claves
```

> El `.env` va en claro. No lo subas a ningún repositorio ni lo compartas. El
> instalador lo deja en permisos `600` cuando el sistema lo permite.

---

## Estructura del proyecto

```
JARVIS/
├── app/                 Interfaz de escritorio (Electron)
│   ├── main.js          Ventanas, atajos, puente con la isla
│   ├── preload.js       Puente seguro entre renderer y proceso principal
│   ├── ui/
│   │   ├── notch/       La isla, y dentro el Coucou original
│   │   ├── overlay/     Superposición a pantalla completa (Win+J)
│   │   └── vendor/      three.js
│   └── scripts/         Utilidades de compilación
├── brain/               Cerebro (Python)
│   ├── main.py          Servidor FastAPI y WebSocket
│   ├── config.py        Configuración y claves
│   ├── model_router.py  Groq → Gemini → OpenRouter
│   ├── ui/              Interfaz web del modo servidor
│   ├── tools/           Catálogo de herramientas
│   ├── integrations/    Campus, máquinas virtuales, Discord
│   ├── memory/          Memoria, contexto y autoreparación
│   └── vision/          Visión por cámara
├── assets/              Iconos, sonidos e imágenes
├── Dockerfile           Imagen del servidor
├── docker-compose.yml   Proyecto para Container Manager
├── instalar.py          Instalador (Windows, Linux, macOS)
├── instalar.sh          Lanzador en Unix
├── instalar.bat         Lanzador en Windows
└── instalar-docker.sh   Instalador del modo servidor
```

---

## Compilación

```sh
npm install --prefix app

npm --prefix app run dist:win        # Windows
npm --prefix app run dist:linux      # Linux x64
npm --prefix app run dist:linux:arm  # Linux arm64
npm --prefix app run dist:mac        # macOS Intel
npm --prefix app run dist:mac:arm    # macOS Apple Silicon
```

> Cada sistema se compila en su propio sistema. Un `.dmg` no se genera desde
> Windows ni un `AppImage` desde macOS. Para las cuatro plataformas a la vez,
> existe el flujo de trabajo de [build.yml](.github/workflows/build.yml), que
> compila cada una en su runner y publica una release con todo.

---

## Desarrollo

```sh
# Interfaz en modo desarrollo
npm --prefix app start

# Cerebro con recarga
cd JARVIS/brain && venv/bin/python -m uvicorn main:app --reload

# Pruebas
npm --prefix app test
```

El repositorio incluye pruebas de la geometría de la isla, de los diagnósticos
del instalador y de la matriz de Mochi, para que los cambios no rompan nada sin
avisar.

---

## Licencia

MIT.