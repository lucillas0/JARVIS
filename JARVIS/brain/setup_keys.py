# -*- coding: utf-8 -*-
"""Asistente de instalación de JARVIS (página /setup).

Modo LITE: se entra por el navegador, se pone la clave que hace falta para que
JARVIS hable y se acaba. Después, la pizarra funciona en el navegador.

Las claves van en dos grupos bien separados, y esto no es capricho: son cosas
distintas. Las OBLIGATORIAS son las que sin las cuales JARVIS no puede
contestar ni una palabra. Las OPCIONALES abren funciones (meteo, Spotify,
Discord, análisis de virus...) y cada una tiene su sitio donde pedirla. Un
asistente que mezclara las dos obligaría a inventarse claves para usar el
meteorológico.
"""

# Clave -> (etiqueta, para qué sirve, dónde pedirla)
CLAVES_OBLIGATORIAS = [
    ("GROQ_API_KEY", "Groq",
     "La voz y las respuestas rápidas. Es la que más se nota.",
     "https://console.groq.com/keys"),
    ("GEMINI_API_KEY", "Google Gemini",
     "Tareas pesadas, razonamiento largo y análisis de imágenes.",
     "https://aistudio.google.com/apikey"),
    ("OPENROUTER_API_KEY", "OpenRouter",
     "Respaldo: si Groq o Gemini se caen, JARVIS sigue contestando.",
     "https://openrouter.ai/keys"),
]

CLAVES_OPCIONALES = [
    ("WEATHER_API_KEY", "Meteorología", "El tiempo a partir de su ciudad.",
     "https://openweathermap.org/api"),
    ("SPOTIFY_CLIENT_ID", "Spotify (id)", "Controlar la música de Spotify.",
     "https://developer.spotify.com/dashboard"),
    ("SPOTIFY_CLIENT_SECRET", "Spotify (secreto)", "Va junto al id de arriba.",
     "https://developer.spotify.com/dashboard"),
    ("VIRUSTOTAL_API_KEY", "VirusTotal", "Analiza ficheros y webs con 70 antivirus.",
     "https://www.virustotal.com/gui/join-us"),
    ("DISCORD_BOT_TOKEN", "Discord (token)", "Controlar JARVIS desde Discord.",
     "https://discord.com/developers/applications"),
    ("DISCORD_OWNER_ID", "Discord (su id)", "Su id de usuario, para que solo usted hable con él.",
     "https://discord.com/users/@me"),
    ("NTIFY_TOPIC", "Avisos al móvil", "Notificaciones push de JARVIS al móvil.",
     "https://ntfy.sh"),
]
