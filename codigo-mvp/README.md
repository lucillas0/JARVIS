# JARVIS — MVP (bloques 1-3)

Cimientos + Brain mínimo + Memoria. Sin voz y sin control del sistema todavía
(eso llega en las siguientes fases).

## Estructura

```
jarvis/
├── app/     ← Electron + React (UI, event bus implícito, SQLite)
└── brain/   ← Servicio Python (FastAPI) — orchestrator + Groq
```

## 1. Levantar el brain (Python)

```bash
cd brain
python -m venv venv
source venv/bin/activate        # en Windows: venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env            # y pega ahí tu GROQ_API_KEY
uvicorn main:app --host 0.0.0.0 --port 8000
```

## 2. Levantar la app (Node/Electron)

En otra terminal:

```bash
cd app
npm install
npm start
```

La app se conecta a `ws://localhost:8000/ws`. Si el brain no está corriendo,
verás "desconectado" en la esquina superior derecha.

## Cómo seguir desde aquí

- **Bloque 4 (Voz)**: añadir wake word + STT/TTS al lado Python, y un
  micrófono/altavoz en el lado Electron.
- **Bloque 5 (Acciones)**: el `tool manager` ya está pensado para esto —
  el LLM nunca ejecuta nada directo, solo pide una herramienta que el
  orchestrator valida y ejecuta.
- **Memoria semántica (Qdrant)**: se añade cuando el historial en SQLite
  ya no quepa cómodo en el contexto del modelo.
