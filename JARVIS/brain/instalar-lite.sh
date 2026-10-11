#!/usr/bin/env bash
# Instalacion de dependencias de JARVIS en modo LITE (Linux / servidor).
# Instala solo el cerebro. El modo FULL (ventana de escritorio) es Windows.
set -euo pipefail
cd "$(dirname "$0")"

echo
echo "  J.A.R.V.I.S.  ·  instalacion LITE"
echo "  ------------------------------------"
echo

command -v python3 >/dev/null 2>&1 || {
  echo "[X] No encuentro python3. Instale Python 3.10 o superior."; exit 1; }
echo "[1/3] Python: $(python3 --version)"

if [ ! -d venv ]; then
  echo "[2/3] Creando el entorno propio..."
  python3 -m venv venv
else
  echo "[2/3] El entorno virtual ya existe."
fi

echo "[3/3] Instalando dependencias (tarda unos minutos la primera vez)..."
./venv/bin/python -m pip install --upgrade pip --quiet
./venv/bin/python -m pip install -r requirements.txt

echo
echo "  Listo. Para arrancar JARVIS:"
echo
echo "    ./venv/bin/python -m uvicorn main:app --host 127.0.0.1 --port 8000"
echo
echo "  Y luego, en el navegador:"
echo
echo "    http://127.0.0.1:8000/modo     (elegir LITE o FULL)"
echo "    http://127.0.0.1:8000/setup    (poner las claves)"
echo "    http://127.0.0.1:8000/board    (la pizarra)"
echo
