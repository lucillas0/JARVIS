# JARVIS — Especificación completa del proyecto (v4, exhaustiva)

> Documento de handoff para que un agente de código (OpenCode) continúe la
> construcción. Todas las decisiones de esta versión fueron confirmadas
> explícitamente por el usuario. No quedan huecos relevantes de diseño.

## 1. Visión y personalidad

Asistente personal de escritorio para **Windows**, con voz y texto,
**personalidad marcada — humor y sarcasmo estilo JARVIS de las películas de
Iron Man**. Controla el sistema operativo de forma avanzada, actúa por
iniciativa propia según contexto, y tiene una interfaz visual tipo
holograma (orbe).

- **Idioma**: español principal; acepta comandos sueltos en inglés.
- **Uso**: personal, un único PC con Windows, un solo monitor.
- **Instalación**: instalador clásico `.exe`, corre siempre en segundo
  plano, optimizado para consumo mínimo en reposo.
- **Reposo visual**: overlay pequeño y siempre visible en una esquina de la
  pantalla.
- **Sin cifrado**: historial y logs se guardan en claro en disco — es un PC
  personal, no se requiere cifrado en reposo.
- **Saludo de arranque**: al iniciar Windows y activarse JARVIS, saluda en
  voz con algo tipo "Sistemas en línea" (tono acorde a su personalidad).
- **Entrada exclusivamente por voz**: no hay atajo de teclado para escribir
  como alternativa — todo pasa por el wake word y la voz.
- **Configuración de API keys**: el instalador `.exe`, en su **primer
  arranque**, muestra un asistente simple que pide de una vez las tres
  claves — Groq, Gemini y OpenRouter — y las guarda en `brain/.env`. No hace
  falta editar el archivo a mano salvo que se quiera cambiar algo después.
  Activación/desactivación de módulos avanzados sigue siendo por archivo de
  configuración (`.json`), sin panel visual completo.

## 2. Stack tecnológico

- **App/UI**: Node.js + Electron.
- **Brain / IA**: servicio Python (FastAPI + WebSocket) en segundo plano.
- **Comunicación interna**: WebSocket local; la app nunca llama a las APIs
  de IA directamente.
- **Model Router de 3 niveles** (por proveedor, no solo por tarea):
  1. **Groq** (`llama-3.1-8b-instant`) — primer intento, para respuestas
     conversacionales simples y rápidas.
  2. **Gemini** — para tareas pesadas: razonamiento complejo, planificar una
     acción con varios pasos, interpretar resultados de una búsqueda web o
     analizar lo que ve en la pantalla (sección 3.1).
  3. **OpenRouter** — última alternativa, solo si Groq y Gemini fallan o no
     responden (timeout/error de API).
- **Fallback offline** (cuando fallan los tres, o no hay internet): modelo
  pequeño local vía Ollama (`llama3.2:1b` o similar). Limitado — sin
  herramientas ni acciones del sistema, solo conversación simple — pero
  JARVIS nunca se queda mudo del todo.
- **Memoria**: SQLite para historial (ya implementado). Contexto "fresco":
  últimos **10-15 mensajes**. Qdrant para memoria semántica cuando el
  historial supere eso.

## 3. Voz

- **STT**: Groq Whisper (nube).
- **TTS**: **Piper TTS** — gratuito, 100% local/CPU, licencia MIT, voces
  nativas en español. Elegir un modelo de voz masculina en español de tono
  grave y calmado.
- **Wake word**: local (ej. openWakeWord), palabra "Jarvis", sin
  distinción de hablante.
- **Modo de conversación**: se activa diciendo "Jarvis" **una sola vez**;
  a partir de ahí sigue escuchando y respondiendo sin necesidad de repetir
  la palabra, hasta que detecta que la conversación terminó (silencio
  prolongado) y vuelve a modo de espera del wake word.
- **Barge-in**: si el usuario habla mientras JARVIS está hablando, JARVIS
  se calla inmediatamente y escucha lo nuevo — sin esperar a terminar su
  frase.

## 3.1 Visión de pantalla (Screen Analysis)

JARVIS puede ver y analizar la pantalla del usuario, y hablar sobre esa
información cuando se le pregunta.

- **Modo continuo**: captura periódica de la pantalla (ej. cada pocos
  segundos, ajustable) que se envía como contexto adicional al Proactive
  Engine — así puede entender mejor qué está haciendo el usuario (ej. en
  qué documento trabaja, qué error muestra el IDE) más allá de solo el
  nombre del proceso activo.
- **Modo bajo pedido**: cuando el usuario pregunta algo como "¿qué dice
  esto?" o "mira mi pantalla y dime...", usa la captura más reciente para
  responder con detalle.
- **Privacidad — no negociable**: las capturas **no se guardan en disco ni
  en el historial**; solo se procesan en memoria (se envían al modelo de
  visión y se descartan). Solo la *descripción/resumen* que genera el
  modelo puede quedar en el contexto de la conversación, nunca la imagen
  cruda.
- **Control fácil de pausa**: un comando de voz simple (ej. "Jarvis, deja
  de ver mi pantalla") debe desactivar el modo continuo al instante, sin
  reiniciar la app. Vuelve a activarse con el comando contrario.
- El análisis de pantalla se enruta al nivel "Gemini" del Model Router
  (sección 2), ya que requiere capacidad de visión y suele ser una tarea
  más pesada que una respuesta de texto simple.

## 4. Interfaz (UI/HUD)

- Base visual: el orbe de
  [ultron-by-sagar-builds](https://github.com/SAGAR-TAMANG/ultron-by-sagar-builds)
  (Three.js), adaptado y embebido en Electron.
- **Paleta**: azul/cian, estilo clásico Iron Man/JARVIS.
- **Reposo**: overlay pequeño en una esquina de la única pantalla.
- **Activo**: línea/onda de voz debajo del orbe, reactiva al audio de TTS.
- **Panel lateral derecho**: muestra la acción en curso; desaparece al
  terminar, sin acumular historial visual.
- Control por gestos de mano (parte del repo original): opcional.

## 5. Control del sistema operativo (Windows)

Prioridad de implementación:

1. Abrir apps y archivos — búsqueda en **todo el disco**.
2. Navegador — el **predeterminado del sistema**.
3. Gestión de ventanas y escritorio.
4. Multimedia — Spotify y volumen.

Después: red, energía y automatizaciones complejas (nivel avanzado
completo).

### Confirmación de acciones sensibles

- **Doble canal siempre**: JARVIS lo anuncia en voz alta Y muestra un popup
  que se aprueba con clic.
- **Timeout: 30 segundos** — si no hay respuesta, la acción se cancela
  automáticamente.
- Acciones sensibles: borrar archivos, cambios de red/energía, cualquier
  automatización que afecte a otros procesos.

## 6. Motor de permisos y riesgo (Permission & Risk Engine)

- Ninguna acción se ejecuta directo desde el LLM — siempre pasa por el Tool
  Manager, que consulta al Risk Engine.
- Auditoría de todo lo que JARVIS ejecuta, sin excepción.
- **Retención de logs de auditoría: 30 días**, con rotación automática
  (se borra lo más viejo).
- El timeout de 30s y la doble confirmación aplican siempre a lo sensible,
  incluso cuando la acción viene del Proactive Engine.

## 7. Web Intelligence

Búsqueda de información actual (clima, noticias, precios) cuando la
pregunta lo requiera. Herramienta de riesgo bajo, sin confirmación.

## 8. Proactividad (Proactive Engine)

- **Detección de contexto**: por procesos/ventanas activas (ej. VSCode =
  programando, un juego en foco = gaming) **y ahora también por el análisis
  continuo de pantalla** (sección 3.1), que le da más matiz (ej. distinguir
  si en VSCode estás debuggeando un error o simplemente leyendo código). No
  cruza con Google Calendar para esto.
- **Nivel de acción**: libertad para actuar según contexto (silenciar
  notificaciones en reunión, pausar música), no solo avisar.
- Lo "sensible" siempre pasa por la doble confirmación con timeout de 30s,
  aunque la iniciativa sea del Proactive Engine.

## 9. Integraciones externas

- **Calendario**: Google Calendar — consulta **y también crear/editar
  eventos** por el usuario.
- **Spotify**: cuenta personal, API oficial — control de reproducción,
  **búsqueda de canciones y creación/edición de playlists**.
- **WhatsApp**: **solo lectura** de mensajes/notificaciones — sin envío,
  para evitar el riesgo de baneo de Meta por automatización.
- **Gmail**: lectura de correos — resumen de remitente y contenido, para el
  Modo Despertar (sección 14) y bajo pedido en cualquier momento. Sin
  capacidad de enviar correos por el usuario en esta fase.
- **Clima**: API estándar de clima, con **detección automática de
  ubicación** (no fija, no preguntada cada vez).

## 10. Estructura de carpetas objetivo

```
jarvis/
├── app/              ← Electron + UI (orbe azul/cian + HUD lateral)
│   └── setup-wizard/ ← pantalla de primer arranque (pide Groq/Gemini/OpenRouter)
├── brain/            ← Python: orchestrator, model router, context, tools
│   ├── voice/        ← wake word local, STT (Groq Whisper), TTS (Piper)
│   ├── vision/       ← captura de pantalla + análisis (modo continuo/on-demand)
│   ├── tools/        ← acciones sobre el SO, cada una con su nivel de riesgo
│   ├── memory/       ← SQLite + Qdrant
│   ├── integrations/ ← Google Calendar (r/w), Spotify (control+búsqueda+
│   │                    playlists), WhatsApp (solo lectura), clima, web
│   └── fallback/     ← modelo local pequeño (Ollama) para modo sin conexión
└── shared/           ← contratos/tipos comunes
```

## 11. Estado actual — punto de partida

`codigo-mvp/` ya implementa y funciona:
- App Electron con chat de texto + SQLite para historial.
- Brain en Python (FastAPI + WebSocket) conectado a Groq.
- Context manager básico (system prompt + últimos N turnos).
- Patrón de Tool Manager dejado preparado (vacío).

**Pendiente, en este orden**:
1. Voz completa: wake word local + Groq Whisper + Piper TTS (voz grave) +
   modo de conversación continua + barge-in.
2. Fallback offline con modelo local pequeño para cuando falle Groq/internet.
3. Permission & Risk Engine (doble confirmación, timeout 30s, auditoría con
   retención de 30 días).
4. Acciones del SO en el orden de la sección 5.
5. Web Intelligence.
6. Model Router (rápido vs. potente, ambos en Groq).
7. Integraciones: Google Calendar (r/w), Spotify (control + búsqueda +
   playlists), WhatsApp (solo lectura), clima (ubicación automática).
8. Proactive Engine (detección por procesos/ventanas, acción con libertad
   salvo lo sensible).
9. Memoria semántica (Qdrant).
10. UI/HUD completo: overlay de reposo, orbe, línea de voz, panel lateral.
11. Empaquetado como instalador `.exe` para Windows.

## 12. Contratos técnicos (para que OpenCode no tenga que inventarlos)

### 12.1 Esquema SQLite (`brain/db/jarvis.db`)

```sql
CREATE TABLE messages (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  role TEXT NOT NULL CHECK(role IN ('user','assistant','system')),
  content TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE audit_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  action_type TEXT NOT NULL,       -- ej. 'open_app', 'delete_file', 'spotify_play'
  payload TEXT,                    -- JSON con los parámetros de la acción
  risk_level TEXT NOT NULL CHECK(risk_level IN ('low','medium','sensitive')),
  confirmed BOOLEAN,               -- NULL si no aplicaba confirmación
  result TEXT CHECK(result IN ('success','failed','cancelled','timeout')),
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
-- Rotación: borrar filas de audit_log con created_at > 30 días, vía job
-- programado al arrancar el brain.

CREATE TABLE settings (
  key TEXT PRIMARY KEY,
  value TEXT
);
```

### 12.2 Protocolo WebSocket (`app` ↔ `brain`, puerto 8000, ruta `/ws`)

**App → Brain** (mensaje del usuario, texto o transcripción de voz):
```json
{
  "type": "user_message",
  "message": "abre spotify y pon algo de rock",
  "input_mode": "voice",
  "history": [ { "role": "user", "content": "..." } ]
}
```

**Brain → App** (respuesta conversacional):
```json
{ "type": "assistant_reply", "text": "Ahí lo tienes, jefe." }
```

**Brain → App** (acción en curso, para el panel lateral — sección 4):
```json
{ "type": "action_status", "label": "Abriendo Spotify", "state": "running" }
```
Al terminar: `{ "type": "action_status", "label": "Abriendo Spotify", "state": "done" }`
— el frontend borra el panel al recibir `state: "done"` o `"failed"`.

**Brain → App** (solicitud de confirmación, doble canal — sección 5):
```json
{
  "type": "confirmation_request",
  "action_id": "a1b2c3",
  "description": "Voy a eliminar 'informe_final.docx'. ¿Confirmas?",
  "timeout_seconds": 30
}
```

**App → Brain** (respuesta a la confirmación):
```json
{ "type": "confirmation_response", "action_id": "a1b2c3", "approved": true }
```
Si no llega respuesta en 30s, el brain cancela la acción y emite
`action_status` con `state: "cancelled"`.

### 12.3 Formato de herramientas (Tool Manager)

Cada herramienta se define en `brain/tools/` con esta forma:

```python
{
  "name": "open_app",
  "risk_level": "low",              # low | medium | sensitive
  "requires_confirmation": False,   # True fuerza el doble canal
  "parameters": {"app_name": "str"},
  "handler": open_app_handler
}
```

El LLM nunca llama `handler` directo: propone `{"tool": "open_app",
"parameters": {...}}`, el orchestrator lo valida contra el Risk Engine, y
solo entonces se ejecuta (con o sin confirmación según `risk_level`).

### 12.4 Variables de entorno (`brain/.env`)

```
GROQ_API_KEY=                      # pedida en el asistente de primer arranque
GEMINI_API_KEY=                    # pedida en el asistente de primer arranque
OPENROUTER_API_KEY=                # pedida en el asistente de primer arranque
GOOGLE_CALENDAR_CREDENTIALS_PATH=
SPOTIFY_CLIENT_ID=
SPOTIFY_CLIENT_SECRET=
WEATHER_API_KEY=
OLLAMA_HOST=http://localhost:11434
AUDIT_LOG_RETENTION_DAYS=30
CONFIRMATION_TIMEOUT_SECONDS=30
CONTEXT_WINDOW_MESSAGES=15
WAKE_WORD=jarvis
SCREEN_ANALYSIS_MODE=continuous    # continuous | on_demand | off
SCREEN_CAPTURE_INTERVAL_SECONDS=5
```

### 12.5 Manejo de errores — reglas generales

- Si Groq falla: reintentar una vez; si falla de nuevo, caer al modelo
  local de fallback (Ollama) y avisar en voz que está en "modo limitado".
- Si una herramienta falla al ejecutarse: registrar en `audit_log` con
  `result: 'failed'`, responder al usuario con el motivo en su tono
  habitual (con humor, sin tecnicismos crudos).
- Si el WebSocket se desconecta: la app reintenta cada 3 segundos
  indefinidamente, mostrando "desconectado" en el overlay.

## 13. Plan de pruebas — cómo validar cada bloque antes de seguir

Regla general: no se pasa al siguiente bloque de la sección 11 sin que el
anterior pase sus pruebas. Cada bloque se prueba manualmente (proyecto
personal, no hace falta CI/CD ni suite automatizada, salvo que se indique).

**Primer arranque del instalador**
- Instalar el `.exe` en una máquina/cuenta limpia: el asistente debe pedir
  las tres claves (Groq, Gemini, OpenRouter) antes de dejar usar JARVIS.
- Dejar una clave vacía a propósito: JARVIS debe seguir funcionando con las
  que sí tiene (ej. sin Gemini, cae directo a OpenRouter para tareas
  pesadas) y no romperse.

**1-3. MVP (cimientos + brain + memoria)**
- Levantar `brain` y `app` por separado; el overlay debe pasar de
  "conectando" a "listo".
- Enviar un mensaje de texto y recibir respuesta coherente de Groq.
- Cerrar y reabrir la app: el historial anterior debe seguir ahí (SQLite
  persistente).
- Apagar el `brain` a propósito: la app debe mostrar "desconectado", no
  romperse.

**Voz (wake word + STT + TTS + barge-in)**
- Decir "Jarvis" con la app en reposo: debe activarse sin falsos positivos
  al hablar de otra cosa cerca del micrófono.
- Verificar que tras un "Jarvis" sigue escuchando varios turnos sin repetir
  la palabra, y que vuelve a modo espera tras un silencio prolongado.
- Hablar mientras JARVIS está respondiendo: debe callarse y escuchar
  (barge-in) sin quedarse "colgado".
- Medir uso de CPU/RAM en reposo (solo wake word activo) durante al menos
  10 minutos — debe mantenerse bajo y estable, no crecer con el tiempo.

**Fallback offline**
- Cortar la conexión a internet (o apagar el acceso a Groq) y hacer una
  pregunta simple: JARVIS debe avisar que está en "modo limitado" y
  responder igual, aunque sea básico, con el modelo local.

**Permission & Risk Engine**
- Pedir una acción de riesgo bajo (ej. abrir una app): se ejecuta sin pedir
  confirmación.
- Pedir una acción sensible (ej. borrar un archivo de prueba): debe pedir
  confirmación por voz Y popup, los dos a la vez.
- No responder a la confirmación: pasados 30s, la acción debe cancelarse
  sola y quedar registrada en `audit_log` con `result: 'timeout'`.
- Revisar `audit_log` tras varias acciones: cada una debe tener su fila con
  el resultado correcto.

**Acciones del sistema (por prioridad de la sección 5)**
- Abrir apps/archivos: probar con al menos 3 apps distintas y un archivo en
  una ruta no obvia (para validar la búsqueda en todo el disco).
- Navegador predeterminado: pedir una búsqueda y verificar que abre el
  navegador que el usuario tiene configurado como predeterminado en
  Windows, no uno fijo.
- Ventanas/escritorio: minimizar, maximizar, cambiar de ventana por voz.
- Multimedia: reproducir/pausar Spotify y subir/bajar volumen.

**Web Intelligence**
- Preguntar algo que requiera info actual (ej. el clima de hoy) y verificar
  que la respuesta es coherente con la realidad, no inventada.

**Model Router (3 niveles)**
- Pedir algo trivial (ej. "hola") y confirmar en los logs que usó Groq.
- Pedir algo que requiera razonamiento pesado (ej. planificar varios pasos)
  y confirmar que usó Gemini.
- Desactivar/invalidar a propósito la key de Gemini y repetir la prueba
  anterior: debe caer a OpenRouter sin romperse.
- Desactivar las tres keys (o cortar internet) y confirmar que cae al
  modelo local de fallback.

**Visión de pantalla**
- Con el modo continuo activo, preguntar "¿en qué estoy trabajando ahora
  mismo?" y confirmar que la respuesta coincide con lo que realmente está
  en pantalla.
- Decir "Jarvis, deja de ver mi pantalla": confirmar que el modo continuo
  se apaga al instante (revisar logs/CPU, no debe seguir capturando).
- Verificar que ninguna imagen de pantalla queda guardada en disco ni en
  `messages`/`audit_log` — solo el texto descriptivo, si acaso.
- Preguntar algo puntual en modo "bajo pedido" (ej. "mira esto y dime qué
  error es") y confirmar que responde sobre el contenido real mostrado.

**Integraciones**
- Google Calendar: crear un evento de prueba por voz y verificar que
  aparece en el calendario real; pedirle que lo edite y que lo borre.
- Spotify: buscar una canción específica y pedir que la añada a una
  playlist; verificar en la cuenta real.
- WhatsApp: verificar que JARVIS avisa de un mensaje entrante de prueba,
  y confirmar que en ningún caso envía nada por su cuenta.
- Clima: preguntar el clima sin dar ciudad y verificar que detecta la
  ubicación correcta.

**Proactive Engine**
- Abrir un juego y verificar que detecta el estado "gaming" (revisar logs
  o preguntarle directamente "¿en qué modo estás?").
- Simular una reunión (abrir Zoom/Meet) y verificar que silencia
  notificaciones sin pedirlo.
- Confirmar que, aunque esté en modo proactivo, una acción sensible sigue
  pidiendo la doble confirmación (no se salta el control).

**Memoria semántica (Qdrant)**
- Generar una conversación larga (más de los 10-15 mensajes "frescos") y
  preguntar sobre algo mencionado al principio: JARVIS debe poder
  recuperarlo desde la memoria semántica, no solo del contexto reciente.

**UI/HUD completo**
- Verificar visualmente: overlay en la esquina en reposo, orbe azul/cian al
  activarse, línea de voz reactiva mientras habla, panel lateral que
  aparece con la acción y desaparece al terminar.

**Empaquetado (.exe)**
- Instalar en una máquina Windows limpia (o una cuenta de usuario nueva) y
  verificar que arranca solo, sin pasos manuales de configuración salvo
  rellenar el `.env` con las claves de API.
- Reiniciar Windows y confirmar el saludo de arranque ("Sistemas en
  línea") y que JARVIS queda escuchando el wake word automáticamente.

## 14. Modo Despertar

Rutina especial que se activa **solo cuando el usuario se lo pide** con una
frase tipo "buenos días" o "despierta" (no es automática por hora — no hay
alarma programada, es bajo demanda).

### Secuencia (en este orden)

1. **Saludo**: "Buenos días, señor."
2. **Clima**: consulta el clima actual en la ubicación detectada
   automáticamente (misma fuente que la sección 9) y lo dice **con cierto
   tono sarcástico**, acorde a la personalidad de JARVIS (ej. si llueve,
   un comentario irónico al respecto en vez de solo dar el dato plano).
3. **Agenda**: consulta los eventos del día en Google Calendar y los
   resume en voz.
4. **Mensajes**: revisa WhatsApp (solo lectura) y Gmail, y hace un
   **resumen de quién ha escrito y de qué trata cada mensaje** — no lee
   cada mensaje palabra por palabra, sino un resumen breve por remitente.
5. **Noticia tech/IA**: busca en la web (Web Intelligence, sección 7) una
   noticia reciente y relevante sobre tecnología/IA — cambios importantes,
   lanzamiento de productos nuevos, etc. — y la resume en un par de frases.
6. **Despedida**: "Espero que tenga un buen día."
7. **Música de fondo**: al terminar la locución, empieza a sonar de fondo,
   **en volumen bajo**, el archivo de audio subido por el usuario —
   guardado en `codigo-mvp/assets/audio/wake-up-theme.mp3` ("Should I Stay
   or Should I Go", The Clash). Este audio **solo suena como parte de este
   protocolo**, nunca en otro momento. El volumen de la música debe bajar
   automáticamente (ducking) si el usuario le habla a JARVIS después,
   para no solaparse con su voz.

### Notas técnicas

- Este protocolo es, en el fondo, una secuencia predefinida de llamadas a
  herramientas ya existentes (clima, calendario, WhatsApp, Gmail, web
  search) encadenadas y resumidas en un único mensaje hablado — no requiere
  un modelo ni una lógica nueva, solo un orquestador de secuencia dentro
  del Brain (`brain/routines/wake_up.py` o similar).
- Ninguno de estos pasos es una acción "sensible" (todos son de solo
  lectura/consulta), así que no requiere el doble canal de confirmación de
  la sección 5 — se ejecuta de corrido en cuanto se activa.
- El archivo de audio es un asset local del proyecto, no se descarga ni se
  genera — simplemente se reproduce desde `assets/audio/`.

### Prueba de validación

- Decir "buenos días" a JARVIS y confirmar que sigue el orden completo:
  saludo → clima (con tono) → agenda → resumen de mensajes → noticia →
  despedida → música de fondo.
- Verificar que el clima mencionado es real (comparar con una app de
  clima).
- Verificar que el resumen de mensajes menciona correctamente quién
  escribió, sin inventar remitentes ni contenido.
- Hablarle a JARVIS mientras suena la música de fondo: el volumen de la
  música debe bajar automáticamente mientras él responde.
