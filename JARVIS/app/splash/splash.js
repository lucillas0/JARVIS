// Pantalla de preparacion.
//
// Este fichero estaba truncado a mitad de una funcion y por eso la barra se
// quedaba muerta: no habia ni suscripcion al progreso. Ahora se escucha de
// verdad, se pinta lo que llega y se translates cada porcentaje a un paso
// concreto para que la espera se entienda de un vistazo.

(function () {
  const $ = (id) => document.getElementById(id);

  const bar = $('bar');
  const pctEl = $('pct');
  const statusEl = $('status');
  const tagline = $('tagline');
  const errorBox = $('errorBox');
  const errorTitle = $('errorTitle');
  const errorDetail = $('errorDetail');
  const stepsEl = $('steps');
  const pctLine = document.querySelector('.pct-line');

  // Los pasos que el proceso principal va contando. El porcentaje marca en
  // cual estamos: hasta el siguiente corte, ese paso es el que se ilumina.
  const PASOS = [
    { hasta: 4, texto: 'Buscando Python' },
    { hasta: 10, texto: 'Creando el entorno' },
    { hasta: 90, texto: 'Instalando dependencias' },
    { hasta: 94, texto: 'Generando recursos' },
    { hasta: 100, texto: 'Arrancando JARVIS' },
  ];

  let pintados = -1;

  function pintaPasos(pct) {
    if (pct === pintados) return;
    pintados = pct;
    stepsEl.innerHTML = '';
    PASOS.forEach((paso, i) => {
      const siguiente = i + 1 < PASOS.length ? PASOS[i + 1].hasta : 101;
      const li = document.createElement('li');
      let marca = '·';
      let clase = '';
      if (pct >= siguiente) { marca = '✓'; clase = 'hecho'; }
      else if (pct >= paso.hasta) { marca = '›'; clase = 'actual'; }
      if (pct >= 100) { marca = '✓'; clase = 'hecho'; }
      li.className = clase;
      const m = document.createElement('span');
      m.className = 'marca';
      m.textContent = marca;
      const t = document.createElement('span');
      t.textContent = paso.texto;
      li.appendChild(m);
      li.appendChild(t);
      stepsEl.appendChild(li);
    });
  }

  function avisa(frase) {
    statusEl.textContent = frase;
  }

  function quitaError() {
    errorBox.hidden = true;
  }

  function ponError(titulo, detalle) {
    errorTitle.textContent = titulo || 'Algo no ha ido bien';
    errorDetail.textContent = detalle || '';
    errorBox.hidden = false;
    // El paso en el que se ha quedado parado es el que hay que marcar.
    const items = stepsEl.querySelectorAll('li');
    for (const li of items) {
      if (li.classList.contains('actual')) {
        li.classList.remove('actual');
        li.classList.add('fallo');
        li.querySelector('.marca').textContent = '!';
        break;
      }
    }
  }

  function pinta(pct) {
    const n = Math.max(0, Math.min(100, Math.round(pct || 0)));
    bar.style.width = n + '%';
    pctEl.textContent = n + '%';
    pintaPasos(n);
  }

  // Mientras llega algo, la barra se queda en movimiento despacio para que se
  // vea que la cosa vive, aunque no haya noticias.
  let vivo = setInterval(() => {
    const actual = parseFloat(bar.style.width) || 0;
    if (actual > 0 && actual < 88) pinta(Math.min(88, actual + 0.4));
  }, 400);

  if (!window.jarvisSetupAPI || !window.jarvisSetupAPI.onProgress) {
    clearInterval(vivo);
    ponError('No puedo hablar con el instalador',
      'La ventana de preparacion no recibio ningun aviso. Prueba a abrir JARVIS de nuevo.');
  } else {
    window.jarvisSetupAPI.onProgress((data) => {
      if (!data) return;
      quitaError();
      const pct = data.percent || 0;

      if (data.phase === 'error') {
        clearInterval(vivo);
        pinta(pct);
        ponError(data.title, data.detail);
        return;
      }

      if (data.phase === 'done') {
        clearInterval(vivo);
        pinta(100);
        bar.classList.add('terminada');
        tagline.textContent = data.title || 'Cerebro listo';
        avisa(data.detail || 'Listo');
        return;
      }

      pinta(pct);
      if (data.title) tagline.textContent = data.title;
      avisa(data.detail || 'Preparando…');
    });
  }

  $('retryBtn').addEventListener('click', () => {
    quitaError();
    clearInterval(vivo);
    pinta(0);
    pintaPasos(0);
    if (window.jarvisSetupAPI && window.jarvisSetupAPI.retry) window.jarvisSetupAPI.retry();
  });

  $('closeBtn').addEventListener('click', () => {
    if (window.jarvisSetupAPI && window.jarvisSetupAPI.quit) window.jarvisSetupAPI.quit();
  });
})();