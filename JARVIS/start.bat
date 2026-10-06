@echo off
REM JARVIS - Arranque completo (app + brain)
setlocal
cd /d "%~dp0.."

echo ============================================
echo   JARVIS - Asistente personal de escritorio
echo ============================================

REM 1) Brain y dependencias
call scripts\setup_brain.bat
if errorlevel 1 goto :error

REM 2) Instalar dependencias de la app si falta node_modules
cd app
if not exist node_modules (
  echo [JARVIS] Instalando dependencias de la app (descarga Electron ~100MB primero)...
  call npm install
)
if errorlevel 1 goto :error

REM 3) Asegurar three.js en vendor
if not exist ui\vendor\three.min.js (
  if exist node_modules\three\build\three.min.js (
    if not exist ui\vendor mkdir ui\vendor
    copy /Y node_modules\three\build\three.min.js ui\vendor\three.min.js >nul
  )
)

echo [JARVIS] Lanzando interfaz Electron...
call npm start
goto :eof

:error
echo [JARVIS] Algo fallo durante la puesta en marcha. Revisa el mensaje anterior.
exit /b 1