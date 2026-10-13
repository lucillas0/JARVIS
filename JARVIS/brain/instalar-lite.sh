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
echo "[1/4] Python: $(python3 --version)"

if [ ! -d venv ]; then
  echo "[2/4] Creando el entorno propio..."
  python3 -m venv venv
else
  echo "[2/4] El entorno virtual ya existe."
fi

# requirements-server.txt y NO requirements.txt: este dice ser LITE y lo que
# trae el otro son torch, spacy, stanza, piper y openwakeword, que son cientos
# de megabytes y son justo lo que el modo LITE deja fuera.
echo "[3/4] Instalando dependencias del servidor (tarda unos minutos la primera vez)..."
./venv/bin/python -m pip install --upgrade pip --quiet
./venv/bin/python -m pip install -r requirements-server.txt

# Aceleradores opcionales: si alguno no tiene rueda para este Python, se pierde
# y no pasa nada. Por eso van uno a uno y no en el grupo de arriba.
echo "[4/4] Aceleradores opcionales (si faltan, no pasa nada)..."
for p in httptools uvloop websockets watchfiles; do
  ./venv/bin/python -m pip install "$p" --quiet --disable-pip-version-check || \
    echo "    $p: sin rueda para este Python. Da igual."
done

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
