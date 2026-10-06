# 🤖 JARVIS

> **Asistente personal de escritorio para Windows con IA, voz y una interfaz holográfica inspirada en J.A.R.V.I.S. de Iron Man.**

JARVIS es un asistente personal de escritorio diseñado para ejecutarse en un PC con Windows. El proyecto combina una interfaz desarrollada con **Electron** con un cerebro de IA desarrollado en **Python + FastAPI**, comunicándose mediante **WebSocket local**.

El objetivo es evolucionar desde un asistente conversacional hasta un asistente capaz de interactuar con el sistema operativo, analizar la pantalla, controlar aplicaciones y ofrecer interacción mediante voz.

---

## ✨ Características

### 🧠 Inteligencia artificial

Actualmente el MVP utiliza **Groq** como proveedor de IA.

El sistema está preparado para evolucionar hacia un sistema de múltiples proveedores:

- **Groq** → respuestas rápidas y conversación.
- **Gemini** → tareas complejas y análisis de visión.
- **OpenRouter** → proveedor de respaldo.
- **Ollama** → alternativa local para funcionamiento sin conexión.

El MVP actual utiliza el modelo:

```text
llama-3.3-70b-versatile
```

La implementación actual del brain utiliza FastAPI, WebSocket, `groq` y `python-dotenv`.

---

## 🖥️ Interfaz

La aplicación utiliza **Electron** para proporcionar una aplicación de escritorio nativa.

La interfaz está diseñada siguiendo una estética:

- 🔵 Azul/cian.
- 🤖 Inspirada en JARVIS.
- 🌐 Interfaz tipo HUD.
- 🔮 Orbe holográfico.
- 📊 Panel de información lateral.
- 🖥️ Overlay de escritorio.

La especificación del proyecto contempla un orbe holográfico basado en Three.js, una línea de audio reactiva y un panel lateral para mostrar las acciones que está realizando JARVIS.

---

## 💬 Conversación

El MVP permite mantener una conversación con JARVIS mediante texto.

El brain recibe los mensajes mediante un WebSocket:

```text
Electron → WebSocket → FastAPI → Groq
                                      ↓
Electron ← WebSocket ← respuesta
```

El contexto actual utiliza un prompt del sistema junto con el historial de conversación disponible.

JARVIS responde principalmente en español y mantiene una personalidad propia, con un tono inspirado en el personaje original.

---

## 🗄️ Memoria

El proyecto utiliza **SQLite** para almacenar el historial de conversaciones.

La aplicación Electron crea una base de datos `jarvis.db` dentro del directorio de datos del usuario y mantiene una tabla de mensajes con:

- ID.
- Rol (`user`, `assistant`, `system`).
- Contenido.
- Fecha de creación.


La especificación futura contempla añadir memoria semántica mediante **Qdrant** cuando el historial sea suficientemente grande.

---

## 🔐 API Keys

JARVIS utiliza variables de entorno para las claves de los diferentes servicios.

Entre las variables previstas se encuentran:

```env
GROQ_API_KEY=
GEMINI_API_KEY=
OPENROUTER_API_KEY=
WEATHER_API_KEY=
```

También se contemplan futuras integraciones con:

```env
GOOGLE_CALENDAR_CREDENTIALS_PATH=
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
OLLAMA_HOST=http://localhost:11434
```

Las claves no deben almacenarse directamente en el código ni subirse a GitHub.

El `.gitignore` del proyecto excluye `.env`, entornos virtuales, ejecutables, modelos ONNX y otros archivos generados.

---

## ⚙️ Arquitectura

El proyecto está dividido principalmente en dos partes:

```text
JARVIS/
│
├── app/                  # Aplicación/interfaz Electron
│
├── codigo-mvp/           # Implementación inicial del proyecto
│   └── brain/            # Cerebro de IA en Python
│       ├── main.py
│       ├── requirements.txt
│       └── .env.example
│
├── JARVIS/               # Componentes principales del proyecto
│
├── camara-probe/         # Pruebas relacionadas con cámara
│
├── .opencode/            # Skills/configuración para desarrollo
│
├── JARVIS-SPEC.md        # Especificación completa
├── main.js               # Proceso principal de Electron
├── instalar-jarvis.cmd   # Script auxiliar de instalación
└── .gitignore
```

La especificación técnica define como arquitectura objetivo:

```text
Electron
   │
   │ WebSocket
   ▼
Python / FastAPI
   │
   ├── Model Router
   ├── Voice
   ├── Vision
   ├── Tools
   ├── Memory
   └── Integrations
```


---

## 🚀 Instalación

### Requisitos

Actualmente el proyecto está orientado principalmente a **Windows**.

Se necesita:

- Windows.
- Python **3.11 o 3.12**.
- Node.js.
- npm.
- Una API key de Groq para utilizar el MVP.
- Micrófono para las futuras funciones de voz.

El propio sistema de preparación de JARVIS comprueba la instalación de Python y crea automáticamente un entorno virtual para el brain.

---

### 1. Clonar el repositorio

```bash
git clone https://github.com/lucillas0/JARVIS.git
cd JARVIS
```

### 2. Configurar el brain

Entrar en el directorio del MVP:

```bash
cd codigo-mvp/brain
```

Crear un entorno virtual:

```bash
python -m venv venv
```

Activarlo en Windows:

```cmd
venv\Scripts\activate
```

Instalar las dependencias:

```bash
pip install -r requirements.txt
```

Las dependencias actuales incluyen:

```text
fastapi
uvicorn
groq
python-dotenv
websockets
```


---

### 3. Configurar Groq

Crear un archivo:

```text
.env
```

con:

```env
GROQ_API_KEY=TU_CLAVE_DE_GROQ
```

**No subas nunca este archivo a GitHub.**

---

### 4. Ejecutar el brain

Desde:

```text
codigo-mvp/brain
```

ejecutar:

```bash
python -m uvicorn main:app --host 127.0.0.1 --port 8000
```

El servicio WebSocket estará disponible en:

```text
ws://127.0.0.1:8000/ws
```

La aplicación Electron utiliza este servicio para comunicarse con el cerebro de JARVIS.

---

## 📦 Instalador de Windows

El proyecto también contempla distribución mediante un instalador `.exe`.

Existe un script:

```text
instalar-jarvis.cmd
```

que automatiza parte del proceso de instalación y arranque en Windows.

Los instaladores y ejecutables generados no se almacenan en el repositorio Git debido a su tamaño. El `.gitignore` excluye explícitamente:

```text
*.exe
*.onnx
dist/
build/
dist-installer/
```


Para distribuir versiones compiladas se recomienda utilizar **GitHub Releases** en lugar de subir los ejecutables directamente al repositorio.

---

# 🎙️ Sistema de voz

Una de las principales líneas de desarrollo de JARVIS es convertirlo en un asistente completamente controlado por voz.

La arquitectura prevista incluye:

### Speech-to-Text

**Groq Whisper**

Permite transformar la voz del usuario en texto.

### Text-to-Speech

**Piper TTS**

Sistema de síntesis de voz local pensado para utilizar una voz masculina en español.

### Wake Word

Detección local de:

```text
"Jarvis"
```

La idea es que el usuario pueda decir:

> "Jarvis"

y comenzar una conversación sin necesidad de utilizar teclado.

También está previsto soportar **barge-in**, permitiendo interrumpir a JARVIS mientras está hablando.

---

# 👁️ Análisis de pantalla

Una de las funciones previstas es permitir que JARVIS pueda analizar lo que aparece en la pantalla.

Se contemplan dos modos:

### Modo continuo

JARVIS captura periódicamente la pantalla para conocer mejor el contexto.

### Modo bajo demanda

El usuario puede preguntar, por ejemplo:

```text
"Jarvis, ¿qué aparece en mi pantalla?"
```

La imagen se procesa en memoria y no se almacena como archivo.

También se contempla poder activar y desactivar esta función mediante comandos de voz.

---

# 🖥️ Control del sistema

El objetivo final es permitir que JARVIS pueda realizar acciones sobre Windows.

La implementación está planificada por fases:

1. Abrir aplicaciones y archivos.
2. Buscar archivos en el sistema.
3. Abrir el navegador.
4. Gestionar ventanas.
5. Controlar multimedia.
6. Controlar Spotify.
7. Realizar acciones de red y energía.
8. Automatizaciones más avanzadas.

Las acciones sensibles deberán solicitar confirmación antes de ejecutarse.

---

# 🔒 Seguridad

Las acciones de riesgo no deben ejecutarse directamente desde el modelo de IA.

La arquitectura prevista incluye:

```text
Usuario
   ↓
JARVIS
   ↓
LLM
   ↓
Tool Manager
   ↓
Risk Engine
   ↓
Confirmación
   ↓
Acción
```

Las acciones sensibles utilizan:

- Confirmación mediante voz.
- Confirmación mediante interfaz gráfica.
- Timeout de 30 segundos.
- Registro en el sistema de auditoría.

La especificación establece además una retención de 30 días para los registros de auditoría.

---

# 🔌 Integraciones previstas

El proyecto está diseñado para incorporar diferentes servicios:

| Servicio | Función |
|---|---|
| 🧠 Groq | Inteligencia artificial |
| 🤖 Gemini | Razonamiento y visión |
| 🌐 OpenRouter | Proveedor alternativo |
| 🦙 Ollama | IA local/offline |
| 📅 Google Calendar | Consultar y gestionar eventos |
| 🎵 Spotify | Música y playlists |
| 🌦️ Weather API | Información meteorológica |
| 📧 Gmail | Lectura de correos |
| 💬 WhatsApp | Lectura de mensajes/notificaciones |

Estas integraciones forman parte de la arquitectura prevista y se irán incorporando progresivamente.

---

# 🗺️ Roadmap

### ✅ Implementado

- [x] Aplicación Electron.
- [x] Brain desarrollado en Python.
- [x] FastAPI.
- [x] Comunicación WebSocket.
- [x] Integración inicial con Groq.
- [x] Historial mediante SQLite.
- [x] Contexto básico de conversación.
- [x] Preparación automática del entorno Python.
- [x] Configuración mediante variables de entorno.

### 🚧 En desarrollo / siguientes fases

- [ ] Wake word local.
- [ ] Reconocimiento de voz.
- [ ] Piper TTS.
- [ ] Conversación continua.
- [ ] Barge-in.
- [ ] Fallback con Ollama.
- [ ] Sistema de permisos y riesgos.
- [ ] Control del sistema operativo.
- [ ] Búsqueda web.
- [ ] Model Router.
- [ ] Google Calendar.
- [ ] Spotify.
- [ ] Gmail.
- [ ] WhatsApp.
- [ ] Análisis de pantalla.
- [ ] Motor proactivo.
- [ ] Memoria semántica con Qdrant.
- [ ] HUD holográfico completo.
- [ ] Instalador final para Windows.

La prioridad de estas fases está definida en `JARVIS-SPEC.md`.

---

# 📁 Documentación

El repositorio incluye una especificación detallada del proyecto:

```text
JARVIS-SPEC.md
```

Este documento describe:

- Arquitectura.
- Personalidad.
- Stack tecnológico.
- Sistema de voz.
- Análisis de pantalla.
- Interfaz.
- Control de Windows.
- Seguridad.
- Integraciones.
- Memoria.
- Protocolo WebSocket.
- Variables de entorno.
- Plan de pruebas.
- Roadmap.

---

# ⚠️ Estado del proyecto

JARVIS se encuentra actualmente en **desarrollo activo**.

El núcleo conversacional funciona, pero muchas de las capacidades descritas en la especificación todavía forman parte del roadmap.

Por este motivo, algunas características mencionadas en este README pueden estar todavía en fase de desarrollo.

---

# 👨‍💻 Autor

**lucillas0**

Repositorio:

**GitHub:** https://github.com/lucillas0/JARVIS

---

# 📄 Licencia

Actualmente no se ha definido una licencia de código abierto en el repositorio.

Si el proyecto se publica para uso de terceros, se recomienda añadir un archivo `LICENSE` con la licencia elegida.

---

> *"Sistemas en línea. ¿En qué puedo ayudarte?"* 🤖
