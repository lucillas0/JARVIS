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
1. idea creada: dev-6142f7993c
   carpeta: /data/proyectos/fichero-html-diga-hola-mundo
2. PLANNING → WAITING_PLAN_APPROVAL   (3 pasos, 3 riesgos, 21-45 min)
4. aprobando → STARTING_OPENCODE → COMPLETED
```

**La cadena entera funciona**: idea → JARVIS la piensa → plan y riesgos →
espera tu aprobación → abre sesión en OpenCode → le manda el prompt.

### Lo que NO puede hacer todavía, y por qué

OpenCode arranca y acepta el encargo, pero **no ha escrito ningún fichero**:

```
/api/provider -> []      /api/model -> []      /api/agent -> []
```

**No tiene ni un proveedor configurado.** JARVIS le pasa `GROQ_API_KEY`, que
es una clave de Groq: OpenCode no la sabe usar porque no es un proveedor suyo.

Y antes de enterarse de eso, JARVIS decía **«Implementación completada»** con
cero ficheros. Eso es mentarle al señor sobre lo único que ha pedido, así que
ahora una tarea que termina sin haber tocado nada **se marca como fallida** y
dice por qué:

> *OpenCode no ha hecho nada: no tiene ningún modelo configurado. Revise que
> tenga una clave (`OPENAI_API_KEY`, `ANTHROPIC_API_KEY`…) dentro del
> contenedor.*

**Para cerrarlo del todo** hay que darle a OpenCode un proveedor. Es
configuración, no código: una clave que OpenCode reconozca en el contenedor.
Se comprobaron tres caminos y **ninguno** funcionó con 1.19.0 (se midió
`/api/provider` con cada uno):

| Cómo | Resultado |
|---|---|
| Sin tocar nada | `[]` proveedores |
| `OPENAI_API_KEY` + `OPENAI_BASE_URL` a Groq | `[]` proveedores |
| `provider.groq` en `opencode.json` | `[]` proveedores |

Se comprueba en cuanto la pongas con:

```bash
docker exec jarvis-prueba python -c "
import sys; sys.path.insert(0,'/app/brain')
from orchestrator import service
print(service.ensure(timeout=40))"
```

### 4. Una tarea se quedaba colgada horas sin decir nada

Sin modelo, OpenCode acepta el encargo, escribe **un** mensaje de error
(`HTTP transport failed`) y se queda ahí. La petición que JARVIS le hizo
—`send_prompt()`, que espera a que la sesión quede libre— **no se cerraba
nunca**, así que el hilo que la esperaba no terminaba y la tarea se quedaba en
`RUNNING` con el último evento siendo «OpenCode está trabajando.».

El límite era brutal: **dos horas** por tarea, sin un solo aviso.

Ahora hay un reloj de estancamiento (`_SIN_PROGRESO`, diez minutos por
defecto): si en ese rato no llega **ningún** mensaje nuevo, **ningún** fichero
tocado y **ninguna** pregunta, la tarea se da por parada y se explica. Un
desarrollo de verdad no se dispara, porque sigue hablando y sigue escribiendo.
Se puede cambiar con `JARVIS_OPENCODE_SIN_PROGRESO` (segundos), con suelo de
5 s para que nadie lo deje en cero.

### 5. Y un fallo mío que dejaba la tarea viva para siempre

Al añadir el aviso de «no ha hecho nada» metí una comprobación nueva con la
clave `detalle`, siendo que el cierre la lee como `detail`. Al cerrar la tarea,
`_resumen()` reventaba con:

```
Exception in thread jarvis-dev-dev-3c93e9960a:
    "errores": [c["detail"] for c in comprobaciones ...
KeyError: 'detail'
```

El hilo de la tarea **moría**, la tarea se quedaba en `RUNNING` para siempre
y el error solo aparecía enterrado en `docker logs`.

Cómo se encontró: el vigilante dejó de guardar la tarea y `updated_at` llevaba
301 s sin tocarse, mientras la API contestaba normal. El vigilante no estaba
colgado — estaba muerto.

Arreglado por dos lados: la comprobación usa `detail`, y `_resumen()` ya no
puede reventar aunque le llegue una comprobación sin esa clave o que no sea un
diccionario. Es una función que solo pinta: si peta, el trabajo hecho se queda
abierto para siempre sin estado ni error.

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
| Conversación normal | contesta en español |
| "cómo va el Real Madrid" | busca en la web y lee una página |
| Las 19 suites | **todas en verde** |

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

- **OpenCode no ha escrito un solo fichero.** La cadena entera está probada de
  verdad en el contenedor (idea → plan → riesgos → aprobación → sesión abierta
  → prompt enviado), pero al no haber proveedor configurado no llega a
  escribir. Está medido y está dicho arriba; lo único que falta es una clave de
  proveedor que OpenCode reconozca, y no se ha puesto ninguna por cuenta ajena.
- **Discord**, **Ollama** y **Qdrant** no están en el contenedor de prueba.
  Qdrant sí está instalado (estaba declarado y faltaba); Ollama no se espera
  en un servidor.
- El `Dockerfile` construye bien con el constructor clásico. **BuildKit falla en
  este equipo** con `parsing time ""` antes de empezar, por el Tooltip del
  Docker Desktop. No es del proyecto: es de la máquina.