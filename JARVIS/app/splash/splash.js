bar.style.width = '100%';
      statusEl.textContent = data.detail || 'Listo';
      tagline.textContent = data.title || 'Cerebro listo';
      setBusy();
      return;
    }

    setBusy();
    bar.style.width = pct + '%';
    pctEl.textContent = pct + '%';
    statusEl.textContent = data.detail || '';
    if (data.title) tagline.textContent = data.title;
  });

  $('retryBtn').addEventListener('click', () => {
    errorBox.hidden = true;
    if (window.jarvisSetupAPI.retry) window.jarvisSetupAPI.retry();
  });
  $('closeBtn').addEventListener('click', () => {
    if (window.jarvisSetupAPI.quit) window.jarvisSetupAPI.quit();
  });
})();