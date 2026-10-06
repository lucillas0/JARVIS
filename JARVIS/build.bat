@echo off
REM JARVIS - Empaquetado como instalador .exe (sección 11, bloque 11)
setlocal
cd /d "%~dp0.."

call scripts\setup_brain.bat
if errorlevel 1 goto :error

cd app
if not exist node_modules (
  echo [JARVIS] npm install primero...
  call npm install
)
if errorlevel 1 goto :error

echo [JARVIS] Generando instalador de Windows con electron-builder...
call npx electron-builder --win
if errorlevel 1 goto :error

echo.
echo [JARVIS] Instalador generado en ..\dist-installer\  ------------------
goto :eof

:error
echo [JARVIS] Fallo al empaquetar.
exit /b 1