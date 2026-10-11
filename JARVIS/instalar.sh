#!/usr/bin/env bash
# =============================================================================
#  JARVIS - instalador para Linux y macOS (escritorio y modo LITE)
# =============================================================================
#  El servidor en Docker tiene su propio instalador: instalar-docker.sh.
# =============================================================================
set -euo pipefail
cd "$(dirname "$0")"

echo
echo "  J.A.R.V.I.S.  -  instalacion"
echo "  ------------------------------"
echo

PYTHON=""

# 1) python3 de la version moderna. Se rechazan los 2.x y los 3.8 o anteriores.
for candidato in python3.13 python3.12 python3.11 python3.10 python3 python; do
    if command -v "$candidato" >/dev/null 2>&1; then
        if "$candidato" -c 'import sys; sys.exit(0 if sys.version_info[:2] >= (3,10) else 1)' 2>/dev/null; then
            PYTHON="$(command -v "$candidato")"
            break
        fi
    fi
done

if [ -z "$PYTHON" ]; then
    echo "[X] No encuentro Python 3.10 o superior."
    echo
    echo "    Debian / Ubuntu :  sudo apt install python3-venv python3-pip"
    echo "    Fedora          :  sudo dnf install python3 python3-pip"
    echo "    Arch            :  sudo pacman -S python python-pip"
    echo "    macOS (Homebrew):  brew install python@3.12"
    echo
    echo "    En Debian y Ubuntu hace falta python3-venv: sin el modulo venv"
    echo "    la creacion del entorno falla y no hay manera de appraisalo."
    echo
    exit 1
fi

echo "    Python en: $PYTHON"
"$PYTHON" --version

# Se pasa todo lo que venga detras para no perder opciones como --rapido.
exec "$PYTHON" instalar.py "$@"