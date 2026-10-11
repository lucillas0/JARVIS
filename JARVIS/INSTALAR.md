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
avisos. Al final te dice cuales fallaron.

Cosas que pasan de vez en cuando:

| Que pasa | Que hacer |
|---|---|
| `No se pudo crear el entorno virtual` | En Debian/Ubuntu: `sudo apt install python3-venv python3-pip` |
| Falla todo al descargar | Revisa el proxy o la conexion. Se puede usar `--proxy` de pip. |
| `No he podido dejar .env en 600` | Sistema de ficheros que no soporta permisos (NTFS). Restringelo a mano. |
| Falla un grupo opcional | Es normal: son paquetes pesados o especificos. JARVIS funciona igual. |

Para rehacer todo y empezar limpio: `python instalar.py --rehacer`.

## Requisitos

- Python 3.10 o superior. Nada mas: ni Node, ni Docker, ni compilador.
- Alrededor de 1,5 GB de disco para el entorno completo, unos 300 MB sin voz.

## Para el servidor

Esto es para el escritorio y para el modo LITE. Si lo que quieres es el
cerebro en un NAS o un servidor con Docker, mira [DOCKER.md](DOCKER.md) y usa
`instalar.sh` de ahi: ese es el que levanta el contenedor.