=> window.close(), 1400);
      } else {
        setMsg('No he podido guardar el archivo. Verifica permisos.', 'err');
      }
    } catch (e) {
      setMsg('Error al guardar: ' + e.message, 'err');
    }
  }

  $('saveBtn').addEventListener('click', save);
  $('skipBtn').addEventListener('click', async () => {
    // Guarda claves vacías para marcar wizard como done
    await window.jarvisAPI.saveKeys({});
    window.close();
  });
})();