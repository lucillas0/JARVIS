"""Genera assets del proyecto: icono del tray y del instalador.

Se ejecuta desde setup_brain.bat (cwd = brain/).
"""
import struct
import zlib
from pathlib import Path

BRAIN_DIR = Path(__file__).resolve().parent.parent
ROOT = BRAIN_DIR.parent
ASSETS = ROOT / "assets"
APP_ASSETS = ROOT / "app" / "assets"


def _circle_png(size: int = 256) -> bytes:
    """Dibuja un orbe azul/cian como PNG (sin dependencias)."""
    r = size // 2
    cx, cy = r, r
    rows = b""
    for y in range(size):
        row = bytearray([0])  # filtro
        for x in range(size):
            dx, dy = x - cx, y - cy
            dist = (dx * dx + dy * dy) ** 0.5
            if dist <= r * 0.86:
                t = dist / (r * 0.86)
                c1 = (10, 60, 110)
                c2 = (60, 220, 255)
                grad = 0.35 + 0.65 * (1 - t)
                color = tuple(int((c1[i] * (1 - grad)) + (c2[i] * grad)) for i in range(3))
            elif dist <= r:
                edge = int(90 * (1 - (dist - r * 0.86) / (r * 0.14)))
                color = (max(0, edge - 40), edge, 255)
            else:
                color = (0, 0, 0)
            row += bytes(color[:3])
        rows += bytes(row)
    raw = zlib.compress(rows)
    ihdr = struct.pack(">IIBBBBB", size, size, 8, 2, 0, 0, 0)

    def chunk(tag, data):
        c = struct.pack(">I", len(data)) + tag + data
        c += struct.pack(">I", zlib.crc32(tag + data) & 0xffffffff)
        return c

    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", raw) + chunk(b"IEND", b"")


def _pngs_to_ico(png_by_size: dict[int, bytes]) -> bytes:
    """ICO con imágenes PNG embebidas (Windows Vista+)."""
    sizes = sorted(png_by_size)
    header = struct.pack("<HHH", 0, 1, len(sizes))
    entries = b""
    offset = 6 + 16 * len(sizes)
    for s in sizes:
        b = 0 if s >= 256 else s
        data = png_by_size[s]
        entries += struct.pack("<BBBBHHII", b, b, 0, 0, 1, 32, len(data), offset)
        offset += len(data)
    body = b"".join(png_by_size[s] for s in sizes)
    return header + entries + body


def main() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    APP_ASSETS.mkdir(parents=True, exist_ok=True)

    png = _circle_png(256)
    (ASSETS / "tray-icon.png").write_bytes(png)
    (APP_ASSETS / "icon.png").write_bytes(png)
    try:
        sizes = {s: _circle_png(s) for s in (16, 32, 48, 64, 128, 256)}
        (APP_ASSETS / "icon.ico").write_bytes(_pngs_to_ico(sizes))
    except Exception:
        pass

    # Audio del Modo Despertar
    theme = ASSETS / "audio" / "wake-up-theme.mp3"
    if not theme.exists():
        for c in [ROOT.parent / "codigo-mvp" / "assets" / "audio" / "wake-up-theme.mp3",
                  ROOT / "assets" / "wake-up-theme.mp3"]:
            if c.exists():
                theme.write_bytes(c.read_bytes())
                break

    print("assets generados:", ASSETS, APP_ASSETS)


if __name__ == "__main__":
    main()