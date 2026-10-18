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
| Conversación normal | contesta en español |
| "cómo va el Real Madrid" | busca en la web y lee una página |
| Las 17 suites | **todas en verde** |

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

- **OpenCode no ha desarrollado nada de verdad.** Está instalado y se localiza,
  y su servidor arranca cuando hace falta (`describe()` lo dice con precisión:
  *"OpenCode está instalado pero su servidor no está en marcha"*). Pero
  lanzar un desarrollo real gasta tokens y minutos: no se ha hecho.
- **Discord**, **Ollama** y **Qdrant** no están en el contenedor de prueba.
  Qdrant sí está instalado (estaba declarado y faltaba); Ollama no se espera
  en un servidor.
- El `Dockerfile` construye bien con el constructor clásico. **BuildKit falla en
  este equipo** con `parsing time ""` antes de empezar, por el Tooltip del
  Docker Desktop. No es del proyecto: es de la máquina.