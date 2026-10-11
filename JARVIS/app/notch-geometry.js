function clamp(value, minimum, maximum) {
  return Math.max(minimum, Math.min(maximum, value));
}

function roundedRectRows(width, height, radius, offsetX = 0, offsetY = 0) {
  const w = Math.max(0, Math.floor(width));
  const h = Math.max(0, Math.floor(height));
  const r = clamp(Number(radius) || 0, 0, Math.min(w, h) / 2);
  const rows = [];

  for (let y = 0; y < h; y++) {
    let inset = 0;
    if (r > 0 && y < r) {
      inset = Math.max(0, Math.ceil(r - Math.sqrt(Math.max(0, r * r - (r - y - 0.5) ** 2)) - 0.5));
    } else if (r > 0 && y >= h - r) {
      const dy = y - (h - r);
      inset = Math.max(0, Math.ceil(r - Math.sqrt(Math.max(0, r * r - (dy + 0.5) ** 2)) - 0.5));
    }
    const rowWidth = Math.max(0, w - inset * 2);
    if (rowWidth) rows.push({ x: offsetX + inset, y: offsetY + y, width: rowWidth, height: 1 });
  }
  return rows;
}

function isPointInsideRoundedRect(point, x, y, width, height, radius) {
  const localX = point.x - x;
  const localY = point.y - y;
  if (localX < 0 || localY < 0 || localX >= width || localY >= height) return false;
  const r = clamp(Number(radius) || 0, 0, Math.min(width, height) / 2);
  if (!r) return true;
  const centerX = localX < r ? r : localX >= width - r ? width - r : localX;
  const centerY = localY < r ? r : localY >= height - r ? height - r : localY;
  return (localX - centerX) ** 2 + (localY - centerY) ** 2 <= r ** 2;
}

// Geometria de Coucou (windows/src/core/layout.ts): compacto 288x32 con
  // radio 14, abierto 720 de ancho con radio 22.
  function notchHitTest(point, bounds, width = 288, height = 32, radius = 14) {
  const left = bounds.x + Math.round((bounds.width - width) / 2);
  return isPointInsideRoundedRect(point, left, bounds.y, width, height, radius);
}

module.exports = { isPointInsideRoundedRect, notchHitTest, roundedRectRows };
