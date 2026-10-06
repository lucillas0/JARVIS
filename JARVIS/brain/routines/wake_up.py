"""Rutina Modo Despertar.

Se activa SOLO bajo demanda ("buenos días" / "despierta"); nunca automática
por hora. Secuencia: saludo → clima (1 frase + sarcasmo pegado al dato) →
agenda (3-4 próximos, hora — título) → mensajes (Gmail y WhatsApp por
separado, remitente + resumen de una línea) → noticia tech (titular + por
qué importa, sin URLs en voz) → despedida → música de fondo en volumen
bajo (con ducking; solo suena en este protocolo).

Reglas: frases cortas de resumen hablado; jamás leer URLs, emails o
metadatos; jamás inventar: si una fuente falla, se dice con naturalidad.

Todos los pasos son de solo lectura → sin doble canal de confirmación.
"""
import logging
import threading
import time
from typing import Optional

from config import Config
from integrations import weather as weather_mod
from integrations import web_intel

log = logging.getLogger("routines.wake_up")

_SARCASM_NOTE = {
    "lluvia": "Perfecto para quedarse en la cama, si no fuera porque el mundo espera.",
    "niebla": "Ideal si hoy planeaba fingir que ve algo.",
    "tormenta": "La atmósfera está tan de mal humor como mi señor antes del café.",
    "nublado": "Como el futuro de las reuniones del lunes.",
    "calor": "Es culpa del cambio climático, no mía.",
    "frío": "Abríguese, señor, no querrá resfriarse antes del café.",
    "despejado": "Casi sospechoso, disfrútelo antes de que cambie de idea.",
}
_SARCASM_DEFAULT = "Típico día para quedarse en la cama, si el trabajo lo permitiera."


def _one_line(s: str, max_len: int = 90) -> str:
    """Colapsa espacios y recorta a una línea (sin citar textos completos)."""
    s = " ".join(str(s or "").split())
    s = s.rstrip(".")
    return s if len(s) <= max_len else s[:max_len].rstrip() + "…"


def _urgent(m: dict) -> bool:
    blob = f"{m.get('subject', '')} {m.get('snippet', '')}".lower()
    return any(k in blob for k in ("urgente", "urgent", "importante", "important", "asap"))


def _speak_flow(reply_fn, texts: list[str]):
    """Encadena locuciones, esperando a que termine cada una."""
    for t in texts:
        reply_fn(t)
        # esperar fin del habla (el flujo lo gestiona el voice manager, aquí
        # simplemente vamos despacio para no pisar la cola)
        time.sleep(0.3)


def _tech_bite() -> str:
    """Titular tech en una frase + por qué importa en otra. Sin URLs en voz."""
    items = [i for i in (web_intel.tech_news(limit=3) or [])
             if (i.get("title") or "").strip()]
    if not items:
        return "No hay noticias frescas de tecnología hoy, señor."
    titles = "\n".join(f"- {_one_line(i['title'], 140)}" for i in items[:3])
    descs = "\n".join(f"- {_one_line(i.get('summary', ''), 200)}"
                      for i in items[:3] if i.get("summary"))
    # Vía modelo ligero: respeta formato y no inventa (usa solo estos datos)
    try:
        from model_router import ModelRouter
        prompt = (
            "Eres JARVIS, mayordomo sobrio. Con SOLO estos titulares (no inventes):\n"
            f"{titles}\n"
            + (f"Contexto:\n{descs}\n" if descs else "") +
            "Devuelve EXACTAMENTE dos frases en español: 1) el titular más "
            "relevante reducido a una frase; 2) por qué importa a quien trabaja "
            "con tecnología, en una frase. Sin URLs, fuentes ni adornos.")
        res = ModelRouter().respond([{"role": "user", "content": prompt}],
                                    light=True)
        bite = " ".join(str(res.get("reply", "") or "").split())
        if bite:
            sents = [s.strip() for s in bite.replace("! ", ". ").replace("? ", ". ").split(". ") if s.strip()]
            sents = [(s if s.endswith((".", "!", "?")) else s + ".") for s in sents[:2]]
            if sents:
                return " ".join(sents)
    except Exception as e:
        log.debug("tech bite LLM: %s", e)
    # Fallback determinista: titular + primera frase del resumen
    top = items[0]
    s1 = _one_line(top["title"], 140) + "."
    s2 = ""
    if top.get("summary"):
        first = top["summary"].split(".")[0].strip()
        if first:
            s2 = " Importa porque " + first[:160].rstrip(".") + "."
    return (s1 + s2) if s2 else s1


def run(emit, speak_fn, engine, music: bool = True) -> Optional[str]:
    """Ejecuta la rutina. speak_fn(texto) encola el TTS.

    Devuelve el texto hablado completo (para el historial) o None.
    music=False omite el tema de fondo (rituales remotos: Discord/WhatsApp).
    """
    timeline: list[str] = []

    try:
        timeline.append("Buenos días, señor.")
        # 1. Saludo
        speak_fn(timeline[0])

        # 2. Clima: UNA frase (dato + sarcasmo pegado), máx. dos frases
        try:
            w = weather_mod.get_weather()
            desc = " ".join(str(w.get("description", "") or "").split()).rstrip(".")
            temp = w.get("temperature")
            unit_txt = "°C" if str(Config.WEATHER_UNIT).lower() != "imperial" else "°F"
            if temp is None and not desc:
                raise RuntimeError("sin datos")
            temp_txt = f"{round(temp, 1)} grados {unit_txt}" if temp is not None else "temperatura desconocida"
            base = f"Fuera hay {temp_txt} y {desc or 'cielo indefinido'}."
            low = desc.lower()
            extra = next((line for key, line in _SARCASM_NOTE.items() if key in low), _SARCASM_DEFAULT)
            base += " " + extra
            timeline.append(base)
            speak_fn(base)
        except Exception as e:
            log.warning("clima en wake-up: %s", e)
            timeline.append("No he podido consultar el clima ahora mismo, señor.")
            speak_fn(timeline[-1])

        # 3. Agenda: 3-4 próximos en orden, "hora — título"; resto = conteo
        try:
            from integrations import calendar
            events = calendar.list_today(limit=10) or []
            if not events:
                summary = "Su agenda está tan vacía como mi lista de quehaceres… da igual, no tiene nada hoy."
            else:
                ordered = sorted(events, key=lambda e: e.get("start") or "")
                show = ordered[:4]
                parts = [f"{_hour(e.get('start', ''))} — {_one_line(e.get('summary', 'Sin título'), 60)}"
                         for e in show]
                summary = "En su agenda: " + ". ".join(parts) + "."
                if len(ordered) > len(show):
                    summary += f" Son {len(ordered)} en total; pídame detalle si los quiere."
            timeline.append(summary)
            speak_fn(summary)
        except Exception as e:
            log.warning("agenda wake-up: %s", e)
            timeline.append("No he podido consultar su agenda ahora mismo, señor.")
            speak_fn(timeline[-1])

        # 4a. Gmail: remitente + resumen de una línea, urgentes primero
        try:
            from integrations import gmail
            unread = gmail.unread_summary(limit=6) or []
            if not unread:
                msg = "Nada en el correo."
            else:
                ordered = sorted(unread, key=lambda m: (not _urgent(m), m.get("date", "")))
                lines = []
                for m in ordered[:3]:
                    who = _one_line(m.get("name") or m.get("from") or "Desconocido", 40)
                    what = _one_line(m.get("subject") or m.get("snippet") or "sin asunto", 80)
                    lines.append(f"{who}: {what}")
                msg = "En el correo: " + ". ".join(lines) + "."
                if len(ordered) > 3:
                    msg += f" Hay {len(ordered)} sin leer en total."
            timeline.append(msg)
            speak_fn(msg)
        except Exception as e:
            log.warning("gmail wake-up: %s", e)
            timeline.append("No he podido consultar el correo ahora mismo, señor.")
            speak_fn(timeline[-1])

        # 4b. WhatsApp (solo lectura): conteo honesto, sin citar literal
        try:
            from integrations import whatsapp
            wapp = whatsapp.unread_summary()
            if not wapp.get("open"):
                msg = "WhatsApp cerrado en el PC."
            elif not wapp.get("lines"):
                msg = "Nada nuevo en WhatsApp."
            else:
                n = len(wapp["lines"])
                msg = f"En WhatsApp hay {n} mensajes recientes a la vista."
            timeline.append(msg)
            speak_fn(msg)
        except Exception as e:
            log.warning("whatsapp wake-up: %s", e)
            timeline.append("No he podido asomarme a WhatsApp ahora mismo, señor.")
            speak_fn(timeline[-1])

        # 5. Noticia tech/IA: titular en una frase + por qué importa en otra
        try:
            news = _tech_bite()
            timeline.append(news)
            speak_fn(news)
        except Exception as e:
            log.warning("noticias wake-up: %s", e)
            timeline.append("No he podido consultar las noticias ahora mismo, señor.")
            speak_fn(timeline[-1])

        # 6. Despedida
        timeline.append("Espero que tenga un buen día.")
        speak_fn(timeline[-1])

        # 7. Música de fondo (volumen bajo, con ducking cuando se le habla después)
        if music:
            theme = Config.AUDIO_THEME_PATH
            if theme and engine and __import__("os").path.exists(theme):
                time.sleep(0.2)
                engine.set_music_volume(0.4)
                engine.set_music_active(True)
                ok = engine.play_mp3_file(theme, volume=0.4)
                if ok:
                    emit({"type": "music_start", "track": "wake-up-theme"})
                    log.info("Sonando tema de despertar en volumen bajo.")
                else:
                    engine.set_music_active(False)
            else:
                log.info("No existe el archivo de música (%s); omisión.", theme)

        return " ".join(timeline)
    except Exception as e:
        log.exception("Rutina wake-up con error: %s", e)
        return " ".join(timeline) if timeline else None


def _hour(iso: str) -> str:
    if not iso:
        return "?"
    try:
        return iso[11:16]
    except Exception:
        return iso[:16]