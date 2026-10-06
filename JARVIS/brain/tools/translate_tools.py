# -*- coding: utf-8 -*-
"""Traducción instantánea: Helsinki-NLP offline (ES<->EN, ~0.3s) con
fallback online (Google gratis) y último recurso LLM. Modelos lazy (solo
al primer uso)."""
import logging
import re

log = logging.getLogger("tools.translate")

_MODELS = {}
_LANGS = {"es", "en"}


def _pair_models(src: str, dst: str):
    key = (src, dst)
    if key in _MODELS:
        return _MODELS[key]
    from transformers import MarianMTModel, MarianTokenizer
    pair = f"Helsinki-NLP/opus-mt-{src}-{dst}"
    tok = MarianTokenizer.from_pretrained(pair)
    mod = MarianMTModel.from_pretrained(pair)
    mod.eval()
    _MODELS[key] = (tok, mod)
    return tok, mod


def offline_translate(text: str, src: str, dst: str) -> str:
    """Traduce ES<->EN sin internet. Lanza excepción si no puede."""
    t = (text or "").strip()
    if not t:
        return ""
    if len(t) > 2000:
        t = t[:2000]
    src = (src or "es").lower()[:2]
    dst = (dst or "en").lower()[:2]
    if src == dst:
        return t
    if src not in _LANGS or dst not in _LANGS:
        raise ValueError(f"offline solo es/en (pedido {src}->{dst})")
    import torch
    tok, mod = _pair_models(src, dst)
    parts = [p for p in re.split(r"(?<=[.!?])\s+", t) if p.strip()] or [t]
    out = []
    with torch.no_grad():
        for i in range(0, len(parts), 8):
            batch = parts[i:i + 8]
            b = tok(batch, return_tensors="pt", padding=True, truncation=True,
                    max_length=256)
            g = mod.generate(**b, max_length=256)
            out.extend(tok.batch_decode(g, skip_special_tokens=True))
    return " ".join(out).strip()


def online_translate(text: str, src: str, dst: str) -> str:
    """Google gratis (sin clave). Para cualquier idioma con internet."""
    import json as _js
    import urllib.parse
    import urllib.request
    t = (text or "").strip()
    if not t:
        return ""
    q = urllib.parse.urlencode({"client": "gtx", "sl": src or "auto",
                                "tl": dst or "en", "dt": "t", "q": t})
    req = urllib.request.Request(
        "https://translate.googleapis.com/translate_a/single?" + q,
        headers={"User-Agent": "Mozilla/5.0"})
    with urllib.request.urlopen(req, timeout=20) as r:
        data = _js.loads(r.read().decode("utf-8", errors="ignore"))
    try:
        return "".join(seg[0] for seg in data[0] if seg and seg[0]).strip()
    except Exception:
        return ""


def translate_text(text: str, src: str = "es", dst: str = "en") -> dict:
    """Offline primero, online después. Devuelve {text, via}."""
    t = (text or "").strip()
    if not t:
        raise ValueError("Nada que traducir.")
    try:
        out = offline_translate(t, src, dst)
        if out:
            return {"text": out, "via": "offline"}
    except Exception as e:
        log.debug("offline: %s", e)
    out = online_translate(t, src, dst)
    if out:
        return {"text": out, "via": "online"}
    raise RuntimeError("No pude traducir (ni offline ni online).")


def translate_handler(params: dict) -> dict:
    return translate_text(params.get("text", ""),
                          params.get("src", "es"), params.get("dst", "en"))


TRANSLATE_TOOLS = [
    {"name": "translate",
     "risk_level": "low",
     "requires_confirmation": False,
     "parameters": {"text": "str", "src": "str (es|en|auto)", "dst": "str"},
     "description": "Traduce texto al instante (offline ES-EN, online resto).",
     "label": "Traduciendo",
     "handler": translate_handler},
]
