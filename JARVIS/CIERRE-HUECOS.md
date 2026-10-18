# JARVIS — CIERRE DE HUECOS

> Los 8 requisitos incompletos de `VERIFICACION.md`, implementados, integrados y probados.
>
> ```
> cd brain
> python verificacion.py        # la matriz de requisitos
> python todas_las_pruebas.py   # las 9 suites
> python rutas_reales.py        # las 161 rutas de la app
> python hardening_audit.py     # análisis estático
> ```

**136 requisitos comprobados · 136 COMPLETE · 0 pendiente.**
**Auditoría estática: 0 CRITICAL, 0 MEDIUM, 4 HIGH (los cuatro, revisados y
aceptados a propósito — ver más abajo).**

---

## Qué se ha construido

| Sistema | Fichero | Qué hace |
|---|---|---|
| Agent Manager | `brain/agent_manager.py` | Inventario real de agentes, cola con prioridad, concurrencia, cancelación, detección de bloqueos y recuperación |
| Resource Manager | `brain/resource_manager.py` | Mide lo que ya medía `centers.recursos()` y **decide**: cuándo bajar concurrencia, con histéresis y por qué |
| Memoria estructurada | `brain/memory/recuerdos.py` | Recuerdos con origen, tipo, proyecto, misión, decisión, relevancia y explicación del porqué |
| Knowledge categorizado | `brain/library.py` | 12 categorías, deducidas de la ruta y reclasificables a mano |
| Autorizaciones | `brain/authz.py` | `once` / `session` / `until` / `reject`, con alcance exacto y revocación inmediata |
| Vistas Live | `brain/live/live.js`, `index.html` | Cola de agentes, límites, niveles de recurso y sus decisiones |

**9 suites de pruebas**, 304 comprobaciones numeradas (más 2 suites que
verifican el ciclo completo sin Contador, porque comprueban que *no* se
simule nada por el camino).

Y **161 rutas** en la API (antes 133).

---

## Decisiones que no se pueden deshacer a posteriori

**1. `once` no deja rastro.** Es el valor por defecto. Una autorización de un
solo uso no se guarda, porque un sistema que "acuerda" por su cuenta que puede
borrar cosas es un sistema que un día se borra a sí mismo.

**2. Lo crítico no se autoriza para recordar.** Ni por sesión ni con
caducidad. La comprobación es por palabra clave (`instalar`, `borrar`,
`docker`, `shell`…), no por nombre de herramienta: si fuera por nombre, bastaría
con llamar "instalar_paquete" en vez de "instalar_dependencia" para esquivarlo.

**3. Una autorización no se amplía sola.** El alcance es la herramienta exacta.
Autorizada `leer_fichero`, `leer_otro` sigue preguntando. En otra sesión de
JARVIS tampoco vale: al reiniciar, `session_id` es otro y lo anterior deja de
contar.

**4. El Resource Manager puede apretar, nunca aflojar.** El límite estático es
`Config.LLM_MAX_CONCURRENT` / `AgentManager.MAXIMO_CONCURRENTE`. El dinámico
solo baja, y solo afecta a lo que **aún no ha empezado**. Un trabajo en marcha
no se interrumpe por un umbral de RAM.

**5. Ante la duda, se vuelve a preguntar.** Riesgo desconocido → `USER_APPROVAL`.
Autorización que no encaja exactamente → no hay autorización. Métrica vieja →
`desconocido` y política conservadora.

---

## Bugs reales encontrados al implementar

Estos aparecieron al hacer funcionar lo nuevo, no salieron de la auditoría:

| Dónde | Qué pasaba |
|---|---|
| `orchestrator/manager.py` | Los estados de permiso mezclaban inglés (`pending`) y castellano (`resuelta`). Una comprobación que buscaba `pending` tras resolver no encontraba nada |
| `resource_manager.py` | En la rama de "recuperado y estable", `nivel_real` no se asignaba → `NameError`, justo en el camino que corre cuando el equipo se recupera |
| `resource_manager.py` | Con la fuente de pruebas, `metricas()` ignoraba su `ts` y decía siempre "recién medido": el sistema se fiaba de datos viejos |
| `memory/recuerdos.py` | Se guardaba el texto crudo y se buscaba por clave normalizada → los duplicados se colaban siempre |
| `memory/recuerdos.py` | Un recuerdo con 50 usos y 400 días ganaba a uno que sí hablaba del tema; el "usado mucho" pesaba más que el "habla de lo mismo" |
| `orchestrator/manager.py` | `decidir_permiso` no comprobaba si el permiso seguía pendiente: una solicitud ya resuelta o caducada se podía volver a aprobar |
| `authz.py` | Una autorización de 1 segundo se convertía en 60 por un `max(60, ...)` |
| `sqlite_store.py` | No había `log` a nivel de módulo; la migración de `audit_log` lo usaba |
| `auth.py` | Si el fichero del token existía pero fallaba la lectura, se generaba otro y **sobrescribía** el anterior: todos los clientes ya configurados se quedaban fuera de golpe, sin aviso. Ahora dice qué ha pasado en vez de rotar el token a ciegas |

---

## Segunda pasada: el auditor estático

Después de cerrar los 8 huecos, el auditor se ejecutó otra vez y se revisó
**uno por uno** lo que reportaba. Resultado: **318 → 294 hallazgos, y de
CRITICAL a MEDIUM, cero en ambos**.

| Qué se hizo | Por qué |
|---|---|
| **Descartado el CRITICAL** | La clave `phc_...` de PostHog estaba en `docker/mem0/mem0-server/`, código **descargado de otro proyecto** (`bajar-mem0.sh`, ni siquiera está en el repo). No es una credencial de JARVIS. El auditor ahora distingue código propio de código de terceros |
| **`auth.py` arreglado** | Es el único hallazgo que de verdad destruía algo (ver la tabla de arriba) |
| **14 `except` silenciosos → con rastro** | Sin cambiar el comportamiento: ahora se anota con `log.debug` por qué se salta un fichero ilegible, por qué no se guarda el estado del micro, por qué la tarea programada no se despierta ya |
| **Bucle de audio: espera adaptativa** | El worker de voz daba 50 vueltas por segundo aunque no hubiera micro ni sonido. Ahora 20 ms cuando hay algo que mirar y 100 ms cuando no. **Con el micro abierto el ritmo corto se mantiene siempre**, incluso en el instante justo después de vaciar la cola: si se dependiera de eso, la palabra clave respondería hasta 100 ms más tarde (`test_audio.py` lo comprueba) |
| **Qdrant cierra limpio** | Al salir, el destructor de `qdrant-client` imprimía una traza que parecía un crash. Ahora se cierra con `atexit` |

### Lo que queda en el auditor, y por qué se queda

**4 avisos HIGH, revisados y aceptados a propósito:**

- `auth.py: origen_permitido()` devuelve `False` si falla. Falla **cerrado**: si
  hay duda, se rechaza. Lo contrario sí sería un agujero.
- `adapter.py: contar_tests()` devuelve `None` si no puede leer los números. Es
  deliberado y está escrito en su documentación: *"Sin inventar: si no
  aparece, `None`"*. Un `0` inventado sería peor que no tener dato.
- `voice/manager.py: speak_out()` devuelve `False` si no hay salida de audio
  (PC sin altavoces). Quien llama lo entrega como texto, que es lo correcto.

Los **290 LOW** son el patrón defensivo habitual en piezas opcionales: que la
cámara no exista no debe tumbar el cerebro.

---

## Migración de la base de datos

Cinco tablas nuevas en `SCHEMA`, que se crean con `CREATE TABLE IF NOT EXISTS`:
`recuerdos`, `knowledge_docs`, `autorizaciones`, `agent_jobs`,
`resource_decisions`.

**No se borra ni reconstruye nada.** Las instalaciones existentes las crean al
abrir la base, como ya hacía el resto del esquema. La migración de `audit_log`
(que sí tenía que rehacerse por su `CHECK`) conserva fila a fila.

---

## Lo que sigue sin poder verificarse

- **Groq real**: este equipo no tiene `GROQ_API_KEY` ni red hacia
  `api.groq.com`. La lógica se prueba contra un doble HTTP que habla el
  protocolo real; la conexión auténtica sigue sin comprobarse.
- **Discord real**: no hay token de bot. Se comprueba la lógica del puente y el
  formato de los mensajes, no el ida y vuelta por la red de Discord.

**Qdrant ya no está en esta lista**: `qdrant-client>=1.9` estaba declarado en
los tres `requirements` pero no instalado aquí. Instalado y **verificado de
verdad**: la biblioteca indexa documentos reales y los recupera por significado
("puerto del servidor" encuentra una nota que habla del puerto 8000).