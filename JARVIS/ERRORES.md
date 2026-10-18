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
> python test_tiempos_respuesta.py            # que ningún mensaje tarde medio segundo
> python test_rutas_crossplatform.py          # que funcione en Windows y en Docker
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

## En Docker, la búsqueda de ficheros no encontraba nada

`filetree.find_fast()` armaba sus carpetas personales pegando una barra
invertida al final del nombre:

```python
roots = [os.path.expanduser("~") + "\\Desktop",     # C:\Users\Lucas\Desktop  ✓
         os.path.expanduser("~") + "\\Documents",   # /root\Desktop            ✗
         ...]
```

En Windows correcto. En Linux, `/root\Desktop` es **una sola palabra con una
barra dentro**: no existe, `isdir()` dice que no, la carpeta se descarta sin
decir nada y "busca el fichero que tengo en el escritorio" dentro del
contenedor no encontraba jamás lo que fuera.

Sin error, sin aviso, sin log. Solo que JARVIS decía que no lo encontraba
cuando **nunca lo había buscado**.

Ahora se arman con `os.path.join`, y en Linux se añaden las carpetas XDG y las
de nombre castellano (`Escritorio`, `Documentos`, `Descargas`), que es como se
llaman en un escritorio en español. El presupuesto de búsqueda bajó de 8 a 4
segundos: ocho de silencio después de "busca el fichero X" es una eternidad.

---

## El resto de los retardos eran caché fría

Un barrido de tiempos sobre 21 mensajes encuentra cosas por encima del medio
segundo. Al perfilar cada uno por separado:

| Mensaje | En el barrido | Calentado |
|---|---|---|
| "cuéntame un chiste" | 3,0 s | 0,018 s |
| "resume mi día" | 2,8 s | 0,014 s |
| "cuánto gas queda" | 1,0 s | 0,016 s |
| "cómo va el equipo" | 3,4 s | 2,5 s |

Los tres primeros eran la primera vez que se tocaba cada camino. **No eran
bugs**, y solo se vio al medir en vez de mirar tiempos sueltos: una prueba que
mide una sola vez está midiendo el arranque, no el mensaje.

---

## "Cómo va el equipo" no contestaba en Docker

Dos cosas en el mismo sitio.

**Una que reventaba.** `syshealth_tools._ps()` llama a `powershell`, que no
existe en Linux — y Docker es Linux. La llamada lanzaba `FileNotFoundError`
**sin que nadie lo capturara**, así que subía hasta el conversacional. En el
portátil "cómo va el equipo" contestaba y dentro del contenedor era un error.

**Y una que tardaba.** `perf_advisor_handler` lanzaba **cinco** procesos de
PowerShell para leer cinco números, y cada arranque cuesta ~450 ms. Ahora va
todo en uno:

| | Antes | Después |
|---|---|---|
| Mensaje más lento (calentado) | 2.394 ms | **337 ms** |
| Procesos de PowerShell por respuesta | 5 | 1 |

---

## El mensaje de error que estás viendo

Este salió de una conversación real del usuario:

> *"Mis disculpas, señor. He intentado con groq y los tres han fallado; esto no
> es que no tenga modelos, es que no he podido hablar con ellos. Mire el
> registro del servidor y ya lo veré."*

Tenía **tres** defectos, y los tres se ven solo cuando lo lees en casa:

1. Decía **"los tres"** con la palabra escrita a mano, mientras la lista de
   proveedores es dinámica. Con una sola clave puesta la frase era falsa de
   arriba abajo: "he intentado con groq **y los tres** han fallado".
2. **`groq-light` y `groq` son el mismo servicio** con distinto modelo, y
   salían listados como dos proveedores distintos.
3. **No decía por qué.** Ese es el grave. Cada proveedor escribía su motivo en
   el log a una línea de distancia y luego guardaba **solo el nombre**:

   ```python
   reply = fn()
   if reply: ...
   fallos.append(name)          # ← el motivo se queda en el log y se tira
   ```

   Por eso lo único que podía decir era "mire el registro del servidor", que
   no es un diagnóstico: es devolverle el problema al que acaba de preguntar.

Ahora el router guarda el motivo de cada proveedor, lo traduce a algo
accionable (sin sacar nunca la clave) y lo dice:

> *"Mis disculpas, señor. Groq, Gemini y OpenRouter fallaron y no he podido
> obtener respuesta. Groq: sin cuota (límite de la API), esperando 30 s;
> Gemini: la clave no es válida o está caducada. Revise las claves en /setup."*

Sin motivo conocido **no inventa uno**: dice que lo está en el registro.

### Y debajo había un fallo más serio

`respond()` solo se caía al camino viejo (`_respond_legacy`) si el AI Gateway
**lanzaba una excepción**. Si el gateway devolvía `{"ok": false}` —que es
exactamente lo que hace cuando los proveedores fallan o se acaba el reloj—
se lo devolvía al usuario tal cual y **la red de seguridad no se probaba
nunca**. Un fallo del gateway dejaba a JARVIS sin poder hablar aunque el
camino antiguo hubiera contestado.

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