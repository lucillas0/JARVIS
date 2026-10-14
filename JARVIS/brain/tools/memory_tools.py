# -*- coding: utf-8 -*-
"""Herramientas de la memoria persistente (Mem0 OSS).

Cinco herramientas, y la sexta ya la hace el sistema sin que nadie las pida:
la busqueda automatica antes de cada respuesta. Estas son para cuando el
modelo quiere mirar o tocar la memoria por su cuenta.

Ojo con lo que no hay aqui: no existe ninguna herramienta para "volver a
hablar de X". Eso no lo hace el modelo, lo hace el manager cuando le pasa
el bloque <relevant_memory> con lo que ha encontrado. Duplicarlo aqui
seria tener dos caminos distintos al mismo sitio.
"""
import logging

log = logging.getLogger("tools.memory")

MEMORY_TOOLS = [
    {
        "name": "memory_search",
        "risk_level": "low",
        "requires_confirmation": False,
        "parameters": {"consulta": "str, sobre qué busca", "limite": "int opcional"},
        "description": "Busca en lo que JARVIS recuerda del señor. Útil si "
                       "pregunta de algo anterior, o si quiere saber si ya "
                       "sabe algo antes de afirmar que no.",
        "label": "Buscando en la memoria",
    },
    {
        "name": "memory_add",
        "risk_level": "low",
        "requires_confirmation": False,
        "parameters": {"texto": "str, lo que hay que recordar",
                       "categoria": "str opcional: preference, project, "
                                    "configuration, device, personal, technical"},
        "description": "Guarda un recuerdo de forma permanente. Se usa solo "
                       "cuando el señor dice «recuerda» o cuando hay un dato "
                       "claro que va a importar dentro de un mes. NO se usa "
                       "para cada respuesta: la memoria se llena de ruido. "
                       "Nunca se guardan claves ni contrasenas.",
        "label": "Guardando en la memoria",
    },
    {
        "name": "memory_update",
        "risk_level": "low",
        "requires_confirmation": False,
        "parameters": {"id": "str, el id del recuerdo", "texto": "str, el nuevo texto"},
        "description": "Cambia un recuerdo que ya está guardado. Úsalo cuando "
                       "el señor corrije algo que tenía mal guardado, en vez "
                       "de añadir el nuevo y dejar los dos.",
        "label": "Actualizando la memoria",
    },
    {
        "name": "memory_delete",
        "risk_level": "medium",
        "requires_confirmation": True,
        "parameters": {"id": "str, el id del recuerdo"},
        "description": "Borra un recuerdo. Pide confirmación siempre: no hay "
                       "tira atrás.",
        "label": "Borrando de la memoria",
        "confirm_text": "Voy a borrar el recuerdo {id} de forma permanente. ¿Confirma?",
    },
    {
        "name": "memory_list",
        "risk_level": "low",
        "requires_confirmation": False,
        "parameters": {"limite": "int opcional"},
        "description": "Todo lo que JARVIS recuerda ahora mismo, con su "
                       "categoría y su fecha. Útil si el señor pregunta «qué "
                       "recuerdas de mí» o quiere revisar lo guardado.",
        "label": "Listando la memoria",
    },
]

# Los handlers se montan aparte porque necesitan el manager, que se construye
# en el arranque. Se registran igualmente: si el manager no estuviera, el
# handler lo diria al señor en vez de reventar la llamada.
def _m():
    from memory.manager import manager
    return manager()


def memory_search_handler(params: dict) -> dict:
    try:
        limite = max(1, min(int(params.get("limite", 5) or 5), 20))
    except (TypeError, ValueError):
        limite = 5
    consulta = (params.get("consulta") or params.get("query")
                or params.get("texto") or "").strip()
    if not consulta:
        return {"ok": False, "error": "Falta sobre qué buscar."}
    m = _m()
    if not m.disponible():
        return {"ok": False, "error": "Ahora mismo no tengo memoria "
                                      "persistente disponible, señor."}
    recs = m.search(consulta, limit=limite)
    return {"ok": True, "consulta": consulta, "total": len(recs),
            "recuerdos": [r["texto"] for r in recs]}


def memory_add_handler(params: dict) -> dict:
    texto = (params.get("texto") or params.get("recuerdo")
             or params.get("contenido") or "").strip()
    if not texto:
        return {"ok": False, "error": "No me ha dicho qué debo recordar."}
    m = _m()
    if not m.disponible():
        return {"ok": False, "error": "Ahora mismo no tengo memoria "
                                      "persistente disponible, señor."}
    r = m.add(texto, categoria=(params.get("categoria") or ""))
    if not r.get("ok"):
        return {"ok": False, "error": r.get("motivo", "no se pudo guardar")}
    return {"ok": True, "guardado": texto}


def memory_update_handler(params: dict) -> dict:
    mid = str(params.get("id") or params.get("memory_id") or "").strip()
    texto = (params.get("texto") or params.get("nuevo") or "").strip()
    if not mid or not texto:
        return {"ok": False, "error": "Necesito el id y el texto nuevo."}
    m = _m()
    if not m.disponible():
        return {"ok": False, "error": "Memoria no disponible, señor."}
    r = m.update(mid, texto)
    return r if r.get("ok") else {"ok": False, "error": r.get("motivo", "")}


def memory_delete_handler(params: dict) -> dict:
    mid = str(params.get("id") or params.get("memory_id") or "").strip()
    if not mid:
        return {"ok": False, "error": "Falta el id del recuerdo."}
    m = _m()
    if not m.disponible():
        return {"ok": False, "error": "Memoria no disponible, señor."}
    r = m.delete(mid)
    return r if r.get("ok") else {"ok": False, "error": r.get("motivo", "")}


def memory_list_handler(params: dict) -> dict:
    m = _m()
    if not m.disponible():
        return {"ok": False, "error": "Memoria no disponible, señor."}
    try:
        limite = max(1, min(int(params.get("limite", 50) or 50), 200))
    except (TypeError, ValueError):
        limite = 50
    recs = m.get_all(limit=limite)
    return {"ok": True, "total": len(recs), "recuerdos": recs}


_HANDLERS = {
    "memory_search": memory_search_handler,
    "memory_add": memory_add_handler,
    "memory_update": memory_update_handler,
    "memory_delete": memory_delete_handler,
    "memory_list": memory_list_handler,
}

for _t in MEMORY_TOOLS:
    _t["handler"] = _HANDLERS[_t["name"]]
