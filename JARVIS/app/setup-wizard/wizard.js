(() => {
  const $ = (id) => document.getElementById(id);
  const fields = {
    GROQ_API_KEY: 'groq',
    GEMINI_API_KEY: 'gemini',
    OPENROUTER_API_KEY: 'openrouter',
  };

  function setMsg(text, kind = '') {
    const msg = $('msg');
    msg.textContent = text;
    msg.className = `msg ${kind}`.trim();
  }

  async function save() {
    const keys = Object.fromEntries(Object.entries(fields)
      .map(([name, id]) => [name, $(id).value.trim()])
      .filter(([, value]) => value));
    if (!Object.keys(keys).length) {
      setMsg('Añade una clave o elige «Continuar sin claves».', 'err');
      return;
    }
    $('saveBtn').disabled = true;
    setMsg('Guardando claves…');
    try {
      const result = await window.jarvisAPI.saveKeys(keys);
      if (!result || !result.ok) throw new Error('No se guardaron las claves.');
      setMsg('Claves guardadas. Iniciando JARVIS…', 'ok');
      setTimeout(() => window.close(), 600);
    } catch (error) {
      setMsg(`Error al guardar: ${error.message}`, 'err');
      $('saveBtn').disabled = false;
    }
  }

  document.querySelectorAll('a[href]').forEach((link) => {
    link.addEventListener('click', (event) => {
      event.preventDefault();
      window.jarvisAPI.openExternal(link.href);
    });
  });

  $('saveBtn').addEventListener('click', save);
  $('skipBtn').addEventListener('click', async () => {
    $('skipBtn').disabled = true;
    try {
      const result = await window.jarvisAPI.skipKeySetup();
      if (!result || !result.ok) throw new Error((result && result.error) || 'No se pudo guardar la elección.');
      window.close();
    } catch (error) {
      setMsg(`Error al continuar: ${error.message}`, 'err');
      $('skipBtn').disabled = false;
    }
  });
})();
