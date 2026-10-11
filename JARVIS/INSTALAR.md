# Instalar JARVIS

Un instalador, el mismo para Windows, Linux y macOS. Pone el Python, las
dependencias y te pregunta las claves de API.

## Empezar

**Windows:** doble clic en `instalar.bat`

**Linux / macOS:**

```sh
chmod +x instalar.sh
./instalar.sh
```

Los dos son lanzadores que buscan un Python y pasan el trabajo a
`instalar.py`, que es el que hace todo. Si ya tienes Python a mano:

```sh
python instalar.py
```

## Qué hace

1. **Comprueba Python.** Necesitas 3.10 o superior. Si no lo encuentra te dice
   cómo instalarlo en tu sistema.
2. **Crea el entorno.** Un `venv` dentro de `brain/`, o reutiliza el que ya
   tengas en `%APPDATA%\JARVIS\brain-data\venv` si vienes de la app de
   escritorio. No te descarga otra vez lo que ya tienes.
3. **Instala por grupos.** No todo de golpe: si un paquete falla, avisa y sigue.
4. **Te pregunta las claves** que falten. Las que ya tengas las deja como
   están.
5. **Lo comprueba de verdad**: importa el servidor y lee su configuración. Si
   eso no funciona, no dice que ha terminado bien.

## Opciones

```sh
python instalar.py                  # normal
python instalar.py --rapido         # solo lo esencial, sin escritorio ni voz
python instalar.py --todo           # ademas voz y modelos locales (pesado)
python instalar.py --sin-claves     # no preguntar por las claves
python instalar.py --solo-claves    # solo configurar o cambiar las claves
python instalar.py --rehacer        # rehacer el entorno desde cero
```

`instalar.bat` y `instalar.sh` pasan las opciones igual:
`instalar.bat --rapido`.

## Las claves

Pregunta por Groq, Gemini, OpenRouter, Discord, Spotify, tiempo y VirusTotal.
Con una basta para que JARVIS conteste; las demas son opcionales.

Se guardan en `brain/.env`, que es el mismo archivo que usa la app de
escritorio, así que no se pisan entre si. Las que ya estaban no se vuelven a
preguntar, y si hay lineas duplicadas se limpian al guardar.

**Va en claro.** El instalador deja el fichero en permisos `600` y avisa si el
sistema de ficheros no dejarlo en solo lectura. No lo subas a ningun repositorio ni
lo compartas.

## Si algo falla

El instalador se para solo si falla el grupo del servidor; el resto son
avisos. Al final te dice cuales fallaron, y **el motivo de cada uno**: no dice
"revisa la red" a todo, que era lo que pasaba antes.

Los errores de Windows que mas se dan, y que son los que suelen parecer
"problemas de seguridad":

| Lo que pone | Que pasa de verdad | Que hacer |
|---|---|---|
| `la red esta inspeccionando las conexiones y el certificado no cuadra` | El certificado TLS no valida: tipico en redes de empresa o universidad que inspeccionan el trafico | `pip install --trusted-host pypi.org --trusted-host files.pythonhosted.org <paquete>`, o configura el proxy con `set HTTPS_PROXY=http://servidor:puerto` |
| `Windows no deja escribir o ejecutar` | El antivirus esta bloqueando el entorno nuevo | Cierra el antivirus mientras instala, o anade `brain\venv` a las exclusiones. Tambien vale ejecutar la consola como administrador |
| `no queda espacio en disco` | Faltan los ~1,5 GB del entorno | Libera espacio y repite |
| `falta un compilador para construir ese paquete` | Un paquete quiere Visual Studio Build Tools | Si solo quieres el servidor: `instalar.bat --rapido` |
| `No encuentro Python` o abre la Microsoft Store | El Python de la Store no se ejecuta, abre la tienda | Descarga el de python.org marcando "Add python.exe to PATH" |

### Lo de "viene de internet"

Si bajaste un ZIP, Windows marca los ficheros y a veces se niegan a tocarlos.
`instalar.bat` lo resuelve solo: lo primero que hace es quitar esa marca con
`Unblock-File`, asi que no tienes que hacer nada.

Si aun asi Windows dice que elscript esta bloqueado,click con el boton derecho
en Properties y pulsa **Unblock** (Desbloquear).

### Si el antivirus lo corta

A veces Defender o el antivirus de tu empresa matan `python.exe` del entorno a
mitad de la instalacion. Despues, el grupo falla con "Windows no deja escribir
o ejecutar". Anade de antemano la carpeta a las exclusiones:

- **Windows Defender:** Seguridad de Windows > Proteccion contra virus > &
  amenaza > Gestionar configuracion > Exclusions > Agregar exclusion > carpeta
- ** otro antivirus:** lo de siempre, carpeta `brain\venv`

Para rehacer todo y empezar limpio: `python instalar.py --rehacer`.

## Requisitos

- Python 3.10 o superior. Nada mas: ni Node, ni Docker, ni compilador.
- Alrededor de 1,5 GB de disco para el entorno completo, unos 300 MB sin voz.

## Para el servidor

Esto es para el escritorio y para el modo LITE. Si lo que quieres es el
cerebro en un NAS o un servidor con Docker, mira [DOCKER.md](DOCKER.md) y usa
`instalar.sh` de ahi: ese es el que levanta el contenedor.