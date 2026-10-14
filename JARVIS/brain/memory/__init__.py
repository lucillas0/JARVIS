"""Memoria de JARVIS.

Hay dos, y conviene no confundirlas:

  · La memoria DE CONVERSACION (este paquete): SQLite con los mensajes, Qdrant
    para lo semántico, el perfil, los proyectos. Es rapida, local, y siempre
    esta. Sin ella JARVIS no arranca.

  · La memoria PERSISTENTE de largo plazo: Mem0 OSS autoalojado, con
    PostgreSQL/pgvector. Aprende hechos sobre el señor, sobrevive a los
    reinicios y a que se borre el historial, y es lo que se consulta antes de
    contestar.

La segunda es opcional. Si MEMORY_ENABLED=0, o si el servicio no esta, este
paquete sigue funcionando exactamente igual: la conversacion no se rompe.
"""
from memory.manager import (  # noqa: F401
    CATEGORIAS,
    MemoryManager,
    para_el_prompt,
    recordar,
)

# OJO con lo que NO se reexporta: la funcion manager(). Este paquete tiene un
# submódulo llamado memory.manager, y poner aqui una variable con el mismo
# nombre la TAPA. Después de este import, "import memory.manager" daba un
# 'function' object has no attribute 'manager', que es un error que no
# aparece hasta que alguien lo llama. Para el manager se importa del
# submódulo directamente: "from memory.manager import manager".

__all__ = ["CATEGORIAS", "MemoryManager", "para_el_prompt", "recordar"]
