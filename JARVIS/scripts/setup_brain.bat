@echo off
REM JARVIS - Instala dependencias del brain (Python) en un venv local
setlocal
cd /d "%~dp0..\brain"

REM Detectar intérprete Python real (descarta el stub de Microsoft Store)
set PYTHON=
for /f "usebackq delims=" %%P in (`py -c "import sys; print(sys.executable)" 2^>nul`) do set PYTHON=%%P
if defined PYTHON ( if exist "%PYTHON%" goto :got_python )
for /f "usebackq delims=" %%P in (`python -c "import sys; print(sys.executable)" 2^>nul`) do set PYTHON=%%P
if defined PYTHON ( if exist "%PYTHON%" goto :got_python )
for /f "usebackq delims=" %%P in (`python3 -c "import sys; print(sys.executable)" 2^>nul`) do set PYTHON=%%P
if defined PYTHON ( if exist "%PYTHON%" goto :got_python )
if exist "%LOCALAPPDATA%\Programs\Python\Python312\python.exe" (
  set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python312\python.exe"
  goto :got_python
)
if exist "%LOCALAPPDATA%\Programs\Python\Python311\python.exe" (
  set "PYTHON=%LOCALAPPDATA%\Programs\Python\Python311\python.exe"
  goto :got_python
)
if exist "%ProgramFiles%\Python312\python.exe" (
  set "PYTHON=%ProgramFiles%\Python312\python.exe"
  goto :got_python
)
echo [JARVIS] ERROR: no encuentro Python real (3.11/3.12). Instala Python desde python.org.
exit /b 1
:got_python
echo [JARVIS] Usando Python: %PYTHON%

REM Recrear venv si falta o está roto (pyvenv.cfg apunta a un home que ya no existe)
set NEED_FIX=0
if not exist venv\Scripts\python.exe set NEED_FIX=1
if exist venv\pyvenv.cfg (
  for /f "tokens=1,* delims==" %%H in ('findstr /b "home" venv\pyvenv.cfg') do (
    if not exist "%%I\python.exe" set NEED_FIX=1
  )
) else (
  set NEED_FIX=1
)
if "%NEED_FIX%"=="1" (
  if exist venv (
    echo [JARVIS] venv detectado como roto. Recreándolo...
    rmdir /s /q venv
  )
)

if not exist venv (
  echo [JARVIS] Creando entorno virtual Python...
  "%PYTHON%" -m venv venv
)

set PY=venv\Scripts\python.exe
if not exist %PY% (
  echo [JARVIS] ERROR: no se pudo crear el venv.
  exit /b 1
)

REM .env por defecto si no existe
if not exist .env (
  copy /Y .env.example .env >nul
  echo [JARVIS] Se creo brain\.env a partir de .env.example
)

REM Instalar solo si cambió requirements.txt; start.bat y build.bat comparten este venv.
set REQUIREMENTS_HASH=
for /f "usebackq delims=" %%H in (`%PY% -c "import hashlib; print(hashlib.sha256(open('requirements.txt','rb').read()).hexdigest())"`) do set REQUIREMENTS_HASH=%%H
if not defined REQUIREMENTS_HASH (
  echo [JARVIS] ERROR calculando la huella de requirements.txt.
  exit /b 1
)
set INSTALLED_HASH=
if exist venv\requirements.sha256 set /p INSTALLED_HASH=<venv\requirements.sha256
if not "%INSTALLED_HASH%"=="%REQUIREMENTS_HASH%" goto :install_requirements
echo [JARVIS] Dependencias ya instaladas; se omite pip.
goto :requirements_ready

:install_requirements
echo [JARVIS] Instalando dependencias del brain...
%PY% -m pip install -r requirements.txt
if errorlevel 1 (
  echo [JARVIS] ERROR instalando dependencias del brain.
  exit /b 1
)
>venv\requirements.sha256 echo %REQUIREMENTS_HASH%

:requirements_ready

REM Genera iconos e inicializa la base de datos
%PY% -c "import scripts.make_assets; scripts.make_assets.main()" >nul 2>&1

echo [JARVIS] Brain listo. Uso: start.bat
endlocal