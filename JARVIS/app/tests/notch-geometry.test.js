const assert = require('node:assert/strict');
const test = require('node:test');

const { isPointInsideRoundedRect, notchHitTest, roundedRectRows } = require('../notch-geometry');

// Los numeros vienen de Coucou, windows/src/core/layout.ts:
// COMPACT_W 288, NOTCH_H 32, ROUNDED_CORNER 14, EXPANDED_W 640, PANEL_W 720.
test('compact notch hit area is 288×32 with a 14px corner, not a capsule', () => {
  const bounds = { x: 390, y: 0, width: 288, height: 32 };
  assert.equal(notchHitTest({ x: 534, y: 16 }, bounds), true);
  assert.equal(notchHitTest({ x: 390, y: 0 }, bounds), false);
  assert.equal(notchHitTest({ x: 534, y: 32 }, bounds), false);
  assert.equal(notchHitTest({ x: 100, y: 16 }, bounds), false);
});

test('rounded mask insets the first and last rows of the 288×32 compact island', () => {
  const rows = roundedRectRows(288, 32, 14, 0, 0);
  assert.equal(rows.length, 32);
  assert.ok(rows[0].x > 0, 'la primera fila entra por el redondeo');
  const fullWidthRow = rows.find((row) => row.width === 288);
  assert.ok(fullWidthRow, 'hay filas a ancho completo');
  assert.equal(fullWidthRow.x, 0);
  assert.equal(rows[31].x, rows[0].x, 'la ultima fila es simetrica a la primera');
});

test('rounded rectangle point test honors offsets and transparent corners', () => {
  assert.equal(isPointInsideRoundedRect({ x: 5, y: 5 }, 0, 0, 100, 40, 20), false);
  assert.equal(isPointInsideRoundedRect({ x: 70, y: 30 }, 10, 10, 100, 40, 20), true);
});