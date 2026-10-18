# JARVIS · Pase de hardening

> AUDITAR → DETECTAR → OPTIMIZAR → CORREGIR → PROBAR → VALIDAR
>
> Este documento es el resultado de ese pase: qué se comprobó **ejecutando** el
> código, qué se corrigió, y qué queda fuera con la razón.

Todo lo de aquí está comprobado con pruebas que se pueden volver a ejecutar:

```
cd brain
python todas_las_pruebas.py      # las 6 suites
python hardening_audit.py         # el análisis estático
python auditoria.py              # la auditoría de sistemas (ejecuta, no supone)
```

---

## 1. El hallazgo grave: la API estaba abierta en la LAN

**Qué pasaba.** El protegía endpoint a endpoint, y se pasó de alto 21 de
ellos. Con el puerto publicado (`"${JARVIS_PUERTO:-8000}:8000"` y
`BRAIN_HOST=0.0.0.0`), eso dejaba en manos de cualquiera de la red:

| Ruta | Qué permite |
|---|---|
| `POST /api/board/terminal` | **abrir una shell real** en el equipo del servidor |
| `POST /api/dev/tasks/{id}/approve` | **lanzar la misión** (que ejecuta OpenCode) |
| `POST /api/config` | **reescribir la configuración** |
| `POST /api/config/importar-claves` | **importar claves** de otro equipo |
| `GET /api/board/dir` | **listar cualquier carpeta** del servidor |
| `GET /api/camara/*` | ver la cámara |
| `DELETE /api/protocols` | borrar protocolos |

El propio `docker-compose.yml` lo decía en un comentario ("`/api/*` no piden
contraseña"), o sea que era una decisión conocida, no un descuido. Lo que no
tenía sentido era que **80 rutas sí lo exigieran** y estas 21 no: la
protección era incoherente, y donde se aplicó a medias fue justo donde están
las operaciones peligrosas.

**Cómo se corrigió.** No endpoint a endpoint, sino en la causa: un middleware
(`_token_en_las_api` en `brain/main.py`) exige el token a **todo** lo que
empieza por `/api/` o `/automatizaciones/`, con una lista corta y explícita de
excepciones (páginas y `/health`). Así una ruta nueva nace protegida.

A la vez se arreglaron los clientes que usan esas rutas de verdad, porque si no
se rompían: el puente de la pizarra en navegador, la app de escritorio
(`/api/config`, `/api/camara/*`, `/api/apps`, `/api/recap`) y, sobre todo,
`_inyecta_token`, que ahora **envuelve `window.fetch`** en las páginas servidas
para que su JS en línea añada la cabecera solo. Eso evita que la próxima página
nueva vuelva a olvidarse.

**Comprobado** por `test_seguridad.py` (26 comprobaciones): las **32 rutas de
API rechazan** sin token, con el token pasan, un token inventado no, el token
también vale en la query (descargas y `<img>`, que no pueden mandar cabeceras),
las páginas y `/health` siguen abiertas, y `/config`, `/setup`, `/modo` y
`/board` se siguen sirviendo con el token dentro.

### Lo que sigue abierto (y hay que saberlo)

Las **páginas** (`/board`, `/ui`, `/live`, `/config`, `/setup`) se sirven sin
token y llevan el token dentro del HTML. Quien pueda cargarlas tiene el token
y, con él, toda la API. Es el diseño que ya tenía `/board` y `/ui` antes de
este pase.

Se cierra haciendo que las páginas también exijan token (una redirección a un
formulario, o una cookie de sesión). No se ha hecho aquí porque cambia cómo
entra el señor al panel y el notch, y eso necesita probarse en el equipo real.
Queda anotado en `REMAINING`.

---

## 2. Otros bugs corregidos

| Dónde | Qué pasaba | Por qué importaba |
|---|---|---|
| `brain/context_engine.py:232` | `import semantic_memory` **nunca funcionaba**: es una *instancia* en `memory/semantic.py:150`, no un módulo. El fallo estaba dentro del `try` | La memoria semántica **se descartaba en silencio**: el modelo nunca recibía un recuerdo. Bug encontrado por un test propio |
| `brain/context_engine.py` (presupuesto) | Las piezas de peso 3 entraban enteras **aunque no cupieran** | Contexto ilimitado: 9.000 caracteres colados con un presupuesto de 2.000. Ahora se recortan y se marca dónde |
| `brain/context_engine.py` (último mensaje) | El último mensaje era el **prompt de sistema**, no la pregunta | El modelo contestaba a sus propias instrucciones |
| `brain/context_engine.py:119` | `date priza` en vez de `date prisa` | «date prisa» no se detectaba nunca |
| `brain/ai_gateway/routes.py:187` | El SSE **preguntaba una lista cada 30 ms** | 33 despertares/segundo y por cliente, aunque no llegara ni un token; hasta 30 ms de retraso por token. Ahora usa `asyncio.Queue` |
| `brain/config.py` | `CONTEXT_MAX_CHARS` **no existía** | El presupuesto del contexto no se podía ajustar |
| `instalar.py` | BOM (U+FEFF) al principio | Rompía el análisis estático y a cualquier herramienta que lo lea como texto |
| `brain/orchestrator/service.py:187` | Log que decía "claves para OpenCode" y registraba el error | Mensaje engañoso: parecía que se estaban volcando secretos |
| `main.py` (import partido) | Un parche partió `from memory.semantic import semantic_memory` en dos | **El arranque del cerebro estaba roto** (error de importación). Corregido |

---

## 3. Optimizaciones

| Cambio | Ganancia real |
|---|---|
| SSE con `asyncio.Queue` en vez de sondeo cada 30 ms | 0 despertares en reposo, latencia de token de ≤30 ms a ~0, y **cancelación real**: si el cliente se va, ahora se cancela la petición al proveedor en vez de seguir gastando tokens |
| Contexto con presupuesto y recorte | Deja de poder colar 9.000 caracteres donde caben 2.000, y el recorte **se dice** |
| `agente de auditoría` sin `except` ruido | 371 → 316 hallazgos, y los que quedan son los que de verdad importan |

---

## 4. Permisos: no hay segunda puerta (comprobado)

El criterio era claro: ningún agente, modelo o herramienta puede saltarse el
Permission Manager. Se comprobó sobre el código:

- **Un solo sitio** invoca handlers de herramientas: `ToolManager.execute()`,
  y llama a `risk.check()` **siempre**, antes de ejecutar.
- `execute_now_safe()` es solo un envoltorio para hilos: delega en `execute()`.
- Cancelaciones y fallos se **auditan** (`_record(..., confirmed=False)`).
- Las 50 funciones que lanzan `subprocess` están todas en herramientas o
  ayudantes, no en rutas de la API: se llega a ellas por `execute()`.

---

## 5. Degradación (FULL / LIGHT)

`test_degradacion.py` arranca el cerebro **de verdad** (su `startup()`), en
procesos limpios, quitando piezas una a una. 13 comprobaciones:

- Sin **ninguna** clave (ni Groq, ni Discord, ni HA, ni memoria persistente):
  arranca, engancha motor de riesgos, herramientas y router, monta 46 rutas y
  una ruta real contesta 200.
- Sin Discord / sin Home Assistant / sin Groq / sin memoria persistente: sigue
  en pie en los cuatro casos.
- Lo que falta, **lo dice**: la salud marca lo caído en vez de fingir.

---

## 6. Lo que queda (y por qué)

| Asunto | Por qué no se tocó aquí |
|---|---|
| **Las páginas sin token** (1º punto) | Cambia cómo entra el señor al panel; hace falta probarlo en su equipo |
| **Docker** | Los 3 servicios tienen `restart`, `healthcheck` y límites de memoria. `jarvis_data`, `jarvis_assets` y `mem0_postgres` son volúmenes, no servicios: no llevan `restart` porque no les corresponde |
| **Bucle de 20 ms en `voice/audio.py`** | Sondeo legítimo a cadencia de audio (barge-in y wake word). Pasarlo a eventos tocaría la parte más frágil del sistema para ganar poco |
| **287 `except` que se tragan la excepción** | Están en herramientas e integraciones opcionales: que la cámara no exista no debe tumbar el cerebro. **No** se reescribieron en bloque porque eso rompería lo que funciona. Los de ficheros que guardan estado (`sqlite_store`, `orchestrator/*`, `tool_manager`) sí se revisaron uno a uno: los hay legítimos con el motivo escrito al lado |
| **Clave `phc_...` de PostHog** en `docker/mem0/` | Es telemetría anónima de un tercero, no una credencial de JARVIS. Se deja y se anota |
| **Live sin testar con un Groq real** | Este equipo no tiene `GROQ_API_KEY` ni red hacia `api.groq.com`; se verificó contra un doble HTTP que habla el protocolo real |

---

## 7. Pruebas

| Suite | Comprobaciones | Qué cubre |
|---|---|---|
| `test_context.py` | 38 | presupuesto, recorte, qué entra y por qué, inyección de prompt, sistema caído |
| `ai_gateway/test_ai.py` | 95 | el gateway contra un doble HTTP del protocolo real (incluye el SSE recién arreglado) |
| `command/test_cmd.py` | — | centro de mando |
| `orchestrator/test_ciclo.py` | — | ciclo completo de una misión |
| `test_seguridad.py` | 26 | **nuevo**: las 32 rutas de API cerradas sin token |
| `test_degradacion.py` | 13 | **nuevo**: arranque sin cada pieza |

`python todas_las_pruebas.py` las corre todas y dice el número real de cada una.