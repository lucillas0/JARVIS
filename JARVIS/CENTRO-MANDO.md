# JARVIS Live · Centro de mando

Este documento explica cómo está montado el centro de mando: qué backend hay
detrás de cada botón, qué eventos existen, cómo se guardan las cosas y qué se
puede recuperar cuando algo falla.

La regla que manda en todo el código:

> Si una acción no tiene un backend real detrás, no se ofrece.
> Si un dato no se puede medir, se dice que no se puede medir.

---

## 1. Arquitectura

```
USUARIO
   ↓
CEREBRO → AI Gateway (brain/ai_gateway: Groq, Gemini, OpenRouter,
│                        OpenCode para el código)
│                        ↓ bus llm.* + Permission Manager
↓
JARVIS LIVE  (brain/live: index.html · live.css · live.js)
   ↓  WebSocket /ws  +  HTTP /api/live, /api/dev, /api/cmd
CEREBRO (brain/main.py)
   ↓  Brain.emit  →  bus de eventos  (Live · Discord · voz · centro de mando)
   ↓
CENTRO DE MANDO (brain/command)          ORQUESTADOR (brain/orchestrator)
   ├── centers.py   salud, recursos, matriz de control
   ├── activity.py  lo que lee la pantalla principal
   ├── notify.py    notificaciones e incidentes desde el bus
   ├── services.py  conocimiento, automatizaciones, memoria, auditoría
   ├── runner.py    test center y checkpoints de Git
   ├── briefing.py  modo ausente, briefing, estado de los pasos
   ├── store.py     notificaciones, incidentes, decisiones, checkpoints
   └── routes.py    API /api/cmd
   ↓
SISTEMAS REALES
   ├── Risk Engine (permisos de JARVIS)
   ├── OpenCode Adapter (sesiones, permisos, preguntas, cambios)
   ├── planificador (tools/auto_tools.py) y protocolos (memory/protocols.py)
   ├── biblioteca de documentos (library.py) y memoria (memory/manager.py)
   ├── autocorrector (memory/selfheal.py) → incidentes
   ├── Git (runner.py) y herramientas del sistema (tools/syshealth_tools.py)
   └── voz, Discord, NAS, Campus, Spotify…
```

`brain/command` **no** sustituye a nada: cada centro llama al módulo que ya
existía. Lo único nuevo son cuatro tablas (notificaciones, incidentes,
decisiones, checkpoints) y una capa de presentación.

---

## 2. La API del centro de mando

Todas las rutas exigen el token de `brain/auth.py` y un origen permitido, como
el resto del panel.

| Ruta | Qué hace | Backend real |
|---|---|---|
| `GET /api/cmd/estado` | Una llamada con todo (salud, recursos, contadores, misiones, ausencias) | agregador |
| `GET /api/cmd/actividad` | La tarea protagonista: fases, progreso con base real, cambios, eventos | `command/activity.py` |
| `GET /api/cmd/permisos` | Pendientes, concedidos, denegados y ámbitos, de JARVIS y de OpenCode | RiskEngine + misiones + auditoría |
| `GET /api/cmd/preguntas` | Lo que OpenCode necesita decidir para seguir | `manager.preguntas_pendientes()` |
| `GET /api/cmd/capacidades` | Matriz de control real | `centers.matriz()` |
| `GET /api/cmd/salud` | Health Center | comprobación de cada subsistema |
| `GET /api/cmd/recursos` | CPU, RAM, disco, GPU, red | psutil / shutil / nvidia-smi |
| `GET /api/cmd/agentes` | Agentes detectados y qué se puede hacer con ellos | `apps.estado()` |
| `POST /api/cmd/agentes/{id}/start` | Abrir el programa del agente | `system_tools.open_app_handler` |
| `GET /api/cmd/notificaciones` · `POST …/{id}` | Bandeja real | `store` + bus |
| `GET /api/cmd/incidentes` · `POST …/{id}` | Incidentes reales | `store` + `selfheal` |
| `GET /api/cmd/auditoria` | Auditoría con filtros | `sqlite_store.list_audit` |
| `GET /api/cmd/historial` | Conversaciones | `recap.resumen` |
| `GET /api/cmd/memoria` | Memoria de JARVIS | `memory.manager` |
| `GET /api/cmd/conocimiento` · `/buscar` · `POST /indexar` · `/olvidar` | Documentos | `library.py` |
| `GET/POST /api/cmd/automatizaciones` · `…/{id}/pausar,reanudar,correr,borrar,editar` | Planificador | `tools.auto_tools` |
| `GET/POST/DELETE /api/cmd/protocolos` | Disparadores | `memory.protocols` |
| `GET/POST /api/cmd/decisiones` | Registro de decisiones | `store` |
| `GET /api/cmd/pruebas` · `POST …` · `GET …/{job}` | Test Center | ejecución real |
| `GET /api/cmd/cambios` · `/diff` · `POST /commit` | Git | `git` + RiskEngine |
| `GET/POST/DELETE /api/cmd/checkpoints` · `…/restaurar` | Puntos de retorno | parches + copia de ficheros nuevos |
| `POST /api/cmd/orden` | Hablar con JARVIS | `main.handle_user_message` |
| `POST /api/cmd/briefing` | Qué pasó mientras no estabas | auditoría + bus + misiones |
| `POST /api/cmd/diagnostico` | Diagnóstico | `tools/diag_tools` |
| `POST /api/cmd/sistema/optimizar` · `/disco` | Mantenimiento | `tools/syshealth_tools` + permiso |
| `POST /api/cmd/opencode/servidor` | Reiniciar el servidor de OpenCode | `orchestrator.service` + permiso |

Las acciones peligrosas (optimizar, limpiar disco, restaurar un checkpoint,
confirmar cambios, reiniciar servicios) **siempre** pasan antes por
`RiskEngine.check()`, que es el mismo Permission Manager que ya usaba JARVIS.

---

## 3. El ciclo de desarrollo con OpenCode

Vive en `brain/orchestrator` y se controla desde la vista **Misiones**.

```
IDEA → ANÁLISIS → PLAN + RIESGOS → APROBACIÓN → OPENCODE →
EJECUCIÓN → PERMISOS → PREGUNTAS → TESTS → VALIDACIÓN → RESULTADO
```

- **Adaptador real** (`adapter.py`): habla con la API HTTP del servidor de
  OpenCode (`/api/session`, `/prompt`, `/permission`, `/form`, `/message`,
  `/interrupt`, `/vcs/status`). No simula nada y no se salta su sistema de
  permisos: enseña lo que OpenCode pide y devuelve `once` / `always` /
  `reject`.
- **Estados**: `DRAFT`, `ANALYZING`, `PLANNING`, `WAITING_PLAN_APPROVAL`,
  `STARTING_OPENCODE`, `RUNNING`, `WAITING_PERMISSION`, `WAITING_USER`,
  `TESTING`, `FAILED`, `CANCELLED`, `COMPLETED`.
- **Tiempos**: análisis, plan, espera de aprobación, ejecución, espera de
  permiso, espera de decisión y tests, calculados con marcas de tiempo reales.
- **Persistencia**: tabla `dev_tasks`; una misión sobrevive al cierre de Live
  y al reinicio del cerebro.
- **Recuperación**: al arrancar, las misiones que quedaron en marcha se
  marcan como interrumpidas, se dice por qué y se ofrecen para continuar en la
  misma sesión de OpenCode. Nunca se relanzan solas.
- **Discord**: si el señor no está delante, la pregunta o el permiso llegan por
  DM y se responden con `2`, `PostgreSQL`, `una vez`, `denegar` o
  `responde: texto libre`.

---

## 4. Eventos

El centro de mando **no crea un bus**: se suscribe al que ya existe
(`Brain.emit`). Los eventos que consume:

| Evento | Notificación | Incidente |
|---|---|---|
| `opencode.permission_requested` | `permission` | — |
| `confirmation_request`, `heal_approval_request` | `permission` | — |
| `opencode.question_requested` | `question` | — |
| `opencode.completed` | `success` | — |
| `opencode.error`, `error_report`, `selfheal_update` | `error` | sí |
| `opencode.test_finished`, `live_task_start/end`, `agent_*` | `info` | — |

El mismo fallo no duplica: se refuerza con `occurrences`. El ruido
(`action_status`, `task_step`, `assistant_reply`) no genera nada.

---

## 5. Lo que no existe (y por tanto no se ofrece)

| Capacidad pedida | Estado real |
|---|---|
| Gestor de Docker | **No existe** en este sistema. La UI no lo muestra y la matriz lo declara. |
| Parar/reanudar agentes | `apps.py` es de solo lectura. Solo se ofrece *abrir* el programa. |
| Temperatura del procesador | No hay sensor expuesto: se muestra "no disponible". |
| Red (tráfico) | Se mide comparando lecturas de `psutil.net_io_counters`. |
| Porcentaje de avance de una misión | OpenCode no lo informa: **no hay barra inventada**. |
| Estado de cada paso del plan | Solo se marca COMPLETADO con evidencia real (fichero tocado o tests). |

### De dónde sale el porcentaje de la barra

La barra del protagonista no es un contador inventado: es
`pasos con evidencia / pasos del plan`. La cifra y su base se leen en
`GET /api/cmd/actividad` (`progreso` y `progreso_base`) y debajo se escribe,
por ejemplo, *«4 de 5 pasos con evidencia real»*. El agente no dice «voy por
el paso 3», así que el paso en curso se marca como *siguiente del plan*, nunca
como hecho.

---

## 6. La pantalla

`brain/live` son tres ficheros y nada más: `index.html`, `live.css` y
`live.js`. No hay una segunda interfaz uiña ni datos duplicados.

```
cabecera   marca · chips de recursos (CPU, RAM, GPU, disco, red) · estado
           global · reloj · notificaciones desplegables · configuración
sidebar    diez vistas con contador real, orbe de voz y enlace al asistente
principal  protagonista (tarea, progreso y fases) · plan · cambios ·
           línea de tiempo · agentes, y una vista propia por sección
lateral    atención (permisos, incidentes, preguntas) · permisos ·
           medidores de recursos · historial reciente
barra      una orden para JARVIS (`POST /api/cmd/orden`)
```

- **Un solo WebSocket** (`/ws?token=…`) y **un solo sondeo**: 4 s mientras hay
  una misión en marcha o algo pendiente, 12 s cuando la pantalla está
  tranquila, y un toque inmediato cuando el bus avisa de algo.
- **Todo lo que se pinta sale de la API.** Si una tecla no tiene backend, no
  existe la tecla.
- Accesible por teclado: navegación real, foco visible, `aria-live` en los
  avisos, enlace de salto al contenido y `prefers-reduced-motion` respetado.
- Sin Three.js, sin partículas ni animaciones que distraigan: líneas finas,
  una familia tipográfica y color solo donde significa algo.

---

## 7. Seguridad

- Nada se ejecuta sin pasar por `RiskEngine` cuando toca.
- JARVIS Live nunca modifica el Permission Manager ni las políticas de OpenCode.
- Los secretos no se muestran: las claves viven en `keys.env` y la API de
  configuración sigue siendo la única que las exporta (con token).
- Los identificadores que vienen de OpenCode se validan antes de meterse en una
  URL (`^[A-Za-z0-9_.:-]{1,120}$`).

---

## 8. Pruebas

```bash
python brain/orchestrator/test_ciclo.py   # 46 comprobaciones: ciclo de una misión
python brain/command/test_cmd.py          # 66 comprobaciones: bus, salud,
                                          # matriz, checkpoints, tests, API
                                          # y lo que lee la pantalla principal
```

Ninguno de los dos necesita OpenCode instalado: el primero usa un doble que
habla su API real por HTTP; el segundo trabaja sobre datos y repositorios
temporales. Los dos son de solo lectura sobre la instalación real: fuerzan
`JARVIS_DATA_DIR` a una carpeta temporal.

---

## 9. Configuración

No hay URLs, puertos ni rutas escritas a mano en el código: todo sale de
`brain/config.py` (`Config`, `DATA_DIR`, `ROOT_DIR`) y de las variables de
entorno ya existentes. El servidor de OpenCode se localiza por
`OPENCODE_SERVER_URL`, por su fichero de registro (`XDG_STATE_HOME` o
`~/.local/state/opencode/service.json`) o arrancándolo con
`opencode serve`.