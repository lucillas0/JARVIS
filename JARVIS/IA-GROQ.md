# Groq como proveedor de modelos

Groq ya era el proveedor principal de JARVIS; lo que faltaba era que fuera un
sistema de producción y no una llamada suelta dentro de un `if`. Esto es lo que
hay ahora:

```
quien pregunta                 (conversación · voz · Discord · /api/ai/chat)
   ↓
ModelRouter.respond()          ← la misma puerta de siempre, sin cambios de firma
   ↓
AI Gateway                     ← elige modelo, reintenta, avisa al bus y mide
   ├── registro                 qué modelo toca para cada tipo de tarea
   ├── proveedor Groq           API oficial por HTTP (streaming incluido)
   ├── ModelRouter (Gemini, OpenRouter, Ollama)  ← los que ya había
   ├── bus de eventos           Brain.emit: llm.requested, llm.started, llm.token…
   └── estadísticas             tokens, latencia, reintentos y errores reales
   ↓
Permission Manager             ← las herramientas siguen pasando por aquí
   ↓
TTS                            ← solo la respuesta final, nunca el razonamiento
```

**OpenCode no se toca.** La programación sigue yendo a OpenCode: el gateway
responde `provider: "opencode"` a una tarea `CODING` en vez de inventarse una
respuesta.

---

## 1. Puesta en marcha

La clave se puede poner de dos maneras, igual que las demás:

- **En `/setup` o `/config`** (lo normal): se guarda en `keys.env`, fuera de la
  instalación, y surte efecto sin reiniciar.
- **En el entorno**, antes de arrancar:

```bash
# Windows (PowerShell)
$env:GROQ_API_KEY = "gsk_..."
# Linux / NAS / Docker
export GROQ_API_KEY="gsk_..."
```

La clave **nunca** se escribe en el código, ni se imprime al arrancar, ni sale
en la API, ni aparece en JARVIS Live. Para ponerla desde la web hay un campo de
escritura en `/api/ai/config` que no devuelve lo guardado.

### Variables de entorno

| Variable | Para qué | Defecto |
|---|---|---|
| `GROQ_API_KEY` | Credencial. Sin ella, Groq no se prueba | vacío |
| `GROQ_BASE_URL` | API oficial, compatible con OpenAI | `https://api.groq.com/openai/v1` |
| `GROQ_DEFAULT_MODEL` | Modelo de conversación normal | `GROQ_MODEL_FAST` |
| `GROQ_FAST_MODEL` | Conversación y comandos rápidos | `GROQ_MODEL_LIGHT` |
| `GROQ_REASONING_MODEL` | Análisis, planificación, diagnóstico | `GROQ_DEFAULT_MODEL` |
| `GROQ_MODEL_VISION` | Lo que se ve en pantalla o cámara | `GROQ_MODEL_FAST` |
| `GROQ_TEMPERATURE` | Temperatura por defecto | `0.7` |
| `LLM_TIMEOUT_SECONDS` | Plazo de una petición | `45` |
| `LLM_MAX_RETRIES` | Reintentos (solo 429, 5xx y red) | `2` |
| `LLM_MAX_CONCURRENT` | Peticiones simultáneas máximas | `4` |
| `LLM_STREAM_ENABLED` | Streaming a JARVIS Live | `1` |
| `LLM_TOOLS_ENABLED` | Ofrecer herramientas a quien las soporte | `1` |

Las antiguas (`GROQ_MODEL_FAST`, `GROQ_MODEL_LIGHT`, `GROQ_MODEL_VISION`) siguen
valiendo: si las nuevas están vacías, se usan las viejas. Una instalación que
ya funcionaba no se rompe.

### Docker

`docker-compose.yml` ya pasa las variables al contenedor:

```yaml
environment:
  GROQ_API_KEY: "${GROQ_API_KEY:-}"
  GROQ_BASE_URL: "${GROQ_BASE_URL:-https://api.groq.com/openai/v1}"
  GROQ_DEFAULT_MODEL: "${GROQ_DEFAULT_MODEL:-}"
  GROQ_FAST_MODEL: "${GROQ_FAST_MODEL:-}"
  GROQ_REASONING_MODEL: "${GROQ_REASONING_MODEL:-}"
  LLM_TIMEOUT_SECONDS: "${LLM_TIMEOUT_SECONDS:-45}"
  LLM_MAX_RETRIES: "${LLM_MAX_RETRIES:-2}"
```

Reiniciar con la clave puesta:

```bash
# 1. la clave va en el .env de la carpeta, NO en el repositorio
echo 'GROQ_API_KEY=gsk_...' >> .env

# 2. recrear el contenedor para que reciba la variable
docker compose up -d --force-recreate jarvis

# 3. ver si la ha cogido (solo dice si hay, nunca el valor)
docker compose exec jarvis printenv GROQ_API_KEY | wc -c
```

En el Synology: Container Manager → el proyecto → *Acción → Recrear*. La clave
del host se guarda en `keys.env` (el volumen `/data`), así que también se
puede poner desde `/setup` dentro del contenedor y sobrevive a las
actualizaciones.

---

## 2. Qué modelo se usa y por qué

| Tarea | Modelo | Para qué |
|---|---|---|
| `FAST` | `GROQ_FAST_MODEL` | «¿qué hora es?», comandos cortos |
| `GENERAL` | `GROQ_DEFAULT_MODEL` | conversación normal |
| `REASONING` | `GROQ_REASONING_MODEL` | análisis, plan, diagnóstico |
| `VISION` | `GROQ_MODEL_VISION` | pantalla o cámara |
| `CODING` | **OpenCode** | cualquier cosa que toque código |

Se cambia desde **JARVIS Live → IA**, o por API (`POST /api/ai/config`). El
cambio se guarda y se nota en la petición siguiente.

### Capacidades

El registro (`brain/ai_gateway/registry.py`) declara, para cada modelo: `id`,
`provider`, `display_name`, `capabilities`, `context_window`,
`supports_streaming`, `supports_tools`, `supports_reasoning` y `enabled`.

- Si Groq contesta a su `GET /models`, se usa lo que dice (la lista real y la
  ventana de contexto).
- Si no contesta, se usa lo declarado, y lo declarado solo cubre modelos que
  existen de verdad.
- **Una capacidad que no se conoce vale `null`, no `true`.** Y ante una
  capacidad desconocida **no se ofrecen herramientas**: pedirlas a un modelo que
  no las soporta devuelve un 400 y quema el turno.

---

## 3. Si un proveedor falla

| Qué pasa | Qué hace JARVIS |
|---|---|
| 401 / 403 | `AUTH_ERROR`. No se reintenta (una clave mala no mejora sola); se cambia de proveedor |
| 404 o `model_not_found` | Se cambia de proveedor |
| 429 | `RATE_LIMITED`. Espera la que dice el proveedor (o 0,6 s × 2ⁿ) y reintenta hasta `LLM_MAX_RETRIES` |
| 5xx | Reintenta con espera creciente y luego cambia de proveedor |
| Timeout | Respeta el plazo: no se pasa de `LLM_TIMEOUT_SECONDS` |
| Red caída | Reintenta una vez y cae en el siguiente proveedor |
| Respuesta rara | `MALFORMED`: no se inventa nada |
| No hay ninguna clave | Lo dice: «no hay ninguna clave de modelo puesta» |

El cambio de proveedor se avisa por el bus (`llm.fallback`) y sale en JARVIS Live.
Nunca hay un modelo inventado de relleno: si no hay nadie, JARVIS devuelve un
error controlado y comprensible.

---

## 4. Streaming

Por texto, la respuesta se pide en streaming: cada trozo sale por el bus como
`llm.token` y **JARVIS Live la escribe mientras se genera**. Por voz **no**:
el TTS necesita el texto entero, y solo recibe la respuesta final (nunca el
razonamiento interno del modelo, ni las llamadas a herramienta, ni los logs).

También hay `POST /api/ai/stream`, que devuelve lo mismo por HTTP en Server-Sent
Events.

Para apagar el streaming: `LLM_STREAM_ENABLED=0`.

---

## 5. Herramientas y permisos

El gateway **no ejecuta nada**. Si el modelo pide una herramienta, devuelve
`tool_calls` en la respuesta, y quien lo pidió (`main._run_tool_loop`) lo pasa
por `brain.tools.execute()`, que es el Permission Manager de siempre, con su
riesgo y su aprobación. Groq no tiene acceso directo al sistema, ni a una shell,
ni a un fichero.

---

## 6. API

| Ruta | Qué hace |
|---|---|
| `GET /api/ai/providers` | Proveedores y su estado real |
| `GET /api/ai/models` | Catálogo (`?consultar=1` pregunta a Groq) |
| `GET /api/ai/status` | Estado completo: modelos, uso, límites, qué está en marcha |
| `GET /api/ai/uso` | Cifras de uso (`?minutos=`) |
| `POST /api/ai/chat` | Una inferencia (`tarea`, `mensajes`) |
| `POST /api/ai/stream` | La misma, en Server-Sent Events |
| `POST /api/ai/test` | Prueba de conexión real (1 token) |
| `POST /api/ai/cancel` | Cancela una petición en marcha |
| `POST /api/ai/config` | Elige modelo por tarea / apaga proveedores / escribe la clave |

Y en el centro de mando: `GET /api/cmd/ia`, `/ia/modelos`, `/ia/peticiones`,
`POST /ia/configurar`, `POST /ia/probar`.

Todas exigen el token de `brain/auth.py` y un origen permitido, como el resto
del panel.

---

## 7. JARVIS Live

Vista **IA**: estado de los proveedores, modelo por tipo de tarea (cambiable),
fallback on/off, prueba de conexión, catálogo del proveedor, métricas reales
(peticiones, tokens de entrada y salida, tiempo medio, mínimo y máximo, errores
por tipo) y las últimas peticiones con su latencia y sus reintentos.

**Lo que no se puede medir, no se enseña**: si el proveedor no da precio, el
coste sale como `unknown`; si no ha habido peticiones, el tiempo medio sale en
blanco, no en cero.

---

## 8. Eventos

`llm.requested`, `llm.started`, `llm.token`, `llm.completed`, `llm.failed`,
`llm.retry`, `llm.rate_limited`, `llm.fallback`, `llm.cancelled`.

Van por `Brain.emit`, el bus que ya existía. No llevan claves ni cabeceras: el
mensaje de cada error pasa por un saneador que quita tokens y URLs con
credenciales antes de escribirlo en el log o devolverlo.

---

## 9. Pruebas

```bash
python brain/ai_gateway/test_ai.py    # 83 comprobaciones
```

Levantan un servidor HTTP local que habla la API de Groq de verdad
(`GET /models`, `POST /chat/completions` y streaming SSE) y comprueban: clave
ausente, clave inválida, 401/403/404/429/500, modelo inexistente, timeout,
espera y reintentos, cambio de proveedor (con un doble de Ollama por HTTP),
streaming, tool calling, cancelación, eventos del bus, paso del contexto de
memoria, la API y el health check.

No hace falta clave real ni internet.

---

## 10. Problemas frecuentes

**«No hay ninguna clave de modelo puesta»** → `GROQ_API_KEY` vacía. Ponla en
`/setup` o en el entorno, y **reinicia el contenedor** si estás en Docker.

**`AUTH_ERROR`** → la clave está mal o caducada. No se reintenta, porque repetir
no la arregla.

**`RATE_LIMITED`** → sin cuota. Espera un poco: el enfriamiento es de 45 s y
crece solo si Groq sigue igual.

**«el modelo insiste en llamar a una herramienta»** → el modelo pidió
herramientas y Groq lo rechaza. Pasa al siguiente proveedor; no es un fallo de
tu clave.

**No sale nada en streaming** → mira `LLM_STREAM_ENABLED`. Si está a `0`, la
respuesta llega entera de golpe, que también funciona.

**`DISCONNECTED` con las claves puestas** → el proveedor no contesta. Mira si hay
salida a internet desde donde corre JARVIS: en un NAS el router a veces bloquea
el dominio.
