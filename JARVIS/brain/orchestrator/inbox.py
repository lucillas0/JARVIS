"""Puente entre una pregunta de OpenCode y el mundo real del usuario.

JARVIS Live tiene prioridad: si el señor está delante del ordenador, la
pregunta se ve en el panel. Si no está, se le avisa por Discord y puede
contestar allí ("2", "PostgreSQL", "sí", "no"...).

Nunca se contesta solo. Este módulo solo interprets lo que el señor escribe;
si no está claro, devuelve None y el mensaje sigue su camino normal hacia el
modelo. Para eso están los dos botones de §42: la decisión la toma una
persona, no el código.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

log = logging.getLogger("brain.opencode.inbox")

_manager = None
_lock_numero = re.compile(r"^\s*(\d{1,2})\s*[.)-]?\s*", re.I)
_letra = re.compile(r"^\s*([a-zA-Z])\s*[).:-]\s+", re.I)


def set_manager(manager) -> None:
    global _manager
    _manager = manager


def manager():
    return _manager


# --------------------------------------------------------------------------- #
# Aviso al propietario (Discord)
# --------------------------------------------------------------------------- #
def notificar(texto: str) -> bool:
    """DM privado al propietario. Si no hay Discord, no pasa nada."""
    texto = (texto or "").strip()
    if not texto:
        return False
    try:
        from main import brain  # import local: el puente no obliga a cargar el cerebro

        bridge = getattr(brain, "discord", None)
        if bridge is None or not hasattr(bridge, "dm_owner") or not hasattr(bridge, "_loop"):
            return False
        import asyncio

        loop = bridge._loop()
        if loop is None or not loop.is_running():
            return False
        future = asyncio.run_coroutine_threadsafe(bridge.dm_owner(texto[:1800]), loop)
        future.add_done_callback(
            lambda f: log.debug("aviso de OpenCode por Discord: %s",
                                "enviado" if f.exception() is None else "no disponible"))
        return True
    except Exception as exc:  # noqa: BLE001 - un aviso fallido no rompe nada
        log.debug("no se pudo avisar por Discord: %s", exc)
        return False


def mensaje_pregunta(tarea: dict, pregunta: dict, pendientes: int = 1) -> str:
    texto = (f"🤖 JARVIS necesita tu respuesta\n"
             f"Tarea: {(tarea.get('idea') or '')[:120]}\n"
             f"Sesión de OpenCode: {tarea.get('opencode_session_id', '')}\n\n"
             f"Pregunta de OpenCode:\n{(pregunta.get('question') or '')[:400]}\n")
    for i, opcion in enumerate(pregunta.get("options") or [], start=1):
        numero = f"{i}️⃣" if i < 10 else f"{i}."
        texto += f"{numero} {opcion.get('label')}\n"
    if pregunta.get("allow_custom") or not pregunta.get("options"):
        texto += "\nOtra respuesta: escribe «responde: tu texto».\n"
    texto += (f"\nPendientes: {pendientes}. "
              f"Responde aquí o en JARVIS Live (/live).\n"
              f"Referencia: {pregunta.get('id', '')}")
    return texto


def mensaje_permiso(tarea: dict, permiso: dict, pendientes: int = 1) -> str:
    return (f"🔐 JARVIS · AUTORIZACIÓN REQUERIDA\n"
            f"Tarea: {(tarea.get('idea') or '')[:120]}\n"
            f"Sesión de OpenCode: {tarea.get('opencode_session_id', '')}\n\n"
            f"OpenCode quiere: {permiso.get('action')}\n"
            f"Detalle: {(permiso.get('detail') or '')[:300]}\n\n"
            f"Responde «una vez», «siempre» o «denegar».\n"
            f"Pendientes: {pendientes}. También puedes decidirlo en JARVIS Live (/live).\n"
            # Los tres identificadores: sin ellos no se puede comprobar que la
            # respuesta sea de esta petición y no de una antigua.
            f"Misión: {tarea.get('id', '')}\n"
            f"Referencia: {permiso.get('id', '')}")


# --------------------------------------------------------------------------- #
# Lectura de la respuesta del usuario
# --------------------------------------------------------------------------- #
_PREFIJOS = ("responde:", "respuesta:", "r:", "answer:")


def _coincide(texto: str, pregunta: dict) -> tuple[bool, object]:
    """¿Qué quiere decir exactamente lo que ha escrito el usuario?

    Cuidado por diseño: en un canal de chat normal ("busca el proyecto en el
    escritorio") un mensaje NO puede convertirse por sorpresa en la respuesta
    a la pregunta de OpenCode. Solo se acepta si encaja con una opción, o si
    el señor lo pide de forma explícita con el prefijo «responde:».
    """
    limpio = (texto or "").strip()
    if not limpio:
        return False, None
    minuscula = limpio.casefold()
    for prefijo in _PREFIJOS:
        if minuscula.startswith(prefijo):
            resto = limpio[len(prefijo):].strip()
            if resto:
                return True, resto[:400]
            return False, None
    m = _letra.match(limpio)
    if m and limpio[m.end():]:
        indice = ord(m.group(1).lower()) - ord("a")
        opciones = pregunta.get("options") or []
        if 0 <= indice < len(opciones):
            return True, opciones[indice]["value"]
    # OJO: la expresión se llama `_lock_numero`, no `_numero`. Estaba escrito al
    # revés, así que contestar con un número ("2", "1)", "3.") lanzaba un
    # NameError. Como quien llama lo envuelve en un try/except, el efecto era
    # peor que un fallo visible: la respuesta se descartaba en silencio, el
    # permiso se quedaba pendiente para siempre y JARVIS no contestaba nada.
    m = _lock_numero.match(limpio)
    if m:
        indice = int(m.group(1)) - 1
        opciones = pregunta.get("options") or []
        if 0 <= indice < len(opciones):
            return True, opciones[indice]["value"]
    etiqueta = limpio.casefold()
    for opcion in pregunta.get("options") or []:
        if etiqueta == str(opcion.get("label", "")).casefold() or \
           etiqueta == str(opcion.get("value", "")).casefold():
            return True, opcion["value"]
    return False, None


_SI = re.compile(r"^\s*(s[ií]|si|claro|adelante|vale|ok|permit[ei]do|once|una vez)\s*\.?\s*$", re.I)
_NO = re.compile(r"^\s*(no|nunca|denegar|deniega|rechaz[ao]|stop|parar)\s*\.?\s*$", re.I)
_SIEMPRE = re.compile(r"^\s*(siempre|always|para siempre)\s*\.?\s*$", re.I)


def responder_desde_chat(texto: str) -> Optional[str]:
    """Si el mensaje contesta a algo pendiente de OpenCode, lo entrega.

    Devuelve el texto de confirmación para el usuario, o None si el mensaje
    no era una respuesta (y por tanto debe seguir su camino normal).
    """
    if _manager is None:
        return None
    limpio = (texto or "").strip()
    if not limpio or len(limpio) > 400:
        return None
    try:
        pendientes_preguntas = _manager.preguntas_pendientes()
        if len(pendientes_preguntas) == 1:
            pregunta = pendientes_preguntas[0]
            ok, valor = _coincide(limpio, pregunta)
            if ok:
                res = _manager.responder(
                    pregunta["task_id"], pregunta["id"],
                    {pregunta.get("key") or "respuesta": valor})
                if res.get("ok"):
                    return (f"✅ Enviado a OpenCode: {pregunta.get('question', '')[:120]}"
                            f"\nRespuesta: {valor}")
                return f"⚠️ {res.get('error', 'No se pudo enviar la respuesta.')}"
        elif len(pendientes_preguntas) > 1:
            for pregunta in pendientes_preguntas:
                ok, valor = _coincide(limpio, pregunta)
                if ok:
                    res = _manager.responder(
                        pregunta["task_id"], pregunta["id"],
                        {pregunta.get("key") or "respuesta": valor})
                    if res.get("ok"):
                        return f"✅ Enviado a OpenCode: {valor}"
                    return f"⚠️ {res.get('error', 'No se pudo enviar la respuesta.')}"

        pendientes_permisos = _manager.permisos_pendientes()
        if len(pendientes_permisos) == 1:
            permiso = pendientes_permisos[0]
            decision = None
            if _NO.match(limpio):
                decision = "reject"
            elif _SIEMPRE.match(limpio):
                decision = "always"
            elif _SI.match(limpio):
                decision = "once"
            if decision:
                res = _manager.decidir_permiso(permiso["task_id"], permiso["id"], decision)
                if res.get("ok"):
                    return f"✅ Permiso enviado a OpenCode: {permiso.get('detail') or permiso.get('action')}"
                return f"⚠️ {res.get('error', 'No se pudo enviar la decisión.')}"
    except Exception as exc:  # noqa: BLE001 - nunca romper el canal de entrada
        log.debug("respuesta por chat: %s", exc)
    return None