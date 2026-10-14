"""Cliente HTTP de Mem0 OSS (autoalojado).

Solo habla con el servicio. No decide NADA: no sabe que merece la pena
guardarse, ni que categoria tiene, ni cuando hay que descartar un recuerdo.
Eso es de memory/manager.py. Repartirlo asi es lo que permite que el resto
del codigo de JARVIS no dependa de Mem0: si manana se cambia de motor, se
toca este fichero y el manager.

La API del servidor OSS NO lleva prefijo /v1 (ese es el de la plataforma
alojada). Las rutas son /memories, /search, /reset.

Autenticacion: el servidor acepta X-API-Key. Si la instancia va con
AUTH_DISABLED=true -que es lo que se pone en una red de casa- no hace falta
ninguna y estas vacio. En cualquier otro caso, sin clave el servidor
contesta 401 y aqui se ve claro en el log.

Ningun secreto se escribe en el log: si falla, se dice el codigo y el
mensaje del servidor, que no lleva la clave.
"""
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

from config import Config

log = logging.getLogger("memory.mem0")


class Mem0NoDisponible(Exception):
    """El servicio no esta. No es un error de JARVIS, es una pieza de mas."""


class Mem0Respuesta(Exception):
    """El servidor ha respondido, pero con un error de la peticion (4xx).

    No es lo mismo que estar caido. Un id que no existe, un cuerpo mal
    formado o un filtro raro son cosas que el servidor ha procesado
    enteramente, y la memoria sigue entrando al dia siguiente.
    """
    def __init__(self, codigo: int, mensaje: str):
        super().__init__(mensaje)
        self.codigo = codigo
        self.mensaje = mensaje


class Mem0Cliente:
    def __init__(self, url: str = "", api_key: str = "",
                 timeout: float = 0.0):
        self.url = (url or Config.MEM0_URL).rstrip("/")
        self.api_key = api_key or Config.MEM0_API_KEY
        self.timeout = timeout or Config.MEMORY_TIMEOUT
        # Cuando Mem0 falla no se reintenta en cada mensaje: se marca como
        # caido y se vuelve a mirar pasado un rato. Sin esto, con el servicio
        # parado se perdian varios segundos en cada pregunta, y lo que tiene
        # que ser una dependencia degradable se.convertia en un lastre.
        self._caido_hasta = 0.0
        self._motivo = ""

    # --------------------------------------------------------------- estado
    @property
    def caido(self) -> bool:
        return time.time() < self._caido_hasta

    def marcar_caido(self, motivo: str) -> None:
        self._caido_hasta = time.time() + max(5, Config.MEMORY_RETRY_SECONDS)
        self._motivo = motivo[:140]
        log.warning("[MEMORY] Servicio no disponible: %s. Reintento en %ss.",
                    motivo[:120], max(5, Config.MEMORY_RETRY_SECONDS))

    def limpiar_estado(self) -> None:
        self._caido_hasta = 0.0
        self._motivo = ""

    def estado(self) -> dict:
        return {"disponible": not self.caido and self.configurado(),
                "url": self.url,
                "api_key": bool(self.api_key),
                "motivo": self._motivo,
                "reintenta_en": max(0, round(self._caido_hasta - time.time()))
                            if self.caido else 0}

    def configurado(self) -> bool:
        return bool(self.url)

    # ------------------------------------------------------------- interno
    def _peticion(self, metodo: str, ruta: str, cuerpo: dict | None = None,
                  params: dict | None = None) -> dict:
        if not self.configurado():
            raise Mem0NoDisponible("no hay MEM0_URL configurado")
        if self.caido:
            raise Mem0NoDisponible(self._motivo or "servicio marcado como caido")

        url = f"{self.url}{ruta}"
        if params:
            url += "?" + urllib.parse.urlencode(
                {k: v for k, v in params.items() if v not in (None, "")})
        datos = json.dumps(cuerpo, ensure_ascii=False).encode("utf-8") \
            if cuerpo is not None else None
        cabeceras = {"Content-Type": "application/json",
                     "Accept": "application/json"}
        if self.api_key:
            cabeceras["X-API-Key"] = self.api_key
        req = urllib.request.Request(url, data=datos, headers=cabeceras,
                                     method=metodo)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                crudo = r.read().decode("utf-8", "replace")
                estado = r.status
        except urllib.error.HTTPError as e:
            # Un 4xx es el servidor RESPONDIENDO, no caido. Solo es un
            # 401/403 cuando es de configuracion (falta la clave), y eso se
            # propaga como error de configuracion.
            #
            # Antes cualquier HTTPError ponia el cliente en "caido" 30
            # segundos. Bastaba con que JARVIS borrara un id que ya no
            # existia para que la memoria quedara muda medio minuto, con el
            # log diciendo que el servicio estaba caido. Un 404 de un id
            # inexistente es una respuesta normal.
            if e.code in (401, 403):
                raise Mem0NoDisponible(
                    f"{e.code}: el servidor pide API key. Pon MEM0_API_KEY o "
                    f"arranca Mem0 con AUTH_DISABLED=true.") from None
            if 400 <= e.code < 500:
                # Respuesta valida del servidor: se devuelve tal cual y el
                # cortacircuitos NO se toca.
                raise Mem0Respuesta(e.code, f"HTTP {e.code}") from None
            self.marcar_caido(f"HTTP {e.code}")
            raise Mem0NoDisponible(f"HTTP {e.code}") from None
        except Exception as e:
            self.marcar_caido(str(e))
            raise Mem0NoDisponible(str(e)) from None

        self.limpiar_estado()
        if not crudo.strip():
            return {}
        try:
            d = json.loads(crudo)
        except Exception:
            log.warning("[MEMORY] Respuesta que no es JSON (HTTP %s)", estado)
            return {}
        return d if isinstance(d, dict) else {"result": d}

    # ------------------------------------------------------------- memoria
    def health(self) -> dict:
        """El servidor contesta? No lanza; sirve para /health y el arranque."""
        if not self.configurado():
            return {"ok": False, "motivo": "sin MEM0_URL"}
        url = f"{self.url}/"
        cabeceras = {"X-API-Key": self.api_key} if self.api_key else {}
        req = urllib.request.Request(url, headers=cabeceras, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                r.read(2048)
            self.limpiar_estado()
            return {"ok": True}
        except urllib.error.HTTPError as e:
            # 307/302 a /docs o similar: el servidor esta vivo, que es lo que
            # se quiere saber aqui. Un 401 tambien significa que vive.
            if e.code in (301, 302, 307, 308, 401, 403):
                self.limpiar_estado()
                return {"ok": True, "http": e.code}
            return {"ok": False, "motivo": f"HTTP {e.code}"}
        except Exception as e:
            return {"ok": False, "motivo": str(e)[:120]}

    def add(self, texto: str, user_id: str, metadata: dict | None = None,
            agent_id: str = "", run_id: str = "") -> dict:
        """Mete un recuerdo.

        Mem0 decide por su cuenta si esto es nuevo, actualiza uno que ya
        existe o lo descarta por duplicado. Por eso aqui no hay
        "comprobar antes de anadir": esa logica ya la tiene el, y
        duplicarla aqui solo produciria recuerdos repetidos.
        """
        cuerpo: dict = {"messages": [{"role": "user", "content": texto}],
                        "user_id": user_id}
        if metadata:
            cuerpo["metadata"] = metadata
        if agent_id:
            cuerpo["agent_id"] = agent_id
        if run_id:
            cuerpo["run_id"] = run_id
        return self._peticion("POST", "/memories", cuerpo)

    def search(self, consulta: str, user_id: str, limit: int = 5,
               metadata_filtro: dict | None = None) -> list[dict]:
        cuerpo: dict = {"query": consulta, "user_id": user_id, "limit": limit}
        if metadata_filtro:
            cuerpo["metadata"] = metadata_filtro
        d = self._peticion("POST", "/search", cuerpo)
        r = d.get("results")
        if r is None:
            r = d.get("memories") or d.get("data") or []
        return r if isinstance(r, list) else []

    def get_all(self, user_id: str, limit: int = 200) -> list[dict]:
        d = self._peticion("GET", "/memories",
                           params={"user_id": user_id, "limit": limit})
        r = d.get("results")
        if r is None:
            r = d.get("memories") or d.get("data") or []
        return r if isinstance(r, list) else []

    def get(self, memory_id: str) -> dict:
        return self._peticion("GET", f"/memories/{urllib.parse.quote(str(memory_id))}")

    def update(self, memory_id: str, texto: str) -> dict:
        return self._peticion(
            "PUT", f"/memories/{urllib.parse.quote(str(memory_id))}",
            {"text": texto})

    def delete(self, memory_id: str) -> dict:
        return self._peticion(
            "DELETE", f"/memories/{urllib.parse.quote(str(memory_id))}")

    def delete_all(self, user_id: str) -> dict:
        return self._peticion("DELETE", "/memories",
                              params={"user_id": user_id})

    def reset(self) -> dict:
        return self._peticion("POST", "/reset")

    def entities(self) -> list[dict]:
        d = self._peticion("GET", "/entities")
        r = d.get("results") or d.get("entities") or []
        return r if isinstance(r, list) else []


_cliente: Mem0Cliente | None = None


def cliente() -> Mem0Cliente:
    """Una sola instancia, que es la que guarda el estado de "caido"."""
    global _cliente
    if _cliente is None:
        _cliente = Mem0Cliente()
    return _cliente
