"""Context Engine: qué información necesita de verdad esta petición.

**Memoria no es contexto.** La memoria guarda; el contexto decide. Antes de
esta pieza, la construcción del contexto vivía incrustada dentro de `_handle`
(una función de más de 1.600 líneas en `main.py`): los recuerdo semánticos, la
memoria persistente, los documentos, los apuntes y el hilo se elegían a mano,
sin presupuesto ni forma de mirar qué se había cogido.

Aquí se juntan, y además:

- se decide **qué no** entra (presupuesto por pieza, con pesos reales);
- se declara **por qué** entró cada pieza, y se puede mirar;
- se añaden las que faltaban: la misión activa, las decisiones recientes y el
  estado del sistema, **cuando son relevantes**;
- se avisa al bus (`context.built`), que es lo que pinta la línea de tiempo.

No duplica nada: el Engine llama a los sistemas que ya había
(`db.get_recent_messages`, `semantic_memory`, `memory.manager.para_el_prompt`,
`library.search_docs`, `campus_apuntes`, el orquestador y `command.store`). Si
uno falla, se sigue sin él: el contexto es una mejora, nunca un punto único de
fallo.
"""
from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

from config import Config

log = logging.getLogger("brain.context")

# --------------------------------------------------------------------------- #
# Presupuesto
# --------------------------------------------------------------------------- #
# El límite son caracteres de las piezas (memoria, documentos, apuntes, misión).
# El prompt de sistema y el historial NO cuentan: esos los pone siempre main.
PRESUPUESTO_DEFECTO = 6000

# Palabras que hacen que el estado del sistema sea relevante.
PIDE_SISTEMA = re.compile(
    r"\b(como estas|c[oó]mo va|estado|salud|sistema|servicio|servidor|"
    r"incidente|problema|falla|error|ca[íi]do|funciona|tengo \d+\s*gb|"
    r"memoria|disco|procesador|cpu|discord|opencode|voz|mem0)\b", re.I)

# Palabras que hacen que la misión activa sea relevante.
PIDE_MISION = re.compile(
    r"\b(misi[óo]n|tarea|plan|riesgo|permiso|pregunta|opencode|"
    r"c[óo]digo|programa|desarrolla|proyecto|estado del trabajo)\b", re.I)

PIDE_DOCUMENTOS = re.compile(
    r"\b(apuntes|notas|documentos?|archivos?|ficheros?|pdf|docx|biblioteca|"
    r"nextcloud|nube)\b", re.I)

PIDE_DECISION = re.compile(
    r"\b(por qu[eé]|decisi[óo]n|eligiste|raz[óo]n|acordaste|planificaste)\b", re.I)


@dataclass
class Pieza:
    """Un trozo de contexto y la razón por la que se ha cogido."""

    origen: str
    texto: str
    peso: int = 1          # 3 = imprescindible, 1 = relleno
    motivo: str = ""
    caracteres: int = 0

    def __post_init__(self):
        if not self.caracteres:
            self.caracteres = len(self.texto or "")


@dataclass
class Contexto:
    """El contexto ya decidido: los mensajes y la explicación de cada pieza."""

    mensajes: list = field(default_factory=list)
    piezas: list = field(default_factory=list)
    descartado: list = field(default_factory=list)
    presupuesto: dict = field(default_factory=lambda: {"usado": 0, "limite": 0})
    situacion: dict = field(default_factory=dict)
    duracion_ms: float = 0.0

    def resumen(self) -> dict:
        """Lo que ve JARVIS Live: qué se cogió, de dónde y qué se dejó fuera."""
        return {
            "piezas": [{"origen": p.origen, "caracteres": p.caracteres,
                        "peso": p.peso, "motivo": p.motivo} for p in self.piezas],
            "descartado": self.descartado,
            "presupuesto": self.presupuesto,
            "situacion": self.situacion,
            "mensajes": len(self.mensajes),
            "duracion_ms": round(self.duracion_ms, 1),
        }

    def como_texto(self) -> str:
        return "\n".join(f"[{p.origen}] {p.texto}" for p in self.piezas)


# --------------------------------------------------------------------------- #
# El motor
# --------------------------------------------------------------------------- #
class ContextEngine:
    """Construye el contexto de una petición. Sin estado entre llamadas."""

    def construir(self, mensaje: str, *, prompt_sistema: str = "",
                  prompt_usuario: str = "",
                  input_mode: str = "text", recent_messages=None,
                  ancla: Optional[str] = None, apuntes_texto: str = "",
                  presupuesto: int = 0, emit: Optional[Callable] = None,
                  mission_id: str = "", incluir_sistema: Optional[bool] = None
                  ) -> Contexto:
        inicio = time.time()
        texto = (mensaje or "").strip()
        voz = input_mode in ("discord-voice", "voice", "whatsapp")
        rapido = bool(re.search(
            r"\b(r[áa]pido|date prisa|corre|modo r[áa]pido|responde r[áa]pido|"
            r"sin rodeos|al grano|breve)\b", texto, re.I))
        conjunto = rapido or voz
        limite = presupuesto or int(getattr(Config, "CONTEXT_MAX_CHARS",
                                            PRESUPUESTO_DEFECTO))

        piezas: list = []

        # --- 1. Apuntes locales (el ciclo) --------------------------------
        if apuntes_texto:
            piezas.append(Pieza("apuntes", apuntes_texto, 3,
                                "el señor pregunta de sus apuntes"))

        # --- 2. Memoria persistente (Mem0) ---------------------------------
        piezas.append(self._memoria(texto))

        # --- 3. Recuerdos semánticos --------------------------------------
        piezas.append(self._recuerdos(texto, conjunto))

        # --- 4. Documentos -------------------------------------------------
        piezas.append(self._documentos(texto, conjunto))

        # --- 5. Hilo de la conversación ------------------------------------
        if ancla:
            piezas.append(Pieza("hilo", ancla, 3,
                                "la pregunta va suelta: hay que seguir el hilo"))

        # --- 6. Misión activa ----------------------------------------------
        pieza_mision = self._mision(texto, mission_id)
        if pieza_mision:
            piezas.append(pieza_mision)

        # --- 7. Decisiones recientes ----------------------------------------
        piezas.append(self._decisiones(texto, conjunto))

        # --- 8. Estado del sistema ------------------------------------------
        quiere = PIDE_SISTEMA.search(texto) is not None if incluir_sistema is None \
            else bool(incluir_sistema)
        if quiere:
            piezas.append(self._sistema())

        piezas = [p for p in piezas if p and p.texto]
        piezas.sort(key=lambda p: -p.peso)

        elegidas: list = []
        gastado = 0
        fuera: list = []
        for pieza in piezas:
            if gastado + pieza.caracteres <= limite:
                elegidas.append(pieza)
                gastado += pieza.caracteres
            elif pieza.peso >= 3 and gastado < limite:
                # Una pieza imprescindible que no cabe ENTERA se recorta: mandarla
                # enteraignora el presupuesto (y con ella la ventana del modelo y
                # el coste), y tirarla del todo pierde justo lo más importante.
                # Se corta por el FINAL, que es donde está la explicación larga.
                quedan = max(0, limite - gastado)
                recorte = pieza.texto[:max(0, quedan - 40)] if quedan > 40 else ""
                if recorte:
                    fuera.append(f"{pieza.origen} recortada de {pieza.caracteres} "
                                 f"a {len(recorte)} caracteres por el presupuesto")
                    pieza.texto = recorte + "\n[...recortado por presupuesto]"
                    pieza.caracteres = len(pieza.texto)
                    elegidas.append(pieza)
                    gastado += pieza.caracteres
                    continue
                fuera.append(f"{pieza.origen} ({pieza.caracteres} caracteres, no "
                             f"cabe ni recortada en el presupuesto)")
            else:
                fuera.append(f"{pieza.origen} ({pieza.caracteres} caracteres, "
                             f"el presupuesto se había llenado antes)")

        # --- Los mensajes, en el orden de siempre ----------------------------
        mensajes = [{"role": "system", "content": prompt_sistema}]
        for pieza in elegidas:
            if pieza.origen == "memoria":
                # Memoria persistente: va marcada como dato, no como orden, para
                # que un recuerdo con "ignora lo anterior" no se lea como
                # instrucción. Es la protección contra inyección de prompt.
                mensajes.append({"role": "user", "content": pieza.texto})
            elif pieza.origen in ("recuerdos", "documentos"):
                mensajes.append({"role": "user",
                                 "content": f"[{pieza.origen}: {pieza.motivo}]: {pieza.texto}"})
            else:
                mensajes.append({"role": "user", "content": pieza.texto})
        for m in (recent_messages or []):
            mensajes.append({"role": m.get("role", "user"),
                             "content": m.get("content", "")})
        if ancla and not any(p.origen == "hilo" for p in elegidas):
            mensajes.append({"role": "user", "content": ancla})
        # El ULTIMO mensaje es siempre la pregunta del señor. El prompt de
        # sistema va el primero y no se repite aquí: mandar el prompt de sistema
        # como si fuera la pregunta hacía que el modelo contestara a sus propias
        # instrucciones en vez de a lo que le habían preguntado.
        mensajes.append({"role": "user", "content": prompt_usuario or texto})

        contexto = Contexto(
            mensajes=mensajes, piezas=elegidas, descartado=fuera,
            presupuesto={"usado": gastado, "limite": limite},
            situacion={"voz": voz, "rapido": rapido, "input_mode": input_mode,
                       "mision": mission_id or (self._mision_activa_id() or "")},
            duracion_ms=(time.time() - inicio) * 1000,
        )
        if emit is not None:
            try:
                emit({"type": "context.built", "piezas": len(contexto.piezas),
                      "caracteres": gastado, "descartado": len(fuera),
                      "voz": voz, "rapido": rapido})
            except Exception as exc:  # noqa: BLE001 - el bus no rompe el contexto
                log.debug("context.built no se pudo publicar: %s", exc)
        return contexto

    # ------------------------------------------------------------------ #
    # Fuentes (cada una es opcional y nunca lanza)
    # ------------------------------------------------------------------ #
    def _memoria(self, texto: str) -> Optional[Pieza]:
        if not getattr(Config, "MEMORY_ENABLED", True):
            return None
        try:
            from memory.manager import para_el_prompt

            bloque = para_el_prompt(texto) or ""
        except Exception as exc:  # noqa: BLE001
            log.debug("memoria persistente no disponible: %s", exc)
            return None
        if not bloque.strip():
            return None
        return Pieza("memoria", bloque.strip(), 3,
                     "lo que JARVIS recuerda de ti sobre esto")

    def _recuerdos(self, texto: str, conjunto: bool) -> Optional[Pieza]:
        if not getattr(Config, "RECALL_ENABLED", True):
            return None
        try:
            # OJO: `semantic_memory` NO es un modulo suelto. Es una INSTANCIA
            # creada en memory/semantic.py. Importarlo como `import
            # semantic_memory` falla con ModuleNotFoundError y, como el fallo
            # estaba dentro del try, la memoria semantica se descartaba en
            # silencio: el modelo nunca recibia un recuerdo.
            from memory.semantic import semantic_memory

            encontrados = semantic_memory.recall_context(
                texto, limit=1 if conjunto else 2) or []
        except Exception as exc:  # noqa: BLE001
            log.debug("recuerdos semanticos no disponibles: %s", exc)
            return None
        if not encontrados:
            return None
        cuerpo = "\n".join(str(r.get("content") or r) for r in encontrados[:3])
        return Pieza("recuerdos", cuerpo, 2, "parecidos a lo que se está hablando")

    def _documentos(self, texto: str, conjunto: bool) -> Optional[Pieza]:
        if not PIDE_DOCUMENTOS.search(texto):
            return None
        try:
            import library as lib

            if not lib.stats().get("files", 0):
                return None
            hits = lib.search_docs(texto, limit=2 if conjunto else 3) or []
        except Exception as exc:  # noqa: BLE001
            log.debug("documentos no disponibles: %s", exc)
            return None
        if not hits:
            return None
        cuerpo = "\n".join(f"{h.get('name')}: {h.get('text')}" for h in hits[:3])
        return Pieza("documentos", cuerpo, 2, "preguntaste por tus documentos")

    def _mision_activa_id(self) -> str:
        try:
            from orchestrator.manager import get_manager

            for tarea in get_manager().listar(6):
                if tarea.get("status") in ("RUNNING", "TESTING", "WAITING_PERMISSION",
                                           "WAITING_USER", "ANALYZING", "PLANNING"):
                    return str(tarea.get("id") or "")
        except Exception:  # noqa: BLE001
            pass
        return ""

    def _mision(self, texto: str, mission_id: str) -> Optional[Pieza]:
        """La misión en marcha, solo si el señor pregunta de ella."""
        if not (mission_id or PIDE_MISION.search(texto)):
            return None
        try:
            from orchestrator.manager import get_manager

            gestor = get_manager()
            lista = gestor.listar(4)
            if mission_id:
                lista = [t for t in lista if t.get("id") == mission_id] or lista
            vivo = next((t for t in lista if t.get("status") in
                         ("RUNNING", "TESTING", "WAITING_PERMISSION",
                          "WAITING_USER", "ANALYZING", "PLANNING")), None)
            tarea = vivo or (lista[0] if lista else None)
            if not tarea:
                return None
            detalle = gestor.obtener(tarea["id"]) or {}
            pasos = (detalle.get("plan") or {}).get("pasos") or []
            titulo = (detalle.get("plan") or {}).get("objetivo") or tarea.get("idea", "")
            cuerpo = (f"MISIÓN {tarea['id']} · {tarea.get('status')}\n"
                      f"Objetivo: {str(titulo)[:220]}\n"
                      f"Pasos del plan: {len(pasos)}"
                      f" · cambios: {len(detalle.get('changes') or [])}"
                      f" · preguntas pendientes: {detalle.get('questions_pending', 0)}")
            return Pieza("mision", cuerpo, 3, "hay una misión en marcha y preguntas de ella")
        except Exception as exc:  # noqa: BLE001
            log.debug("mision activa no disponible: %s", exc)
            return None

    def _decisiones(self, texto: str, conjunto: bool) -> Optional[Pieza]:
        if not PIDE_DECISION.search(texto) or conjunto:
            return None
        try:
            from command import store

            filas = store.listar_decisiones(3) or []
        except Exception:  # noqa: BLE001
            return None
        if not filas:
            return None
        cuerpo = "\n".join(f"- {f.get('chosen', '')}"
                           + (f" ({f.get('reason', '')})" if f.get("reason") else "")
                           for f in filas[:3])
        return Pieza("decisiones", cuerpo, 1,
                     "preguntaste por qué se decidió algo")

    def _sistema(self) -> Optional[Pieza]:
        try:
            from command import centers

            salud = centers.salud()
            if not salud.get("subsistemas"):
                return None
            malos = [s for s in salud["subsistemas"]
                     if s.get("estado") in ("down", "degraded")]
            cuerpo = (f"ESTADO DEL SISTEMA: {salud.get('estado')} "
                      f"({salud.get('cuentas')})")
            if malos:
                cuerpo += "\nCaídos o degradados: " + ", ".join(
                    f"{s['nombre']} ({s['estado']})" for s in malos[:4])
            else:
                cuerpo += "\nTodos los subsistemas comprobados están bien."
            return Pieza("sistema", cuerpo, 1, "preguntaste cómo está JARVIS")
        except Exception as exc:  # noqa: BLE001
            log.debug("estado del sistema no disponible: %s", exc)
            return None


# --------------------------------------------------------------------------- #
_instancia: Optional[ContextEngine] = None


def motor() -> ContextEngine:
    global _instancia
    if _instancia is None:
        _instancia = ContextEngine()
    return _instancia


def construir(mensaje: str, **kwargs) -> Contexto:
    """Atajo: construye el contexto con el motor único."""
    return motor().construir(mensaje, **kwargs)