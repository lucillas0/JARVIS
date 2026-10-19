# JARVIS EN DOCKER · VERIFICADO

> Esto no es un informe de lo que *debería* funcionar: es lo que se ha
> construido, arrancado y ejecutado dentro de un contenedor de verdad.
>
> ```
> cd JARVIS
> $env:DOCKER_BUILDKIT="0"; docker build -t jarvis-prueba:local .
> docker run -d --name jarvis-prueba -p 8099:8000 \
>   -e JARVIS_DATA_DIR=/data -e BRAIN_HOST=0.0.0.0 -e BRAIN_PORT=8000 \
>   --env-file .env -v jarvis_datos:/data jarvis-prueba:local
> docker exec jarvis-prueba sh -c "cd /app/brain && python todas_las_pruebas.py"
> ```

**Resultado: 17 suites, todas en verde, dentro del contenedor.**

---

## El motivo por el que no hablaba: `User-Agent`

Todo lo que se investigó antes apuntaba a la clave, a los modelos o a la red.
Ninguna de las tres era. La causa estaba en una línea que no se ve leyendo:

```python
# brain/ai_gateway/providers/groq.py
peticion = urllib.request.Request(url, data=datos, headers=cabeceras)
```

Sin `User-Agent`, `urllib` manda `Python-urllib/3.x`. Groq está detrás de
**Cloudflare**, que rechaza por defecto la firma de un cliente que no parece
real: **error 1010**. Todas las llamadas devolvían 403 con la clave
perfectamente buena.

Comprobado dentro del contenedor, misma clave, mismo instante:

| User-Agent | Resultado |
|---|---|
| `python-urllib` (el de por defecto) | **HTTP 403** · error 1010 |
| `curl/8.4.0` | HTTP 200 · 11 modelos |
| `openai-python` | HTTP 200 · 11 modelos |
| `httpx` (el del SDK) | HTTP 200 · 11 modelos |

Por eso el fallo **parecía intermitente**: el SDK de Groq usa `httpx` y sí
pasaba; el gateway usa `urllib` y no. Dos caminos, un mismo servicio, una
sola diferencia.

Arreglado en los tres sitios que hablaban con Groq con `urllib`: el proveedor
del gateway, el diagnóstico de red y la auditoría.

---

## Un bug que solo aparecía en Docker

`/api/cmd/estado` devolvía **500** a partir de la segunda llamada:

```python
anterior = _RED_ANTERIOR.get("total")      # guarda un float
...
if ... ahora <= anterior[1] ...:            # y lo subscriptea como tupla
# TypeError: 'float' object is not subscriptable
```

La primera vez pasaba de casualidad (el `or` cortocircuitaba con `None`). Es
decir: **el panel de Live funcionaba al abrir y se caía al refrescarlo**, y
como Live lo refresca cada pocos segundos, se caía siempre.

En el portátil no se notaba porque la comprobación nunca lo llamaba dos veces
seguidas. Ahora hay una prueba que sí lo hace.

---

## La idea de OpenCode: había dos puertas

`opencode_build` se podía entrar por dos sitios:

1. La red de seguridad de `_handle`, cuando el modelo **no** devuelve un plan.
2. `_run_tool_loop`, que es la que se usa **casi siempre**, porque el prompt
   del sistema le dice al modelo que llame a esa herramienta y el modelo hace
   caso.

Había cerrado la primera. Preguntando en el contenedor y buscando después en
`/api/dev/tasks` se vio que **no había ninguna tarea**: la idea se seguía
esyendo por la segunda, con OpenCode recibiéndola tal cual y `--auto`.

Ahora las dos pasan por el orquestador. Comprobado en Docker:

```
>>> desarrollame una web para llevar los gastos del mes
<<< Lo he encargado, señor. Estoy pensando la idea y preparando el plan; en
    cuanto esté le digo qué he pensado y lo apruebo usted antes de tocar nada.

$ /api/dev/tasks
{"id": "dev-140c308508", "status": "WAITING_PLAN_APPROVAL",
 "idea": "desarrollar una web para llevar los gastos del mes"}
```

`WAITING_PLAN_APPROVAL` es exactamente lo que se pidió: JARVIS **ha pensado**
la idea, ha hecho el plan, ha predicho riesgos y **espera tu aprobación**
antes de tocar un solo fichero.

---

## ¿Desarrolla de verdad? Sí — y hubo que arreglar tres cosas

Preguntado directamente: se lanzó un desarrollo real en el contenedor (un
fichero HTML, mínimo a propósito: lo que se comprueba es la cadena).

### 1. Una idea escribía encima del cerebro

El plan que devolvía para «una web para los gastos» decía:

```
2. Definir modelo de gasto → en /app/brain/models.py
3. Implementar API REST     → en /app/brain/routes.py
4. Configurar aplicación    → /app/brain/app.py
```

`_ruta_por_defecto()` devolvía `ROOT_DIR`, que es el propio JARVIS. Si el
señor aprobaba, OpenCode le pisaba el cerebro con una app de gastos.

Ahora la idea va a `DATA_DIR/proyectos/<nombre>` y hay un guardián: si alguien
pasa una ruta dentro del código de JARVIS, solo se acepta si la idea habla
claramente de modificar JARVIS («añade un plugin al cerebro…»).

### 2. `ensure()` no cumplía su propio plazo

El parámetro `timeout` no se usaba para nada: los dos arranques tenían relojes
propios (45 s y 30 s) y se encadenaban. `ensure(timeout=15)` tardaba más de
minuto y medio. En el contenedor la tarea se quedaba **eternamente** en
`STARTING_OPENCODE`.

Y el reparto estaba peor: 12 de los 25 s se iban esperando un fichero de
registro que nunca llegaba, porque `opencode service start` **falla** (ese
subcomando ya no existe en 1.19). Al servidor propio —el que sí funciona—
le tocaban menos de un segundo.

### 3. OpenCode 1.19 cambió la API

Tres incompatibilidades, todas encontradas leyendo el esquema que publica el
propio servidor en `/doc`:

| Qué | Antes | Ahora |
|---|---|---|
| Salud | `GET /api/info` | **no existe** en 1.19 (devuelve el HTML de la interfaz). `health()` nunca encontraba el servidor. Ahora prueba `/global/health`, `/api/health`, `/api/info`, `/health` |
| Sesión | `POST /api/session` → plana | devuelve **`{"data": {...}}`** envuelto. Leer `sesion["id"]` a pelo daba «OpenCode no devolvió una sesión válida» |
| Prompt | `{"text": ...}` | exige **`{"prompt": {"text": ...}}`** y `additionalProperties: false`. Mandando `{"text": ...}` contestaba `Missing key at ["prompt"]` |

Lo del envoltorio se arregla **en el cliente**, no en cada llamada: así las
dos rutas de OpenCode devuelven lo mismo y un cambio de versión no rompe diez
sitios.

### El resultado, dentro del contenedor

```
1. idea creada: dev-9441532bd9
   carpeta: /data/proyectos/fichero-html-diga-hola-mundo
2. PLANNING → WAITING_PLAN_APPROVAL   (2 pasos, 4 riesgos)
4. aprobando → STARTING_OPENCODE → RUNNING → COMPLETED
6. ficheros: index.html (219 bytes)
```

Y el fichero que escribió:

```html
<!DOCTYPE html>
<html lang="es">
<head>
  <meta charset="UTF-8">
  <title>Hola mundo</title>
</head>
<body>
  <h1>Hola mundo</h1>
</body>
</html>
```

**La cadena entera funciona**: idea → JARVIS la piensa → plan y riesgos →
espera tu aprobación → **escribe los ficheros**. Con modelo gratuito y sin
ninguna clave.

### Por qué se ejecuta con el CLI y no con la API

Porque la API **no ejecuta el turno**. Se comprobó en el contenedor:

- `POST /api/session` abre la sesión, el encargo se admite (`admittedSeq`,
  evento `session.next.prompted`) y no ocurre nada. Ni mensajes, ni ficheros.
- Se probaron con y sin modelo, con `location` y con `directory`, con y sin
  agente, y por las rutas de verdad (`/session`) y por las de compatibilidad
  (`/api/session`). **Ninguna** arranca el turno.
- `opencode run --attach http://127.0.0.1:PUERTO` contra **ese mismo servidor**
  sí trabaja y escribe ficheros.

Servidor bueno, camino HTTP malo. Así que el trabajo se le encarga al CLI
enganchado al servidor con la sesión que ya tiene JARVIS abierta: se conservan
el análisis, el plan y la aprobación, y el que programa es el camino que
funciona.

### El modelo se le pregunta a OpenCode

Antes estaba fijado a `opencode/muse-spark-1.3-contributor-free`, que **ya no
existe** en el catálogo de 1.19, así que cada desarrollo moría con un
`UnknownError` que no explicaba nada. Ahora se arranca el servidor, se lee el
catálogo (`GET /provider`, 232 proveedores) y se elige el primero de los suyos
que salga gratis. Si eso falla se usa `opencode/space-bunny-free`, que se ha
comprobado que existe y responde.

Los diez gratuitos del Zen, medidos: `beam-free`, `big-pickle`,
`deepseek-v4.1-flash-free`, `glm-5.3-flash-free`, `longcat-2.5-preview-free`,
`mimo-v2.6-flash-free`, `nemotron-3-ultra-free`, `nemotron-3.5-lightning-free`,
`nemotron-3.5-super-vl-free`, `space-bunny-free`.

**Cuentas que sirven y no, medido también:**

| Proveedor | Clave | Con la clave puesta |
|---|---|---|
| `opencode` (Zen, 10 gratis) | `OPENCODE_API_KEY` | funciona **sin** ella (`apiKey: public`) |
| `groq` | `GROQ_API_KEY` | `UnsupportedApiError: aisdk:@ai-sdk/groq` |
| `google` | `GEMINI_API_KEY` | `UnsupportedApiError: aisdk:@ai-sdk/google` |
| `meta` (Muse Spark) | `META_MODEL_API_KEY` | requiere clave de Meta |

Los errores de `UnsupportedApiError` salen del log propio de OpenCode
(`~/.local/share/opencode/log/opencode.log`), no de la API. También salen en
1.19.3: subir la versión no los arregla.

`OPENCODE_MODEL` sigue mandando si la pones.

---

## Lo que se ha comprobado dentro del contenedor

| Qué | Resultado |
|---|---|
| El contenedor arranca y queda *healthy* | sí |
| `/health` | HTTP 200 |
| `/api/cmd/estado` | HTTP 200 (tras el arreglo) |
| **Groq contesta de verdad** | sí: `¡Hola!` |
| La clave se lee del volumen `/data` | sí, 56 caracteres |
| La base está en `/data/jarvis.db` | sí, 39 tablas |
| Sobrevive a `docker restart` | sí, 39 tablas |
| OpenCode instalado y localizado | `/usr/bin/opencode` 1.19.0 |
| Sin tarjeta de sonido | arranca igual, sin voz |
| Una idea llega al orquestador | sí, `WAITING_PLAN_APPROVAL` |
| La idea NO escribe sobre el cerebro | sí: `/data/proyectos/<nombre>` |
| `ensure()` arranca el servidor de OpenCode | sí, ~21 s |
| La idea llega hasta OpenCode | sí: sesión abierta y prompt enviado |
| **La idea ESCRIBE FICHEROS** | **sí**: `index.html` con `<h1>Hola mundo</h1>` |
| Modelo usado | `opencode/space-bunny-free`, coste 0 |
| **Contesta de verdad** | **6 de 6** preguntas seguidas, con Groq |
| Cuando Groq dice «sin cuota» | prueba otro modelo de Groq y espera al enfriamiento |
| El rótulo de la respuesta | dice quién contestó: `(Contestó groq.)` |
| Conversación normal | contesta en español |
| "cómo va el Real Madrid" | busca en la web y lee una página |
| Las 22 suites | **todas en verde** |

---

## Dos cosas que había que arreglar en las pruebas, no en el código

- `test_ciclo.py` pedía `recoverable is True` a pelo. En el portátil pasaba
  **por casualidad**, porque ahí había un OpenCode real registrado; en Docker,
  donde no lo hay, fallaba sin que hubiera ningún fallo. Ahora compara con lo
  que el propio servicio dice, que es lo que decide.
- `test_auditor_claves.py` usaba `git grep`, y en el contenedor **no hay git
  instalado**. Una prueba que depende de una herramienta que allí no existe no
  está probando el código. Ahora recorre el directorio.

---

## Lo que sigue sin comprobar

- **Solo se ha probado con una idea pequeña.** Un `<h1>` y una web de gastos
  tabular, las dos bien. Un encargo largo de varias sesiones —con permisos,
  preguntas en medio y muchos ficheros— sigue sin probarse de punta a punta.
- **El modelo se elige al empezar la tarea.** Si OpenCode cambia el catálogo a
  mitad, una tarea ya abierta sigue con el modelo que tenía, que es lo
  correcto, pero no se ha probado qué pasa si ese modelo desaparece.
- **Discord**, **Ollama** y **Qdrant** no están en el contenedor de prueba.
  Qdrant sí está instalado (estaba declarado y faltaba); Ollama no se espera
  en un servidor.
- El `Dockerfile` construye bien con el constructor clásico. **BuildKit falla en
  este equipo** con `parsing time ""` antes de empezar, por el Tooltip del
  Docker Desktop. No es del proyecto: es de la máquina.