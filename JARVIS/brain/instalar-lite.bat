@echo off
rem Instalador de dependencias de JARVIS en modo LITE (servidor o Windows).
rem Instala solo el cerebro: Python, sus librerias y la pizarra web.
rem NO instala Electron ni crea ventana de escritorio: eso es el modo FULL.
setlocal
cd /d "%~dp0"

echo.
echo   J.A.R.V.I.S.  ·  instalacion LITE
echo   ------------------------------------
echo.

where python >nul 2>nul
if errorlevel 1 (
  echo [X] No encuentro Python en este equipo.
  echo     Instale Python 3.10 o superior desde https://python.org
  echo     y marque "Add python.exe to PATH" durante la instalacion.
  pause
  exit /b 1
)

for /f "tokens=2" %%v in ('python --version 2^>^&1') do set PYVER=%%v
echo [1/4] Python detectado: %PYVER%

if not exist "venv" (
  echo [2/4] Creando el entorno propio...
  python -m venv venv
  if errorlevel 1 (
    echo [X] No se pudo crear el entorno virtual.
    pause
    exit /b 1
  )
) else (
  echo [2/4] El entorno virtual ya existe.
)

echo [3/4] Instalando dependencias del servidor (tarda unos minutos la primera vez)...
call venv\Scripts\python.exe -m pip install --upgrade pip --quiet
rem requirements-server.txt y NO requirements.txt: este dice ser LITE y lo que
rem trae el otro son torch, spacy, stanza, piper y openwakeword, que son cientos
rem de megabytes y son justo lo que el modo LITE deja fuera.
call venv\Scripts\python.exe -m pip install -r requirements-server.txt
if errorlevel 1 (
  echo.
  echo [X] Faltaron dependencias. Si falla por red, copie la carpeta
  echo     wheelhouse\ y ejecute esto otra vez.
  echo.
  echo     Si el error dice "Failed building wheel", tu Python es demasiado
  echo     nuevo para algun paquete. Instala Python 3.12 y vuelve a intentarlo.
  pause
  exit /b 1
)

echo [4/4] Aceleradores opcionales (si faltan, no pasa nada)...
for %%p in (httptools websockets) do (
  call venv\Scripts\python.exe -m pip install %%p --quiet --disable-pip-version-check
)

echo.
echo   Listo. Para arrancar JARVIS:
echo.
echo     venv\Scripts\python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000
echo.
echo   Y luego abra en el navegador:
echo.
echo     http://127.0.0.1:8000/modo     (elegir LITE o FULL)
echo     http://127.0.0.1:8000/setup    (poner las claves)
echo     http://127.0.0.1:8000/board    (la pizarra)
echo.
pause
