
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
