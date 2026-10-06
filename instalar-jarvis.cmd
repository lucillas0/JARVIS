@echo off
rem ---------------------------------------------------------------------
rem  Instala y arranca JARVIS de una vez, sin pantallas.
rem  Si avisa de que hay que cerrar OpenCode: cierralo entero (ventana e
rem  icono de la bandeja) y vuelve a hacer doble clic aqui.
rem ---------------------------------------------------------------------
setlocal
set INSTALADOR=C:\jarvis-handoff\JARVIS\dist-installer\JARVIS Setup 4.0.0.exe
set APP=C:\Users\Lucas\AppData\Local\Programs\jarvis\JARVIS.exe
set ASAR=%LOCALAPPDATA%\Programs\jarvis\resources\app.asar

echo [1/4] Cerrando JARVIS si esta abierto...
taskkill /F /IM JARVIS.exe >nul 2>&1
ping -n 4 127.0.0.1 >nul

echo [2/4] Comprobando que nada tiene cogido el programa...
powershell -NoProfile -Command "$f='%ASAR%'; if (Test-Path $f) { try { $s=[IO.File]::Open($f,'Open','ReadWrite','None'); $s.Close(); 'LIBRE' } catch { 'BLOQUEADO' } } else { 'NOEXISTE' }" > "%TEMP%\jarvis_estado.txt" 2>nul
set /p ESTADO=<"%TEMP%\jarvis_estado.txt"
echo        estado del programa: %ESTADO%

if /i "%ESTADO%"=="BLOQUEADO" (
  echo.
  echo  ============================================================================
  echo   No se puede instalar: hay un programa usando
  echo     %ASAR%
  echo.
  echo   Cierra OpenCode ENTERO (ventana y el icono de la bandeja) y
  echo   vuelve a hacer doble clic en este archivo.
  echo  ============================================================================
  echo.
  pause
  exit /b 1
)

echo [3/4] Instalando. Puede tardar 2-3 minutos, no lo cierre...
start "" /wait "%INSTALADOR%" /S
ping -n 12 127.0.0.1 >nul

echo [4/4] Arrancando JARVIS...
start "" "%APP%"

echo.
echo Listo. JARVIS se esta arrancando; tarda un minuto en despertar.
ping -n 8 127.0.0.1 >nul
exit /b 0
