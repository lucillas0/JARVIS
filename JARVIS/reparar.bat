@echo off
rem =============================================================================
rem  JARVIS - deja el escritorio al dia. Doble clic y ya esta.
rem =============================================================================
rem  Esto es solo una abreviatura de:
rem      instalar.bat --reparar
rem
rem  Que hace, y por que existe:
rem
rem  · Mira que version hay puesta y la compara con la del codigo.
rem  · Si no ha cambiado nada, lo dice y no toca nada (no hay esperas).
rem  · Si ha cambiado, reinstala lo que falte por grupos: un paquete pesado
rem    que falle ya no tira el resto.
rem  · Si el entorno esta roto, lo rehago. El caso tipico es haber actualizado
rem    Python: el venv sigue con sus carpetas y su python.exe, pero apunta al
rem    interprete viejo, que ya no existe, y todo falla luego con un error que
rem    no explica nada.
rem
rem  Todo esto lo hace tambien la propia app al arrancar; esto es para cuando
rem  quieres forzarlo sin abrir el menu.
rem =============================================================================
setlocal EnableExtensions
cd /d "%~dp0"

if exist "%~dp0*" (
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Unblock-File -Path '%~dp0*' -ErrorAction SilentlyContinue" >nul 2>&1
)

echo.
echo   J.A.R.V.I.S.  -  dejar al dia
echo   -----------------------------
echo.
echo   Esto solo toca las dependencias y el entorno de Python.
echo   No toca tus claves, ni la base de datos, ni el historial.
echo.

set "PYTHON="
for /f "delims=" %%p in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do (
    if exist "%%p" if not defined PYTHON set "PYTHON=%%p"
)
if not defined PYTHON (
    for /f "delims=" %%p in ('where python 2^>nul') do (
        if not defined PYTHON if exist "%%p" set "PYTHON=%%p"
    )
)
if defined PYTHON (
    echo %%PYTHON%% | find /i "WindowsApps" >nul && set "PYTHON="
)
if not defined PYTHON (
    echo [X] No encuentro Python. Instala Python 3.10+ desde python.org
    echo     marcando "Add python.exe to PATH", y ejecuta esto otra vez.
    echo.
    pause
    exit /b 1
)

%PYTHON% "%~dp0instalar.py" --reparar --sin-claves
set "CODIGO=%ERRORLEVEL%"

echo.
if "%CODIGO%"=="0" (
    echo   Todo al dia.
) else (
    echo   No he podido dejarlo al dia del todo. Mira lo de arriba.
)
echo.
pause
exit /b %CODIGO%