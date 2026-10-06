import os
import json
from fastapi import FastAPI, WebSocket
from groq import Groq
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()
client = Groq(api_key=os.environ["GROQ_API_KEY"])

SYSTEM_PROMPT = (
    "Eres JARVIS, un asistente personal. Respondes en español, "
    "de forma clara, directa y con un toque de personalidad. "
    "Por ahora solo puedes conversar; el control del sistema y la voz "
    "se añadirán en fases posteriores."
)


def build_messages(user_message: str, history: list) -> list:
    """Context manager mínimo: system prompt + últimos turnos + mensaje actual."""
    messages = [{"role": "system", "content": SYSTEM_PROMPT}]
    for turn in history:
        role = "assistant" if turn.get("role") == "assistant" else "user"
        messages.append({"role": role, "content": turn.get("content", "")})
    messages.append({"role": "user", "content": user_message})
    return messages


@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    await websocket.accept()
    try:
        while True:
            raw = await websocket.receive_text()
            data = json.loads(raw)
            user_message = data.get("message", "")
            history = data.get("history", [])

            messages = build_messages(user_message, history)

            completion = client.chat.completions.create(
                model="llama-3.3-70b-versatile",
                messages=messages,
            )
            reply = completion.choices[0].message.content

            await websocket.send_text(json.dumps({"reply": reply}))
    except Exception:
        await websocket.close()
