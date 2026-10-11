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
- [El modo servidor](#el-modo-servidor)
- [La interfaz](#la-interfaz)
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

Container Manager no ejecuta scripts, así que se instala desde su interfaz.
Copia esta carpeta a una carpeta compartida del NAS y, en **Proyecto →
Crear**, apúntala como ruta del proyecto con el `docker-compose.yml` que trae.
Container Manager lee el `.env` de la misma carpeta.

El `PUID` y `PGID` del `.env` son tus ids de usuario del DSM. Si no los pones,
Docker creará los ficheros como `root` y luego no podrás borrarlos desde la
interfaz.

Más detalle en [DOCKER.md](JARVIS/DOCKER.md).

### Desde código

```sh
cd JARVIS/brain
python -m venv venv
venv/bin/pip install -r requirements-server.txt
venv/bin/python -m uvicorn main:app --host 0.0.0.0 --port 8000
```

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