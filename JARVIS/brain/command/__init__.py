"""Centro de mando de JARVIS: la capa que convierte el Live en control real.

  command/centers.py    salud, recursos y matriz de control
  command/notify.py     notificaciones e incidentes desde el bus real
  command/services.py   conocimiento, automatizaciones, memoria, auditoría
  command/runner.py     test center y checkpoints de Git
  command/briefing.py   modo ausente, briefing y estado de los pasos
  command/store.py      persistencia (notificaciones/incidentes/decisiones)
  command/routes.py     API /api/cmd

Regla de este paquete: si una acción no tiene un backend real detrás, no se
ofrece. La matriz de control lo declara y la interfaz lo respeta.
"""

from . import activity, briefing, centers, notify, runner, services, store  # noqa: F401

__all__ = ["activity", "briefing", "centers", "notify", "runner", "services", "store"]