"""La puerta de entrada: una pagina para entrar con el token.

Antes, las paginas que hacen falta para usar JARVIS (`/config`, `/setup`,
`/live`, `/board`) se servian a cualquiera que llegara al puerto, yupuertas
en su HTML el token de sesion. Con el puerto publicado en la LAN eso era
suficiente para que cualquier equipo de la red se llevara TODAS las claves de
API en claro (`POST /api/config/exportar-claves`).

Ahora hay una puerta: se pide el token una vez, se guarda en una cookie
`HttpOnly`, y a partir de ahi las paginas ya no llevan el token dentro: lo
lleva la cookie, que el JavaScript no puede leer.

El token se le muestra al usuario en la consola al arrancar el servidor, que
es donde lo puede ver sin que ningun sitio web lo publique.
"""
from __future__ import annotations

PAGINA = """<!DOCTYPE html>
<html lang="es">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>JARVIS · entrar</title>
<style>
  :root{color-scheme:dark}
  *{box-sizing:border-box}
  body{margin:0;min-height:100vh;display:grid;place-items:center;
    background:#0b0f17;color:#e2e8f0;
    font-family:system-ui,-apple-system,"Segoe UI",sans-serif;line-height:1.5}
  .caja{width:min(420px,92vw);background:#111827;border:1px solid #1f2a3c;
    border-radius:14px;padding:28px 26px;box-shadow:0 24px 60px rgba(0,0,0,.45)}
  h1{margin:0 0 4px;font-size:20px;letter-spacing:.02em}
  .sub{margin:0 0 20px;color:#94a3b8;font-size:13.5px}
  label{display:block;font-size:12.5px;color:#94a3b8;margin:0 0 6px}
  input{width:100%;padding:11px 13px;border-radius:9px;border:1px solid #263449;
    background:#0b0f17;color:#e2e8f0;font:inherit;font-size:14px}
  input:focus{outline:none;border-color:#38bdf8;box-shadow:0 0 0 3px rgba(56,189,248,.15)}
  button{width:100%;margin-top:14px;padding:12px;border:0;border-radius:9px;
    background:#38bdf8;color:#04121e;font:inherit;font-weight:700;cursor:pointer}
  button:hover{background:#7dd3fc}
  button:disabled{opacity:.6;cursor:progress}
  .nota{margin:18px 0 0;padding:12px 14px;border-left:2px solid #38bdf8;
    background:#0f172a;border-radius:0 8px 8px 0;color:#94a3b8;font-size:12.5px}
  .nota b{color:#cbd5e1}
  .error{margin:12px 0 0;color:#f87171;font-size:13px;min-height:1em}
  code{font-family:ui-monospace,Menlo,Consolas,monospace;color:#cbd5e1}
</style>
</head>
<body>
  <main class="caja">
    <h1>JARVIS</h1>
    <p class="sub">Entra con el token de sesión para usar el centro de mando.</p>
    <form id="f" autocomplete="off">
      <label for="t">Token de sesión</label>
      <input id="t" name="token" type="password" spellcheck="false"
             autocapitalize="off" required>
      <button type="submit" id="b">Entrar</button>
    </form>
    <p class="error" id="e" role="alert"></p>
    <p class="nota">
      El token está en <code>jarvis.token</code>, dentro de la carpeta de datos
      de JARVIS, y se muestra en la consola al arrancar el servidor.
      Si no lo tienes, búscalo ahí: <b>es la contraseña de tu asistente</b>.
    </p>
  </main>
<script>
  const f = document.getElementById('f');
  const e = document.getElementById('e');
  const b = document.getElementById('b');
  f.addEventListener('submit', async (ev) => {
    ev.preventDefault();
    e.textContent = '';
    b.disabled = true;
    b.textContent = 'Comprobando…';
    try {
      const r = await fetch('/entrar', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ token: document.getElementById('t').value })
      });
      const d = await r.json();
      if (!r.ok || !d.ok) throw new Error(d.error || 'Token incorrecto.');
      location.href = d.destino || '/live';
    } catch (err) {
      e.textContent = err.message;
      b.disabled = false;
      b.textContent = 'Entrar';
    }
  });
</script>
</body>
</html>
"""
