# JARVIS en Docker / Synology / Xpenology

Este directorio monta el **cerebro** de JARVIS como servidor: todo lo de
escritorio (Electron, el notch, las manos, la voz) se queda en tu PC y el
servidor se encarga de pensar, recordar y responder. Por eso no hay ni Electron
ni dependencias de audio en la imagen: en un NAS no hay ni pantalla ni tarjeta
de sonido, y lo que falta se avisa en `/health` como `"sin hardware"` en vez de
tirar el arranque.

## Lo primero, y lo importante

**No hace falta que crees ninguna carpeta, ni que compiles nada, ni que
instales modelos.** El `docker-compose.yml` de este repositorio está hecho
para eso:

- **No compila.** No lleva `build:`, así que Container Manager no construye
  nada: tira de la imagen ya publicada. En un NAS el build va por SMB y se
  queda colgado (o no termina nunca en un DS220+).
- **No usa carpetas.** Los datos van a volúmenes con nombre, que crea Docker
  solo. Es la causa número uno de «no funciona»: Synology no crea la carpeta
  que le pongas a un bind mount y el arranque muere con `Bind mount failed`.
- **No necesita `.env`.** Cada variable tiene su valor por defecto. Si pones
  un `.env` al lado, se usa; si no, el proyecto se crea igual.

Solo hay dos cosas que no se pueden automatizar y que son tuyas: **la carpeta
del proyecto** (DSM la pide siempre) y **la clave de IA**.

## Dos vías, y las dos son cortas

| Vía | Cuándo |
|---|---|
| [1. Container Manager](#1-container-manager-lo-mas-corto) | **La recomendada.** 4 clics y ya. |
| [2. Un script por SSH](#2-un-solo-script-ssh) | Si prefieres no usar la interfaz, o si quieres una instalación totalmente desatendida. |

---

## 1. Container Manager (lo más corto)

### Paso 1 · Copiar la carpeta

Desde File Station, copia esta carpeta `JARVIS` a una carpeta compartida, por
ejemplo `/volume1/docker/jarvis`. Es lo único que hay que hacer a mano, y es
porque DSM exige una ruta de proyecto.

### Paso 2 · El comando que prepara el resto

Por SSH, dentro de esa carpeta:

```sh
sh instalar-docker.sh
```

Eso, y solo eso. El script:

1. Arranca Container Manager si está parado.
2. **Baja la imagen**, para que el proyecto de Container Manager no tenga que
   compilar nada.
3. **Crea el `.env`** con tus ids de usuario del DSM, para que los ficheros
   no acaben como `root` y no puedas borrarlos desde File Station. Sin esto es
   el problema número dos.
4. **Te pregunta la clave de IA** al final y la guarda.
5. **Comprueba que el puerto esté libre**, para no perder después tres
   minutos de descarga en un puerto que ya tenía otro.

Si la clave no la tienes a mano, no pasa nada: pulsa Intro y sigue. La pones
después en el navegador.

### Paso 3 · Los 4 clics

En **Container Manager → Proyecto → Crear**:

1. **Ruta del proyecto**: la carpeta donde dejaste `JARVIS`.
2. **Fuente**: *Usar un archivo `docker-compose.yml` existente*.
3. **Nombre**: `jarvis`.
4. **Crear**.

Espera un par de minutos a que termine de tirar la imagen y abre:

```
http://IP-DEL-NAS:8000/setup     <- si no pusiste la clave antes, aquí
http://IP-DEL-NAS:8000/         <- la interfaz
```

La clave que pongas en `/setup` **se guarda en el volumen**, no en el
contenedor. Antes se perdía en cada actualización y había que volver a
pegarla; ahora sobrevive.

### Para actualizar a una versión nueva

```sh
sh instalar-docker.sh --actualizar
```

Y luego, en Container Manager, botón derecho sobre el proyecto → *Acción* →
*Detener* y luego *Construir*. Tus datos y tus claves se quedan: viven en el
volumen, no en el contenedor.

---

## 2. Un solo script (SSH)

Si no quieres usar la interfaz de Container Manager nunca:

```sh
cd /volume1/docker/jarvis
sh instalar-nas.sh
```

Un `docker run` y se acabó. Sin `.yml`, sin redes propias, sin claves de
despliegue y sin build en el NAS. Crea las carpetas que falten, tira de la
imagen y espera a que `/health` conteste.

```sh
JARVIS_PUERTO=8080 sh instalar-nas.sh          # otro puerto
JARVIS_DATOS=/volume2/jarvis sh instalar-nas.sh # datos en otro volumen
PUID=1026 PGID=100 sh instalar-nas.sh           # tu usuario del NAS
sh instalar-nas.sh --actualizar                 # actualizar
```

---

## Tus archivos: el share del NAS

El contenedor trae el share montado en **solo lectura**. Por defecto apunta a
`/volume1` de Synology, así que desde el explorador de la pizarra (`/board`)
puedes recorrer tus carpetas, buscarlas y abrir los ficheros en el navegador.

No tienes que configurar nada: está en el `docker-compose.yml` con su valor por
defecto. Si tu NAS tiene los datos en otro volumen, o quieres poner un
`.env` al lado del proyecto:

```ini
JARVIS_NAS=/volume2          # el share que se monta
JARVIS_RAICES=/mnt/nas,/data # desde donde se puede leer
```

### Por qué solo lectura

El `:ro` no es cosmetico. JARVIS ya puede borrar ficheros por voz, y con el
share entero montado en lectura y escritura un «borra las descargas» mal
interpretado se lleva un share por delante. Si la tienes clara y quieres que
escriba, cambia `:ro` por `:rw` en la línea del `docker-compose.yml` y
reinicia el contenedor.

### Dónde se ven

En `/board`, el panel de ficheros. La raíz te enseña los puntos de montaje que
hay de verdad, leídos de `/proc/mounts`, y no una lista fija. En un contenedor no
hay escritorio, así que pinchar un fichero no abre una ventana como en Windows:
lo que hace es mandarlo al navegador, donde las imágenes, los PDF, el texto, el
audio y el vídeo se ven directamente y el resto se descarga.

### Qué puede leer JARVIS

Listar carpetas no tiene lista blanca: con el token se recorre el share entero.
**Servir el contenido de un fichero sí la tiene** (`JARVIS_RAICES`), porque sin
eso cualquiera con el token se bajaría el `.env` con todas las claves de API.
Por eso `/app/brain/.env` devuelve 403 aunque tenga el token.

### Jarvis en un servidor: lo que cambia

Un NAS no es un escritorio, y el código se dio cuenta:

- **El prompt es otro.** El de escritorio iba entero (912 palabras) con reglas
  de Spotify, ventanas, teclas globales, ver la pantalla, VirtualBox, campus y
  el puente de Discord. En un servidor no se pueden hacer, y no eran ruido
  inofensivo: el modelo las leía, veía que preguntabas por un fichero y
  contestaba con lo que esperaba de un escritorio en vez de usar la
  herramienta. El de servidor son 608 palabras, con un bloque que sí explica
  dónde está y qué puede hacer.
- **27 herramientas no se ofrecen al modelo** en Linux: las de ventanas,
  teclas multimedia, Defender, VirtualBox y las que abren PowerShell para
  enumerar cámaras. Se siguen viendo en `/api/tools` con `disponible: false`
  y el motivo, para que se pueda comprobar. Las otras 82 funcionan.
- **`list_dir` entra siempre** en el catálogo del servidor, y `open_app`,
  `open_file` y `vbox_list` salen: no tienen sentido ahí y el modelo elegía
  antes ellas que mirar el NAS.
- **El resultado de las herramientas de datos llega al modelo.** Las que
  devuelven datos (leer un fichero, listar, un diagnóstico) pasan por una ronda
  de razonamiento. Si una se queda fuera de esa lista, la herramienta se
  ejecuta, se traga el resultado y el modelo contesta sin verlo. Pasó con
  `analizar_archivo`, y el síntoma era que pedías leer un fichero y te decía
  que no tenía permisos, siendo verdad que el fichero se leía bien.

Se comprueba desde fuera sin necesidad de credenciales:

```sh
curl -s http://IP:PUERTO/health | python -m json.tool
# -> "herramientas": {"total": 109, "disponibles": 82, "no_disponibles": 27}
# -> "providers": "Gemini, OpenRouter"
```

### Si dice «sin modelos disponibles»

No le pasa al modelo. Pasa cuando el sondeo de internet que se hacía **antes**
de elegir proveedor no contestaba: se consultaba un único host y, si ese host
no llegaba, la lista de proveedores se quedaba vacía sin probar ninguno, aunque las
tres claves fueran válidas. En un NAS basta con que el DNS del router resuelva
otra cosa.

Ahora lo que decide es **qué claves hay**, no si un host contesta, y el sondeo
se ha movido a un host neutro. Si aun así falla, el registro dice qué se
intentó y por qué:

```sh
docker logs jarvis 2>&1 | grep -A3 "Todos los proveedores fallaron"
```

---

## Domotica: Home Assistant en el mismo NAS

Una Synology no habla con bombillas. Lo que habla es **Home Assistant**, que
corre en el mismo NAS por Container Manager y expone una API REST estándar.
Por eso este plugin no depende de ninguna marca: sirve para Philips Hue,
Shelly, Sonoff, Zigbee o Matter, porque todo eso acaba siendo una entidad de
Home Assistant.

| Herramienta | Qué hace |
|---|---|
| `domotica_estado` | Cómo está algo: encendida, apagada, a qué temperatura, abierta. |
| `domotica_lista` | Qué hay en casa. Sin dominio, cuenta por tipo. |
| `domotica_accion` | Enciende, apaga, regula el brillo o la temperatura. |

### Montarlo

1. En Home Assistant, perfil (abajo a la izquierda) → **Tokens de acceso de
   larga duración** → Crear token. Se copia una vez; Home Assistant no lo
   vuelve a mostrar.
2. En el `.env` del proyecto:

```ini
HA_URL=http://192.168.1.10:8123
HA_TOKEN=el-token-que-acabas-de-copiar
HA_INSEGURO=0        # ponlo a 1 solo si usas un certificado autofirmado
```

### Dos decisiones que no son óbvias

**Habla como una persona, no como la API.** El modelo dice «enciende la luz del
salón», y el `entity_id` puede ser `light.salon_luz_principal` o como lo
llamara cada uno al instalarlo. El plugin busca por el nombre que sale en la
app, entiende que «luz» es un tipo y no la persiana de al lado, y traduce
«enciende» a `light.turn_on» y «abre» a `cover.open_cover`.

**Las cerraduras y las persianas piden confirmación.** Encender una bombilla no.
Una puerta de garaje, sí: el plugin devuelve `confirmacion_requerida` y JARVIS
pregunta. Si el señor dice que sí, se vuelve a llamar con `confirmado: true`.

### Lo que dice si algo falla

No contesta «no encuentro eso» cuando lo que pasa es que el token caducó o que
Home Assistant está parado. Son dos cosas distintas y cada una tiene su
arreglo, así que se distinguen: primero se pregunta si la casa contesta, y solo
después se busca el aparato.

Cuando Home Assistant no responde se paga **un** timeout de 8 s y las
siguientes llamadas responden en 0 ms durante un minuto, igual que con el NAS.

---

## Ficheros

| Fichero | Para qué |
|---|---|
| `docker-compose.yml` | El proyecto de Container Manager. Sin build, sin carpetas, sin `.env` obligatorio. |
| `instalar-docker.sh` | Prepara el proyecto y te deja a 4 clics de que funcione. |
| `instalar-nas.sh` | La vía de un solo `docker run`, sin compose. |
| `Dockerfile` | Imagen mínima: Python + el cerebro. Multi-arch (amd64 y arm64). |
| `docker/entrypoint.sh` | Lo que va antes de uvicorn: comprueba permisos, avisa de lo que falta y baja a PUID/PGID. |
| `configuracion.env.example` | Todas las opciones, comentadas y con los valores por defecto reales. Se copia a `.env` si lo quieres. |

### Hablar con el NAS: la API de DSM

Con el share montado, el brain ve tus archivos pero **no tu NAS**: no sabe cuánto
disco queda, qué contenedores corren ni si el equipo se calienta. Para eso está
la API de DSM, y hay cuatro herramientas que la usan:

| Herramienta | Qué contesta |
|---|---|
| `nas_espacio` | Cuánto disco queda por volumen, con aviso si alguno está lleno. |
| `nas_salud` | CPU, RAM, temperatura, modelo y uptime. |
| `nas_contenedores` | Qué hay en Container Manager y qué está corriendo. |
| `nas_registro` | Las últimas líneas del registro del NAS. |

En el `.env` del proyecto:

```ini
DSM_NAS_URL=http://192.168.1.10:5000   # 5000 es la API por http, 5001 por https
DSM_NAS_USER=mi_usuario
DSM_NAS_PASSWORD=mi_clave
DSM_NAS_INSEGURO=1                     # 0 solo si el NAS tiene certificado válido
```

Sin esto no se rompe nada: las herramientas lo dicen en claro en vez de
inventarse una respuesta. El puerto **no** es el de DSM en el navegador, que es
el mismo número con otro protocolo detrás (5000 http / 5001 https).

---

## Plugins

`brain/tools/custom_tools.py` es la vía para ampliar JARVIS sin tocar el
núcleo: se sueltan handlers y entradas en `CUSTOM_TOOLS`, y arrancan solos.
Los que vienen de serie:

- **Agenda y correo.** `agenda_de_hoy` y `correo_sin_leer`. Los módulos
  (`integrations/calendar.py`, `integrations/gmail.py`) ya estaban escritos y
  funcionaban, pero ninguna herramienta los exponía: solo los usaba el ritual
  de las mañanas. Preguntar en una conversación normal devolvía un «Procederé a
  revisarlo, señor» y nada más.
- **El NAS.** Las cuatro de arriba, sin dependencias: la API de DSM es HTTP y
  JSON, y meter una librería por eso añadiría seis paquetes a la imagen.
- **Domotica.** `domotica_estado`, `domotica_lista` y `domotica_accion`, contra
  la API de Home Assistant. Sin dependencias también.

El sistema **se amplía en caliente**: `custom_tools.py` se carga solo al
arrancar, y el reparador lo vuelve a leer sin reiniciar.

Un detalle que se nota: cuando el NAS no responde, las llamadas tardan **una
vez** 8 s (el timeout) y las siguientes **0 ms** durante un minuto. Antes cada
capa reintentaba por su cuenta y se pagaban cuatro timeouts seguidos: 32
segundos de espera invisible.

### Variables del share

| Variable | Por defecto | Qué hace |
|---|---|---|
| `JARVIS_NAS` | `/volume1` | El share del NAS que se monta en `/mnt/nas`, en solo lectura. |
| `JARVIS_RAICES` | `/mnt/nas,/data` | Desde dónde se puede servir un fichero. Fuera de aquí, 403. |

---

## Por qué el compose es así

Si te lo encuentras en otra parte, esto es lo que importa y por qué. Los cuatro
puntos del fichero original que rompían algo están resueltos:

- **No declara `networks:`.** Docker Compose 5.1.0, el que lleva DSM 7.1 y 7.2,
  falla con `Container cannot be connected to network endpoints` contra cualquier
  daemon con API menor de 1.44 en cuanto un servicio declara su propia red. Sin
  declarar nada va a la red por defecto y desaparece el problema.
- **No usa `user:`.** El contenedor arranca como root y el entrypoint ya baja a
  `PUID`/`PGID` solo. Con `user:` el entrypoint corría sin permisos y no podía
  arreglar nada, que es como `/app/brain/db` se quedaba sin permisos y el
  arranque moría con un `PermissionError`.
- **No lleva `deploy:`.** Fuera de Swarm se ignora, y hay versiones de Container
  Manager que se atascan al leerlo.
- **No usa `env_file:` ni monta `./.env` en `/app/.env`.** El proyecto se crea
  aunque no haya `.env`, y el que haya solo sirve para sustituir los
  `${VAR:-defecto}`. Montar el `.env` era además decorativo: el sitio donde lo
  lee `brain/config.py` es `/app/brain/.env`, no `/app/.env`.

### El fallo que quedaba: las claves se perdían

`_keys_env_path()` en `brain/main.py` devolvía `%APPDATA%/JARVIS/keys.env`, y en
Linux y en Docker `APPDATA` no existe: devolvía cadena vacía. Las claves que
ponías en `/setup` se guardaban **dentro del contenedor**, en `/app/brain/.env`,
y se evaporaban en cuanto se recreaba. En un NAS eso significaba pegar la
clave otra vez en cada actualización, a mano.

Ahora `brain/config.py` busca las claves en tres sitios, en este orden, y el
segundo es el que faltaba:

```
%APPDATA%/JARVIS/keys.env   →  Windows
DATA_DIR/keys.env           →  Linux, NAS y Docker (el volumen)
brain/keys.env              →  por si acaso
```

Y al guardar, `Config` se actualiza en memoria, así que la clave **funciona sin
reiniciar el contenedor**.

---

## Por qué antes no levantaba

Tres cosas, en orden de probabilidad:

1. **`PermissionError` al arrancar.** `brain/memory/sqlite_store.py` creaba
   `brain/db/` con un `os.makedirs` sin `try`. Como `.dockerignore` quita
   `brain/db/` de la imagen y el contenedor corre con el usuario del NAS, `/app`
   es de root y ahí no se puede crear. Ahora el `makedirs` está protegido y el
   Dockerfile deja esa carpeta creada y con permisos de escritura.
2. **`Bind mount failed`.** Synology no crea las carpetas que aparecen en un
   bind mount. Ya no hay bind mounts: los volúmenes con nombre los crea Docker.
3. **La red del compose**, con el fallo de Compose 5.1.0 sobre daemons viejos
   descrito más arriba.

## Qué hay dentro

- **Solo el cerebro.** Sin Electron, sin `sounddevice`, sin `piper`, sin `torch`,
  sin `openwakeword`. De las ~200 dependencias del escritorio se queda una
  docena. Los imports de audio están todos dentro de `try/except` o de funciones,
  así que su ausencia no tumba nada.
- **La interfaz no sale a internet.** `brain/ui/index.html` lleva `three.js`
  dentro y todos sus endpoints son del mismo origen (`location.origin`). No hay
  ni un CDN: funciona con el NAS sin salida a la nube.
- **Modo LITE por defecto.** En un contenedor sin Electron, la pizarra web no
  puede enseñarte lo que hace el escritorio, así que JARVIS arranca en lite la
  primera vez y lo recuerda en `/data/modo.txt`. Se cambia en `/modo`.

## Configuración

Casi todo se puede cambiar desde el navegador, en `http://IP:8000/setup` y en
`/config`. Lo que sigue es para lo que prefieras tener en un fichero: copia
`configuracion.env.example` a `.env` en la carpeta del proyecto y pon lo que
quieras. Si no lo pones, se usan los valores por defecto del compose.

Lo único imprescindible para que conteste algo es **una** clave de modelo
(Groq, Gemini u OpenRouter). Sin ninguna, el contenedor **arranca pero no
responde**: es un aviso, no un fallo, y el entrypoint lo dice al arrancar y en
los logs. Además, si no hay clave, `/` te manda a `/setup` en vez de a la
interfaz, para que la pongas sin buscar nada.

Lo que conviene poner en un NAS:

```env
SCREEN_ANALYSIS_MODE=off   # en un NAS no hay pantalla
PUID=1026                  # id -u de tu usuario del DSM
PGID=100                   # id -g de tu usuario del DSM
```

## Seguridad

`/health`, `/api/*` y `/ws` **no piden contraseña**. El compose abre el puerto
solo en la LAN, y conviene que siga así. Si necesitas llegar desde fuera, pon
delante algo que autentique (Caddy, nginx con basic auth, Cloudflare Tunnel) y
no expongas el 8000 a internet.

Ojo también a la cámara: `/api/camara/*` responde con
`Access-Control-Allow-Origin: *` para poder usarse desde el tracker de gestos.
En la LAN da igual; no lo expongas a internet.

## Comandos

```sh
docker logs -f jarvis          # ver lo que pasa
docker restart jarvis          # reiniciar
docker stop jarvis             # parar
docker rm -f jarvis            # borrar el contenedor (los datos se quedan)
```

Los datos viven en el volumen `jarvis_data`: base de datos, memoria, registros
y tus claves. Borrar el contenedor no los toca.

Para verlo:

```sh
docker volume inspect jarvis_data
```

## Comprobar que va

```sh
curl http://IP_DEL_NAS:8000/health
```

Debería salir algo con `"status":"ok"`. Si `"voice"` sale en `false`, es lo
normal en un NAS: no hay tarjeta de sonido.