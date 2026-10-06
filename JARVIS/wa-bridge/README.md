# Puente WhatsApp self-chat para JARVIS

Te escribes a ti mismo en WhatsApp ("Mensajes a ti mismo") y JARVIS responde ahí.

## Requisitos
- Node 18+ (`node --version`)
- JARVIS en marcha (el brain en `http://127.0.0.1:8000`)

## Primera vez
```bat
cd E:\jarvis-handoff\JARVIS\wa-bridge
npm.cmd install
npm.cmd start
```
Escanea el QR que sale en la consola con **WhatsApp > Ajustes > Dispositivos vinculados > Vincular**.
La sesión queda guardada en `wa-bridge/session/` (no la borres: es el "dispositivo").

## Uso diario (automático)
Desde la versión instalada, el puente **arranca solo y en silencio** con JARVIS
(sin ventana): sesión en `%APPDATA%\JARVIS\brain-data\wa-session`,
log en `%APPDATA%\JARVIS\wa-bridge.log`.
No arranques otro puente a mano a la vez (se pisarían la sesión).

## Uso manual (desarrollo)
`npm.cmd start` dentro de `wa-bridge` (consola visible con QR y logs).

## Variables de entorno (opcionales)
| Variable | Defecto | Para qué |
|---|---|---|
| `WA_BRAIN_URL` | `http://127.0.0.1:8000` | URL del brain |
| `WA_SESSION_DIR` | `wa-bridge/session` | Dónde guardar la sesión vinculada |
| `WA_PREFIX` | (vacío = todo) | Ej. `!`: solo responde a mensajes que empiecen por `!` |
| `WA_BRAIN_TIMEOUT` | `180000` ms | Timeout esperando la respuesta del brain |
| `WA_LOOKBACK_SEC` | `900` (15 min) | Responde a mensajes recientes aunque se escribieran justo antes de arrancar |
| `WA_DEBUG` | `1` | `1` = muestra una línea `DBG` por cada mensaje recibido (útil para diagnosticar) |
| `WA_OWNER` | (usa `owner.json`) | Número propietario alternativo sin editar el fichero |

## Si no responde
1. Cierra cualquier puente viejo (solo uno a la vez).
2. Arranca con `npm.cmd start` y espera a `Propietario verificado`.
3. Escribe en tu self-chat.
4. Mira las líneas `DBG`: dicen `type`, `remote`, `fromMe`, `self=true/false` y el texto. Si `self=false`, pégame esa línea y lo ajusto.

## Seguridad: solo tú
- El puente **solo arranca** si la sesión vinculada es el número de `owner.json` (`34643351642`). Si no coincide, se apaga solo sin responder a nadie.
- Además **solo atiende tu self-chat**: ignora grupos y chats ajenos a propósito.
- Para cambiar de número: actualiza `owner.json` (o la variable `WA_OWNER`), borra `session/` y re-escanea el QR.

## Límites honestos
- Texto, audios e imágenes. Los audios se transcriben (Whisper) y se responden **por voz** (Piper); las fotos se analizan con visión y se responden por texto.
- Solo tu self-chat: ignora grupos y chats ajenos a propósito.
- Las acciones "sensibles" (borrar, apagar…) piden confirmación en el PC; desde el móvil caducan a los 30s y se cancelan.
- El PC debe estar encendido. Vía no oficial: riesgo bajo en self-chat, pero existe.
- Si WhatsApp cierra la sesión (cambio de móvil, etc.): borra `session/` y re-escanea el QR.
