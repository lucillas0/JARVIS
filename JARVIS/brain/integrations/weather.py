"""Clima con detección automática de ubicación (sección 9).

- Sin API key: usa Open-Meteo (gratis, sin key) y geolocalización por IP.
- Con WEATHER_API_KEY: usa OpenWeatherMap.
"""
import json
import logging
import urllib.request
from urllib.parse import quote

from config import Config

log = logging.getLogger("integrations.weather")

try:
    import geocoder
    _HAS_GEOCODER = True
except Exception:
    _HAS_GEOCODER = False


def _my_location() -> tuple[float, float]:
    """Detección automática de ubicación (IP). Devuelve (lat, lon)."""
    try:
        if _HAS_GEOCODER:
            g = geocoder.ip("me")
            if g.latlng:
                return g.latlng[0], g.latlng[1]
        req = urllib.request.Request("http://ip-api.com/json/?fields=lat,lon,query",
                                     headers={"User-Agent": "curl/8"})
        with urllib.request.urlopen(req, timeout=8) as resp:
            d = json.loads(resp.read())
            return d["lat"], d["lon"]
    except Exception as e:
        log.warning("No pude detectar ubicación: %s", e)
    return 40.4168, -3.7038  # fallback Madrid


def get_weather(**kwargs) -> dict:
    """Devuelve dict con condiciones actuales + predicción del día."""
    lat, lon = _my_location()
    unit = Config.WEATHER_UNIT
    try:
        if Config.WEATHER_API_KEY:
            url = (f"https://api.openweathermap.org/data/2.5/weather?lat={lat}&lon={lon}"
                   f"&units={unit}&lang=es&appid={Config.WEATHER_API_KEY}")
            with urllib.request.urlopen(url, timeout=15) as resp:
                d = json.loads(resp.read())
            temp = d["main"]["temp"]
            desc = d["weather"][0]["description"]
            feels = d["main"]["feels_like"]
            hum = d["main"]["humidity"]
            wind = d["wind"]["speed"]
            return {"source": "openweathermap", "lat": lat, "lon": lon,
                    "description": desc, "temperature": temp, "feels_like": feels,
                    "humidity": hum, "wind_kmh": wind}
        # Open-Meteo
        url = (f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}"
               f"&current=temperature_2m,relative_humidity_2m,apparent_temperature,"
               f"weather_code,wind_speed_10m&daily=weather_code,temperature_2m_max,"
               f"temperature_2m_min&timezone=auto&forecast_days=1")
        with urllib.request.urlopen(url, timeout=15) as resp:
            d = json.loads(resp.read())
        cur = d["current"]
        desc = _code_to_desc(cur["weather_code"])
        return {"source": "open-meteo", "lat": lat, "lon": lon,
                "description": desc,
                "temperature": cur["temperature_2m"],
                "feels_like": cur["apparent_temperature"],
                "humidity": cur["relative_humidity_2m"],
                "wind_kmh": cur["wind_speed_10m"],
                "max": d["daily"]["temperature_2m_max"][0],
                "min": d["daily"]["temperature_2m_min"][0]}
    except Exception as e:
        log.warning("Clima falló: %s", e)
        raise RuntimeError("No pude consultar el clima ahora mismo.")


def _code_to_desc(code: int) -> str:
    codes = {
        0: "cielo despejado", 1: "mayormente despejado", 2: "parcialmente nublado",
        3: "nublado", 45: "niebla", 48: "niebla con escarcha", 51: "llovizna ligera",
        53: "llovizna", 55: "llovizna intensa", 61: "lluvia ligera", 63: "lluvia",
        65: "lluvia intensa", 71: "nevada ligera", 73: "nevada", 75: "nevada intensa",
        80: "chubascos ligeros", 81: "chubascos", 82: "chubascos fuertes",
        95: "tormenta", 96: "tormenta con granizo", 99: "tormenta intensa",
    }
    return codes.get(code, f"código {code}")