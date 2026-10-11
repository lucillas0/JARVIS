// Genera los iconos que electron-builder necesita para Linux y macOS a partir
// del icono.png que ya hay. Sin esto, --linux falla por linux-icons y --mac
// falla por icon.icns.
//
// El .icns se arma a mano porque Pillow no lo sabe escribir. Un .icns es una
// cabecera 'icns' con el tamaño total, seguida de un 'ic08' (PNG de 256px) y un
// 'ic09' (512px). Los lectores de macCatalyst/electron aceptan esas entradas.
//
//   node generar-iconos.mjs [rutaBase]

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { createRequire } from 'node:module';

const require = createRequire(import.meta.url);
const aqui = path.dirname(fileURLToPath(import.meta.url));
const base = process.argv[2] || path.join(aqui, '..');

const origen = path.join(base, 'assets', 'icon.png');
if (!fs.existsSync(origen)) {
  console.error('  No encuentro ' + origen);
  process.exit(1);
}

// Pillow viene en el entorno del cerebro, no en node_modules de la app.
const venvPy = process.env.JARVIS_PY || path.join(
  process.env.APPDATA || '', 'JARVIS', 'brain-data', 'venv', 'Scripts', 'python.exe');
if (!fs.existsSync(venvPy)) {
  console.error('  No encuentro el Python del cerebro: ' + venvPy);
  process.exit(1);
}

const guion = `
import os, sys
from PIL import Image

base = sys.argv[1]
origen = os.path.join(base, 'assets', 'icon.png')
img = Image.open(origen).convert('RGBA')

# --- Linux: una carpeta con los tamanos que electron-builder busca -----------
linux = os.path.join(base, 'assets', 'linux-icons')
os.makedirs(linux, exist_ok=True)
for lado in (16, 24, 32, 48, 64, 128, 256, 512, 1024):
    img.resize((lado, lado), Image.LANCZOS).save(os.path.join(linux, 'icon_%dx%d.png' % (lado, lado)))

# --- Windows ---------------------------------------------------------------
img.save(os.path.join(base, 'assets', 'icon.ico'),
         sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])

# --- macOS: .icns ----------------------------------------------------------
def bloque(magic, png_bytes):
    # cabecera de tipo (4) + tamaño (4) + los propios bytes
    return magic + len(png_bytes).to_bytes(4, 'big') + png_bytes

import io
entradas = []
for magic, lado in ((b'ic08', 256), (b'ic09', 512)):
    buf = io.BytesIO()
    img.resize((lado, lado), Image.LANCZOS).save(buf, format='PNG')
    entradas.append(bloque(magic, buf.getvalue()))
cuerpo = b''.join(entradas)
icns = b'icns' + (8 + len(cuerpo)).to_bytes(4, 'big') + cuerpo
with open(os.path.join(base, 'assets', 'icon.icns'), 'wb') as f:
    f.write(icns)

print('OK linux:', len(os.listdir(linux)), 'iconos | icns:', len(icns), 'bytes')
`;

const tmp = path.join(base, '.generar-iconos.py');
fs.writeFileSync(tmp, guion, 'utf8');
try {
  const { execFileSync } = require('node:child_process');
  const salida = execFileSync(venvPy, [tmp, base], { encoding: 'utf8' });
  console.log('  ' + salida.trim());
} finally {
  fs.rmSync(tmp, { force: true });
}