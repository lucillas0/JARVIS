# Memoria persistente · JARVIS

JARVIS ya tenía memoria de conversación (SQLite + Qdrant): los últimos
mensajes y lo semántico de las conversaciones largas. Eso **sigue igual**.

Lo que hay aquí es otra cosa: un servicio aparte, **Mem0 de código abierto
autoalojado**, que recuerda *hechos sobre usted* entre conversaciones. Que
el NAS es un DS920+, que prefiere que JARVIS hable en español, en qué
proyecto trabaja. Eso no caduca con el historial.

```
Usted → JARVIS → Agentes / Orquestador → Memory Manager → Mem0 OSS API
                                                    ↓
                                          PostgreSQL + pgvector
                                                    ↓
                                            recuerdos persistentes
```

## Qué es y qué no es

| | |
|---|---|
| **Es** | Una capa más, con un servicio propio, en tu NAS |
| **No es** | Mem0 Cloud ni la plataforma de pago: el código abierto no usa el prefijo `/v1` |
| **No sustituye** | La memoria de siempre. SQLite y Qdrant siguen ahí |
| **Si Mem0 se cae** | JARVIS contesta igual, sin recuerdos nuevos. Avisa en `/health` |
| **Si no lo quieres** | `MEMORY_ENABLED=0` y no pasa nada |

## Puesta en marcha

```sh
# 1. Copia la configuración
cp configuracion.env.example .env

# 2. Pon una contraseña de base de datos. OBLIGATORIA:
#    sin ella postgres no arranca.
nano .env
#    POSTGRES_PASSWORD=una-cosa-larga-y-que-no-sea-1234
#    GEMINI_API_KEY=la-que-ya-tienes

# 3. Levanta
docker compose up -d

# 4. Mira qué ha pasado
docker compose ps
docker compose logs -f mem0 | head -40
```

La primera vez `mem0` tarda: corre las migraciones de Alembic y prueba a
hablar con el proveedor. El `start_period` del healthcheck son 180 s por eso.

## Comprobar que funciona

```sh
# El estado de la memoria
curl -s http://localhost:8000/health | python -m json.tool | grep -A6 memoria
```

Debe salir algo así:

```json
"memoria": {
  "activado": true,
  "disponible": true,
  "alcanzable": true,
  "url": "http://mem0:8888",
  "user_id": "default"
}
```

Si `alcanzable` es `false`, JARVIS sigue funcionando pero no recuerda nada.
Mira `docker compose logs mem0`.

### Guardar un recuerdo a mano

```sh
TOKEN=$(curl -s http://localhost:8000/ui | grep -o 'JARVIS_TOKEN_URL="?token=[^"]*"' | cut -d= -f2 | tr -d '"')

curl -s -X POST http://localhost:8000/api/memory \
  -H "Content-Type: application/json" \
  -H "X-Jarvis-Token: $TOKEN" \
  -d '{"texto":"El NAS del señor es un Synology DS920+","categoria":"device"}'
```

### Comprobar que JARVIS recuerda entre reinicios

```sh
# 1. Dile algo que quiera recordar
curl ... -d '{"texto":"Recuérdame que el servidor de archivos es nas-casa"}'

# 2. Reinicia TODO, included la base de datos
docker compose down

# 3. Levanta de nuevo
docker compose up -d

# 4. Pregúntale por ello
curl -s http://localhost:8000/health | grep alcanzable
```

Y desde el chat: *«¿cómo se llamaba el servidor de archivos?»*. Si lo
recuerda, la memoria ha sobrevivido.

En la interfaz se ve en **/config → pestaña Memoria**: lista, busca, borra y
vacía.

## Endpoints

Todos exigen el token de `brain/auth.py`, el mismo que protege el WebSocket
y `/api/config`. Quien entre ahí puede leer y borrar todo lo que JARVIS sabe
de usted.

| Método | Ruta | Qué hace |
|---|---|---|
| `GET` | `/api/memory` | Todos los recuerdos |
| `GET` | `/api/memory?q=...` | Los que casan con la búsqueda |
| `POST` | `/api/memory` | Añadir uno. Body: `{"texto", "categoria"}` |
| `POST` | `/api/memory/search` | Buscar. Body: `{"consulta", "limite"}` |
| `PUT` | `/api/memory/{id}` | Actualizar. Body: `{"texto"}` |
| `DELETE` | `/api/memory/{id}` | Borrar uno |
| `POST` | `/api/memory/clear` | Vaciar **toda** la memoria |

## Herramientas para JARVIS

`memory_search`, `memory_add`, `memory_update`, `memory_delete`,
`memory_list`. El modelo las usa cuando lo necesita, pero **no hacen falta
para que la memoria funcione**: el sistema busca solo antes de cada
respuesta. Son un atajo, no el mecanismo.

## Cómo decide qué se guarda

Esto es lo importante, porque un asistente que memoriza cada conversación
deja de servir para nada.

**Se guarda:**
- Lo que usted pide: *«recuerda que…»*, *«apúntalo…»*
- Preferencias, configuración, proyectos, dispositivos, nombres
- Decisiones y cambios permanentes

**No se guarda:**
- *«Hola»*, *«gracias»*, *«vale»*
- Preguntas y respuestas normales
- Información temporal
- **Claves, contraseñas, API keys y tokens** — ni aunque usted lo pida.
  Se le avisa y no se guarda. Un secreto en una base vectorial sale en los
  backups y en los logs tarde o temprano.

Mem0 decide por su cuenta si un recuerdo es nuevo, **actualiza** uno que ya
existe o lo descarta por duplicado. Por eso *«ahora quiero que respondas en
inglés»* sustituye la preferencia anterior en vez de dejar dos contradictorias.

## Un usuario o varios

Con un solo JARVIS, `MEMORY_USER_ID=default`. El sistema está preparado para
varios sin rehacer nada: cada `user_id` tiene sus recuerdos, y `GET
/api/memory` solo devuelve los suyos.

## Seguridad

- **PostgreSQL no se publica.** Vive en la red del proyecto y solo `mem0` lo ve.
- **`mem0` tampoco se publica.** JARVIS lo alcanza por `http://mem0:8888`
  dentro de la red. El panel no está abierto a la LAN.
- Con `MEM0_AUTH_DISABLED=1` el servicio no pide clave, **porque no está
  expuesto**. Si algún día lo expones: ponlo a `0` y genera un `MEM0_API_KEY`.
- Los secretos van en el `.env`, nunca en el código.
- Los endpoints respetan la autenticación que ya tenía JARVIS.

Si quieres abrir el panel de Mem0 para mirar los recuerdos a mano, descomenta
el `ports` del `docker-compose.yml`, pero hazlo **con una IP restringida** a
la que solo llegues tú.

## Problemas encontrados (y no los he escondido)

Cinco cosas no funcionaban como dice la documentación de Mem0. Todas están
arregladas aquí, y las tres primeras solo se ven **al ejecutarlo**, no al leer
el código.

### 1. El proveedor de Gemini no se puede cargar (bug de upstream)

`server/requirements.txt` instala `google-generativeai`, que es el SDK viejo
de Google. Pero `mem0/llms/gemini.py` y `mem0/embeddings/gemini.py` importan
`from google import genai`, que es el SDK nuevo (`google-genai`), y no están
instalados:

```
ImportError: The 'google-genai' library is required.
```

Y como el servidor declara `gemini` en `BUNDLED_LLM_PROVIDERS` y
`BUNDLED_EMBEDDER_PROVIDERS`, **anuncia un proveedor que no puede cargar**.
Arreglado en `docker/mem0/Dockerfile`, que instala también `google-genai`.

### 2. Las variables de proveedor no existen

Casi toda la documentación menciona `MEM0_LLM_PROVIDER` y
`MEM0_EMBEDDER_PROVIDER`. **`server/main.py` no las lee.** Las únicas de
proveedor que existen son `MEM0_DEFAULT_LLM_MODEL` y
`MEM0_DEFAULT_EMBEDDER_MODEL`, que eligen el *modelo*, no el *proveedor*:
este va fijo a `openai` y solo se cambia desde la tabla `settings`.

### 3. Sin claves de OpenAI, mem0 no arranca ni llega a abrir un puerto

`initialize_state(DEFAULT_CONFIG)` se ejecuta al **importar** el servidor, y su
config por defecto es OpenAI. Sin `OPENAI_API_KEY` peta ahí mismo:

```
openai.OpenAIError: Missing credentials.
```

El contenedor sale con código 1, Docker lo reinicia, y otra vez. **No hay
ninguna ventana** para mandarle un `POST /configure` y arreglarlo en caliente.

Pero sí la hay por otro lado: `initialize_state` llama antes a
`_load_overrides()`, que lee una fila de la tabla `settings` con clave
`config_overrides` y la **funde** sobre la config por defecto. Escribiendo esa
fila antes de arrancar, el proveedor OpenAI no llega a instanciarse nunca.

Por eso está `docker/mem0/configurar-mem0.py`: siembra esa fila. **No modifica
ni una línea del código de Mem0**, solo escribe un dato que su propio servidor
ya sabe leer. Es idempotente: si ya está en Gemini no toca nada, y si has
cambiado algo a mano desde `/configure`, solo rellena huecos.

### 4. Sin `alembic upgrade head` no se crea ninguna tabla

El arranque de upstream es `alembic upgrade head && uvicorn ...`. Con solo
`uvicorn`, la base se queda vacía (`psql -c "\dt"` → *"Did not find any
relations"*) y el servicio revienta al primer uso. El `CMD` de nuestro
Dockerfile hace las tres cosas en orden: migrar, sembrar la configuración, y
luego sí escuchar.

### 5. La imagen oficial no existe para x86

`mem0/mem0-api-server` solo tiene manifiesto `linux/arm64` (su último push es
de septiembre de 2025), así que en un Synology x86 —un DS220+ o un DS920+, que
es lo normal— falla con:

```
no matching manifest for linux/amd64/v3 in the manifest list entries
```

Por eso compilo el servidor desde el código en `docker/mem0/`. Es la única
imagen oficial, y no hay alternativa.

### La clave de Gemini queda en la base de datos

Como el LLM de Gemini **no** lee la clave del entorno (a diferencia del
embedder, que sí usa `GOOGLE_API_KEY`), la clave tiene que ir en la config, y
la config se guarda en la tabla `settings` de postgres. Es el diseño de Mem0:
su propio `POST /configure` hace exactamente eso.

Lo que esto significa en la práctica: **la clave está en el volumen
`mem0_postgres`**, así que va a salir en los `pg_dump` de backup. Si eso te
molesta, en el `.env` usa una clave de Gemini distinta de la que usa JARVIS
para chatear; si pierdes la una, solo pierdes la memoria.

### 6. Las dimensiones del vector tienen que cuadrar (y se lee de la base)

Alembic crea la tabla `memories` con `vector(1536)`, que es lo que espera el
proveedor **por defecto** (OpenAI, `text-embedding-3-small`). Gemini devuelve
**3072**. Y mem0 pide al embedder lo que pone en la config.

Con tres números distintos, el error es:

```
psycopg.errors.DataException: expected 1536 dimensions, not 768
```

que le llega a usted como un `502` de mem0, sin mencionar dimensiones. Es
precisamente el tipo de cosa que hace perder media hora mirando claves y
cuotas cuando el problema es un número.

Por eso `configurar-mem0.py` **pregunta a postgres** de cuántas dimensiones
tiene la columna y pide exactamente eso. Si alguien cambia el embedder o
recrea la tabla, se ajusta solo. Solo hace falta rellenar `MEM0_GEMINI_DIM`
a mano si se fuerza algo.

### 7. `/app/history` no existe y sqlite3 no crea directorios

El servidor abre un SQLite auxiliar en `HISTORY_DB_PATH`, que por defecto es
`/app/history/history.db`. Si el directorio no está:

```
sqlite3.OperationalError: unable to open database file
```

Tercera forma de que este servicio no arranque, y la más tonta: una carpeta
que falta. Ese fichero **no son los recuerdos** (esos están en postgres), es
un registro de operaciones y se puede perder.

### 8. La clave de Gemini gratuita responde a muy pocos modelos

Con la clave del señor, medido uno por uno:

| Modelo | Resultado |
|---|---|
| `gemini-2.5-flash`, `gemini-2.0-flash` | 404 — no disponibles para cuentas nuevas |
| `gemini-3.6-flash` | 429 — cuota agotada |
| `gemini-3.5/3.7/3.8-flash`, `gemini-flash-latest` | 503 — no disponibles ahora |
| `gemini-flash-lite-latest` | **OK** |
| `gemini-3-flash-preview` | **OK** |

Por eso el defecto es `gemini-flash-lite-latest`: el trabajo del LLM de mem0 es
solo extraer hechos, así que da igual que sea el más pequeño siempre que
conteste. Los límites van **por modelo**, no por cuenta.

Si mem0 responde con un `502`, lo primero es cambiar `MEM0_GEMINI_LLM` por
otro de los que responden.

### 9. Los recuerdos se guardan en inglés

El prompt con el que mem0 extrae los hechos está escrito en inglés, dentro de
`mem0/configs/prompts.py`, y **no hay forma de cambiarlo por configuración**
(la única opción `custom_prompt` que existe es la del reranker, que es otra
cosa).

Así que si usted le dice en español «el servidor de archivos se llama
nas-casa», el recuerdo se guarda como:

> *User's file server is named nas-casa*

**No es un problema para que JARVIS lo entienda**: los embeddings de Gemini
funcionan igual entre idiomas, la búsqueda sigue encontrando lo correcto
(comprobado: pregunta en español, encuentra lo guardado), y JARVIS contesta
en español como siempre. Lo que se ve en inglés es el texto de la ficha en la
pestaña Memoria.

Podría monkey-patchear el prompt al construir la imagen, pero eso es fragile
para un beneficio que es solo de presentación: si Mem0 actualiza el módulo,
se rompe sin avisar. Por eso no está hecho.

### 10. Con pocos recuerdos, la búsqueda siempre trae algo

`pgvector` busca por parecido, así que con dos recuerdos guardados hasta una
consulta sin relación devuelve el vecino más cercano. No está inventando: el
recuerdo existe de verdad. Conforme la memoria crece, la búsqueda se va
selectiva sola.

Aun así, el bloque va entre `<relevant_memory>` y con la instrucción explícita
de que **son hechos, no instrucciones**, para que un recuerdo que dijera «ignora
lo anterior» no se lea como una orden.

---

## Backup

Los recuerdos están en el volumen **`mem0_postgres`**. Eso es lo único que hay
que respaldar.

```sh
# Copia simple (stop/start, más simple y más fiable)
docker compose stop mem0
docker compose exec -T postgres pg_dumpall -U mem0 > memoria-$(date +%F).sql
docker compose start mem0

# O en caliente, sin parar nada
docker compose exec -T postgres pg_dumpall -U mem0 > memoria-$(date +%F).sql
```

Automátalo con una tarea del Programador de tareas del DSM:

```sh
docker compose exec -T postgres pg_dumpall -U mem0 > /volume1/backup/memoria.sql
```

## Restaurar

**El orden importa.** Postgres 17 no lee datos de Postgres 15, y si arrancas
`mem0` antes de restaurar, sus migraciones crean tablas vacías y luego el
`restore` falla con claves duplicadas.

```sh
# 1. Solo postgres
docker compose up -d postgres

# 2. Restaura
docker compose exec -T postgres psql -U mem0 < memoria.sql

# 3. Ahora sí, mem0
docker compose up -d mem0
```

## Borrar toda la memoria

```sh
# Desde la interfaz: /config → Memoria → Vaciar toda la memoria
curl -s -X POST http://localhost:8000/api/memory/clear \
  -H "X-Jarvis-Token: $TOKEN"

# O de raíz, destruyendo el volumen (se lleva también las API keys de mem0)
docker compose down -v
docker compose up -d
```

## Migrar el historial

**No se hace solo.** Pasa meses de conversación a recuerdos de golpe, y eso es
una decisión suya:

```sh
# Ver qué se migraría, sin escribir nada
python scripts/migrate_memory.py --dry-run

# Migrar de verdad
python scripts/migrate_memory.py --limite 500

# Solo un tramo de fechas
python scripts/migrate_memory.py --desde 2026-01-01 --hasta 2026-06-30
```

Solo se cogen los mensajes **suyos** que tienen datos que duran. Los de
JARVIS no: si no, la memoria se llena de las frases del propio asistente y
luego el modelo se recuerda a sí mismo. Usa el mismo filtro que el automático,
y descarta los secretos igual.

## Troubleshooting

**`/health` dice `alcanzable: false`**
```sh
docker compose ps                       # ¿está postgres up?
docker compose logs mem0 | tail -40     # ¿qué dice?
curl http://localhost:8888/            # ¿vive el servicio? (solo si publicaste el puerto)
```

**`401: el servidor pide API key`**
`MEM0_AUTH_DISABLED` no es `1`, o `MEM0_API_KEY` está vacía. Con la
autenticación apagada no hace falta clave.

**`502 Upstream provider error`**
Mem0 no puede hablar con el LLM. Casi siempre es `GEMINI_API_KEY` vacía: sin
ninguna clave de proveedor arranca, pero no puede escribir ningún recuerdo.

**Postgres no arranca**
Falta `POSTGRES_PASSWORD`. Sin ella el contenedor se niega a salir, y con
razón.

**JARVIS va lento de golpe**
Mira si la memoria está marcada como caída:
```sh
docker compose logs jarvis | grep '\[MEMORY\]'
```
Con el servicio caído hay un timeout al principio y las siguientes son
instantáneas.

**El contenedor de JARVIS se reinicia solo, en bucle**
```sh
docker compose ps                      # ¿sale "Restarting"?
docker compose logs jarvis | tail -20
```
Si en el log hay esto:
```
PermissionError: [Errno 1] Operation not permitted
File "<string>", line 3
```
y el contenedor sale con código 1 una y otra vez, es que **faltan
capacidades para bajar privilegios**. En el `docker-compose.yml` tienen que
estar las cuatro:
```yaml
cap_drop:
  - ALL
cap_add:
  - SETGID      # setgid()/setuid(): bajar del root al PUID del usuario
  - SETUID
  - CHOWN       # chown de /data y de brain/db, que es el motivo de PUID
  - FOWNER      # borrar ficheros nuestros (caches, logs, sockets)
```
Sin esas cuatro, `cap_drop: ALL` se lleva `CAP_SETUID` y `CAP_SETGID`, y el
entrypoint —que baja al `PUID` del usuario del DSM para que los ficheros de
`/volume1` se puedan borrar desde File Station— falla con `EPERM`, el
contenedor sale con 1 y Docker lo reinicia. Bucle infinito con tres líneas
de traza y ninguna pista de la causa.

Las cuatro son las justas: identidad y propiedad de ficheros. El resto sigue
fuera, y en cuanto el proceso baja al `PUID` pierde todas las capacidades
igual, así que uvicorn corre sin ninguna.

Tampoco pongas `user:` en el compose: eso arranca el proceso ya sin
capacidades y el mismo `setuid` vuelve a fallar. Para eso está `PUID`/`PGID`.

Ahora, si aun así no se puede bajar, el servidor **arranca igual como root** y
lo dice en el log. Es peor en permisos, pero infinitamente mejor que un
contenedor que no arranca.

**No recuerda nada y el servicio está bien**
- `MEMORY_ENABLED=1`?
- ¿Le ha dicho "recuerda" o ha dicho algo con datos que duren? Si solo ha
  dicho "hola", no guarda nada, y es lo correcto.
- `GET /api/memory` debería devolver lo que haya. Si está vacío, el problema
  es la clave del proveedor, no la memoria.

## Los logs

```
[MEMORY] Buscando recuerdos…
[MEMORY] 3 recuerdos relevantes
[MEMORY] Anadiendo recuerdo…
[MEMORY] Recuerdo guardado
[MEMORY] Servicio no disponible: <motivo>. Reintento en 30s.
```

Nunca se registra ninguna clave ni el contenido de lo que se guarda.
