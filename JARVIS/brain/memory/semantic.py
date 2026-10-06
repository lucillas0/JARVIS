"""Memoria semántica con Qdrant (modo local embebido, sin servidor).

Cuando el historial supera CONTEXT_WINDOW_MESSAGES, se indexan los mensajes en
Qdrant local para recuperar cosas de conversaciones largas (sección 11, bloque 9).
Usa el modelo de embeddings de Ollama si está disponible; si no, hash-hashing
con Jaccard (sin dependencias) para poder funcionar siempre.
"""
import hashlib
import json
import logging
import os
import re
import time
from pathlib import Path

from config import Config

log = logging.getLogger("memory.semantic")

try:
    from qdrant_client import QdrantClient
    from qdrant_client.models import Distance, VectorParams, PointStruct
    _HAS_QDRANT = True
except Exception as e:  # pragma: no cover
    _HAS_QDRANT = False
    log.warning("qdrant-client no disponible: %s", e)


_ollama_up = {"t": 0.0, "v": False}
_OLLAMA_UP_TTL = 8.0


def _ollama_reachable() -> bool:
    """¿Responde el puerto de Ollama? Cacheado 8s para no penalizar mensajes."""
    now = time.time()
    if now - _ollama_up["t"] <= _OLLAMA_UP_TTL:
        return _ollama_up["v"]
    ok = False
    try:
        import socket
        import urllib.parse
        u = urllib.parse.urlsplit(Config.OLLAMA_HOST)
        host = (u.hostname or "127.0.0.1").lower()
        if host == "localhost":
            host = "127.0.0.1"
        s = socket.create_connection((host, u.port or 11434), timeout=1.0)
        s.close()
        ok = True
    except Exception:
        ok = False
    _ollama_up["t"] = now
    _ollama_up["v"] = ok
    return ok


class SemanticMemory:
    def __init__(self):
        self._client = None
        self._collection = "jarvis_semantic"
        self._dim = 384
        self._tokens_cache: dict = {}

    def _ensure(self):
        if self._client is None and _HAS_QDRANT:
            try:
                os.makedirs(Config.QDRANT_PATH, exist_ok=True)
                self._client = QdrantClient(path=Config.QDRANT_PATH)
                if not self._client.collection_exists(self._collection):
                    self._client.create_collection(
                        collection_name=self._collection,
                        vectors_config=VectorParams(size=self._dim, distance=Distance.COSINE),
                    )
            except Exception as e:
                log.warning("Qdrant local no arrancó: %s", e)
                self._client = None
        return self._client

    # ---------- embeddings ----------
    def _embed(self, text: str) -> list[float]:
        """Embeddings de Ollama si está, si no vector de caracteres (hashes).

        Timeout corto + probe cacheado: esto corre de forma síncrona antes de
        cada respuesta, un Ollama caído/ocupado no debe secuestrar el mensaje.
        """
        if _ollama_reachable():
            try:
                import urllib.request
                payload = json.dumps({"model": "llama3.2:1b", "prompt": text[:2000]}).encode()
                req = urllib.request.Request(f"{Config.OLLAMA_HOST}/api/embeddings",
                                             payload, {"Content-Type": "application/json"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    data = json.loads(resp.read())
                emb = data.get("embedding")
                if emb:
                    return [float(x) for x in emb[: self._dim]]
            except Exception:
                pass
        return self._hash_embedding(text, self._dim)

    @staticmethod
    def _hash_embedding(text: str, dim: int) -> list[float]:
        """Embedding determinista basado en tokens (substitute cuando no hay Ollama)."""
        vec = [0.0] * dim
        tokens = re.findall(r"\w+", text.lower())
        for t in tokens:
            h = int(hashlib.md5(t.encode()).hexdigest(), 16)
            vec[h % dim] += 1.0
        norm = (sum(x * x for x in vec) ** 0.5) or 1.0
        return [x / norm for x in vec]

    # ---------- API ----------
    def add(self, role: str, content: str, created_at: str = "") -> None:
        client = self._ensure()
        if not client:
            return
        try:
            from qdrant_client.models import PointStruct
            point_id = int(hashlib.md5(f"{role}:{content}:{created_at}".encode()).hexdigest()[:12], 16) % (1 << 63)
            client.upsert(self._collection, [PointStruct(
                id=point_id,
                vector=self._embed(content),
                payload={"role": role, "content": content, "created_at": created_at},
            )])
            log.info("Evento indexado en memoria semántica")
        except Exception as e:
            log.warning("Qdrant add falló: %s", e)

    def search(self, query: str, limit: int = 4) -> list[dict]:
        client = self._ensure()
        if not client:
            return []
        try:
            hits = client.query_points(
                collection_name=self._collection,
                query=self._embed(query),
                limit=limit,
            ).points
            return [{"content": p.payload.get("content"), "role": p.payload.get("role"),
                     "score": round(float(p.score), 4)} for p in hits]
        except Exception as e:
            log.warning("Qdrant search falló: %s", e)
            return []

    def recall_context(self, query: str, limit: int = 4) -> list[dict]:
        """Convierte resultados semánticos en turnos de chat reutilizables."""
        return [{"role": h.get("role", "user"), "content": h.get("content", "")}
                for h in self.search(query, limit) if h.get("content")]


semantic_memory = SemanticMemory()