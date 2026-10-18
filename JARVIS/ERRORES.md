# JARVIS — ERRORES DE CONVERSACIÓN

> Lo que se encontró buscando fallos de conversación, y por qué en Docker
> aparecen más que en un portátil.
>
> ```
> cd brain
> python test_conversacion_investigacion.py   # la investigación, turno a turno
> python test_fuzz_conversacion.py            # 790 mensajes, todo externo fallando
> python test_endpoints_vivos.py              # que las rutas funciona, no que existan
> python test_plazo_investigacion.py          # cuánto se puede llegar a tardar
> ```

---

## El que más duele: un problema de disco deja a JARVIS mudo

`handle_user_message` guardaba el turno en el historial **sin proteger**:

```python
reply = _handle(text, input_mode)      # esto sí tenía try/except
db.add_message("user", text)           # esto no
db.add_message("assistant", reply)
```

Si la base no escribía, la excepción subía y **se llevaba por delante la
respuesta**. Esa función es la entrada de **chat, voz, Discord, WhatsApp y las
tareas programadas**: un volumen lleno en Docker tumbaba las cinco a la vez.

Es especialmente traicionero porque el `docker/entrypoint.sh` ya documenta el
caso en su línea 84 (`attempt to write a readonly database`): el equipo sabía
que pasaba, y que la consecuencia de no repararlo ni siquiera estuviera
anotada en ninguna parte.

Ahora se guarda en `_guardar_historia()`, que anota el fallo y sigue. Perder el
historial de una frase es una molestia; perder la conversación entera es que
JARVIS parezca muerto.

---

## Contestar con un número no hacía nada, y no se veía

`orchestrator/inbox.py` declara la expresión como `_lock_numero` y la usaba
como `_numero`. Responder `2` a una pregunta de OpenCode lanzaba un
`NameError`… que el llamante se comía con un `except` que solo escribía un
`log.debug`.

El efecto era peor que un fallo visible: **la respuesta se descartaba en
silencio**, el permiso se quedaba pendiente para siempre y JARVIS no volvía a
sacar el tema. El señor pulsaba "2", no pasaba nada, y el pendiente seguía ahí.

---

## Una ruta montada que devolvía 500

`PUT /api/cmd/memoria/recuerdos/{id}` usaba `Optional[...]` en un modelo de
Pydantic **sin haber importado `Optional`**. Con `from __future__ import
annotations` la anotación llega como texto y Pydantic la resuelve contra el
namespace del módulo: sin el nombre, el modelo no se puede construir.

La ruta estaba montada, aparecía en el recuento de `rutas_reales.py` y la
matriz de requisitos la daba por buena. **Estar montada no es funcionar.** Por
eso ahora `test_endpoints_vivos.py` llama a los endpoints y mira qué contestan.

---

## Las métricas nunca se leían del disco

`sqlite_store.list_llm_usage()` llamaba a un `_fila()` que no existía. El
`NameError` lo tragaba el `except Exception` de quien la llamaba, así que
`stats.listar()` **siempre** devolvía las filas en memoria y nunca las
guardadas. Sin historial de peticiones entre reinicios.

Al arreglarlo apareció un segundo bug debajo: la fila guardada en SQLite
devolvía `streaming` y `ok` como `0`/`1`, y `1 is True` es `False` en Python.
La misma clave llegaba desde dos caminos distintos con dos tipos distintos, y
la API enseñando `"streaming": 0` en vez de `false`. Corregido en la lectura,
que es donde se debe arreglar y no en quien lo consume.

---

## Una investigación podía tardar minutos sin decir nada

Medido, no estimado:

| | Antes | Ahora |
|---|---|---|
| Motor de búsqueda **sin red** | **13,0 s** | 4,5 s |
| Techo declarado | ninguno | 12 s (búsqueda) / 25 s (lectura) |

`_search_lite` dormía 0 + 4 + 9 segundos entre reintentos, y a eso se le
sumaban hasta tres motores más. Luego `_research` probaba seis páginas con 15 s
cada una: **noventa segundos de silencio** en el peor caso.

En un portátil se nota poco. En un contenedor, cuya salida a internet suele ir
por una IP que DuckDuckGo trata peor, es el caso normal: el señor ve a JARVIS
parado y lo que cuenta es «se ha quedado colgado».

---

## Cada mensaje tardaba 44 segundos buscando máquinas virtuales

El más gordo de todos, y el que se encontró cronometrando en vez de leyendo.

Todo mensaje que llega al modelo pasa antes por `_atajo_maquinas`, que pregunta
por las máquinas virtuales. Ese atajo llama a `VBoxManage` dos veces, con
tiempos de **20 y 15 segundos**. Con el perfil de ejecución encima:

```
9514 llamadas a funciones en 43,883 segundos   (un solo mensaje)
    40,155 s  _atajo_maquinas
    40,155 s  vms.buscar -> vms.listar -> vms._leer
    40,155 s  virtualbox_tools._run   (4 llamadas)
     3,710 s  context_engine.construir
```

Y lo hacía **siempre**, porque cuando la lectura fallaba devolvía la caché
**sin actualizar su fecha**:

```python
if not base:
    return _cache          # no toca _cache_t
```

Con la lista vacía, la comprobación de vigencia de `listar()` nunca se
cumplía, así que el siguiente mensaje volvía a pagar los 35 segundos enteros.
Para siempre. No era un retraso de arranque: eran 40 segundos en **cada**
respuesta.

Ahora el fallo también se cachea, y la vigencia se mira por fecha y no por si
la lista tiene algo dentro:

| | Antes | Después |
|---|---|---|
| 1.º mensaje | 44 s | 27 s (paga una vez) |
| Cada mensaje siguiente | 44 s | **0,02 s** |

En Docker `VBoxManage` no existe y falla enseguida, así que ahí el coste era
menor — pero el defecto era el mismo, y en cualquier equipo con VirtualBox
roto era demoledor.

---

## Otros

- **La respuesta de una investigación salía en inglés**: `He investigated
  «la energía nuclear», señor. Corregido, con prueba que lo vigila.
- **`/api/cmd/decisiones` devolvía 500**: `services.decisiones()` usaba `store`
  sin importarlo.
- **Código muerto en `opencode_tools.py`**: el cuerpo de `opencode_ask` estaba
  pegado dentro de `opencode_jobs_handler` después del `return`, con dos
  nombres que no existen. No reventaba (era inalcanzable), pero era un pegado
  mal hecho del que solo faltaba que alguien lo completara.
- **Claves repetidas en diccionarios**: en `system_tools.py`, `edge` estaba
  escrito dos veces con valores distintos y la segunda pisaba a la primera, así
  que el alias se perdía en silencio. Y en `verificacion.py`, `Conocimiento`
  estaba duplicado: las cuatro rutas de la primera definición **no se estaban
  comprobando nunca** y la matriz daba verde sobre un hueco.

---

## Por qué en Docker fallan más cosas

No es que el código de Docker sea peor. Es que Docker es donde **falla de
verdad** lo que en un portátil nunca falla:

| En Docker | En un portátil |
|---|---|
| El volumen `/data` se llena o se queda en solo lectura | Raro: hay espacio de sobra |
| Sin tarjeta de sonido (`PortAudioError: device -1`) | Audio siempre disponible |
| Sale por una IP que los buscadores tratan peor | IP de casa, sin castigo |
| El usuario es el PUID del NAS, no root | Administrator |

Ninguno de esos casos estaba probado. Por eso los tres bancos de pruebas
sustituyen lo externo y lo hacen **fallar**, que es lo contrario de lo que
hacían las pruebas anteriores.

---

## Lo que sigue sin comprobarse aquí

Docker no está corriendo en esta máquina, así que **nada de esto se ha
ejecutado dentro de un contenedor de verdad**. Lo que sí se ha hecho:

- La lógica de la conversación se ha probado con 790 mensajes y todos los
  externos fallando (sin red, sin modelo, sin base, con respuestas basura).
- Los endpoints se llaman de verdad contra la app montada.
- Los tiempos se han **medido** con dobles que tardan lo que se les dice.

Lo que **no**: que el contenedor real arranque y mantenga el ritmo. Eso hay que
verlo con `docker logs`.