@echo off
rem =============================================================================
rem  JARVIS - instalador para Windows
rem =============================================================================
rem  Este archivo solo busca un Python y llama a instalar.py, que es el que hace
rem  el trabajo de verdad.
rem
rem  Va en ASCII puro y sin BOM a proposito: cmd.exe lee el .bat con la pagina
rem  de codigos del sistema, y un BOM o un acento salen como caracteres raros y
rem  rompen la primera linea. Los textos con tildes van en instalar.py, que si
rem  sabe mostrarlos bien.
rem =============================================================================
setlocal EnableExtensions
cd /d "%~dp0"

rem --- Quitar la marca de "viene de internet" ---------------------------------
rem Si bajaste un ZIP, Windows marca los ficheros y algunos programas se
rem niegan a tocarlos. Unblock-File quita esa marca; si no hay nada marcado,
rem no hace nada y no falla.
if exist "%~dp0*" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Unblock-File -Path '%~dp0*' -ErrorAction SilentlyContinue" >nul 2>&1
)

echo.
echo   J.A.R.V.I.S.  -  instalacion
echo   ------------------------------
echo.

rem --- Buscar Python ---------------------------------------------------------
rem Se prueban por orden: el lanzador "py" es el mas fiable en Windows porque
rem sabe cual es el Python por defecto aunque no este en el PATH.

set "PYTHON="

for /f "delims=" %%p in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do (
    if exist "%%p" if not defined PYTHON set "PYTHON=%%p"
)

if not defined PYTHON (
    for /f "delims=" %%p in ('where python 2^>nul') do (
        if not defined PYTHON if exist "%%p" set "PYTHON=%%p"
    )
)

rem El Python de la Tienda (WindowsApps) abre la tienda en vez de ejecutar:
rem si es ese, se descarta y se sigue buscando uno de verdad.
if defined PYTHON (
    echo %%PYTHON%% | find /i "WindowsApps" >nul && set "PYTHON="
)

if not defined PYTHON (
    for /f "delims=" %%d in ('dir /b /ad "%ProgramFiles%\Python3*" 2^>nul') do (
        if not defined PYTHON if exist "%ProgramFiles%\%%d\python.exe" set "PYTHON=%ProgramFiles%\%%d\python.exe"
    )
)

if not defined PYTHON (
    for /f "delims=" %%d in ('dir /b /ad "%LocalAppData%\Programs\Python\Python3*" 2^>nul') do (
        if not defined PYTHON if exist "%LocalAppData%\Programs\Python\%%d\python.exe" set "PYTHON=%LocalAppData%\Programs\Python\%%d\python.exe"
    )
)

if not defined PYTHON (
    echo [X] No encuentro Python en este equipo.
    echo.
    echo     Instala Python 3.10 o superior desde https://www.python.org/downloads/
    echo     y MARCA la casilla "Add python.exe to PATH" durante la instalacion.
    echo.
    echo     Ojo: el Python de la Microsoft Store no vale, abre la tienda en
    echo     lugar de ejecutarse. Descarga el de python.org.
    echo.
    echo     Si ya lo tienes y aqui no aparece, se puede lanzar a mano:
    echo         python instalar.py
    echo.
    pause
    exit /b 1
)

echo     Python en: %PYTHON%

rem --- Version suficiente -----------------------------------------------------
%PYTHON% -c "import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)" >nul 2>&1
if errorlevel 1 (
    echo [X] Ese Python es demasiado antiguo; necesito 3.10 o superior.
    %PYTHON% --version
    pause
    exit /b 1
)

rem --- Delegar ---------------------------------------------------------------
rem Se pasa todo lo que venga detras para no perder opciones como --rapido.
%PYTHON% "%~dp0instalar.py" %*
set "CODIGO=%ERRORLEVEL%"

echo.
if "%CODIGO%"=="0" (
    echo   Instalacion terminada.
) else (
    echo   La instalacion ha terminado con avisos. Mira lo de arriba.
)
pause
exit /b %CODIGO%