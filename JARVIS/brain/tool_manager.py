"""Tool Manager (sección 12.3 de la especificación).

El LLM nunca llama a un handler directo: propone {"tool": ..., "parameters": {...}},
el orchestrator valida contra el Risk Engine, y solo entonces se ejecuta.
"""
import asyncio
import logging
import os
from typing import Any, Awaitable, Callable

from risk_engine import RiskEngine

log = logging.getLogger("tool_manager")

# Herramientas que se describen siempre, las de un solo uso, y el conjunto de
# nombres que se usan como base para la eleccion por relevancia.
_TRABAJO_UNA_VEZ: set = set()


def _norm_tokens(texto: str) -> list:
    """Palabras de un texto, sin tildes, para ver si encaja con la peticion."""
    import re
    import unicodedata as _ud
    t = _ud.normalize("NFKD", (texto or "").lower())
    t = "".join(c for c in t if not _ud.combining(c))
    return [p for p in re.findall(r"[a-z0-9]{3,}", t)]


class ToolManager:
    def __init__(self, risk_engine: RiskEngine):
        self.risk = risk_engine
        self._registry: dict[str, dict] = {}
        self._no_disponibles: list[dict] = []
        self._register_defaults()

    def register(self, tool: dict) -> None:
        """tool = {"name","risk_level","requires_confirmation","parameters",
                  "confirm_text","handler"}

        Las que no pueden funcionar en este sistema se registran igualmente,
        para que /api/tools no cambie de forma entre el escritorio y un NAS,
        pero se marcan. El modelo las ve en el catalogo y no las prueba.
        """
        self._registry[tool["name"]] = tool

    # Herramientas que en Linux no tienen sentido: dependen de la API de
    # Windows, de VirtualBox o de un escritorio. NO son peso muerto en el
    # escritorio, asi que no se quitan: se declaran para que el router sepa
    # que estan ahi pero que no puede pasar el rato llamando a algo que
    # siempre va a fallar.
    #
    # Verificado dentro del contenedor, no de memoria: son 14 de 109. Las
    # otras 95 contestan con elegancia ("Campus sin credenciales", "no hay
    # telefonopaired") y no molestan.
    _SOLO_WINDOWS = {
        # Gestion de ventanas: la hace la API de Windows, no hay equivalente.
        "minimize_window", "maximize_window", "switch_window",
        # Teclas multimedia: nircamds de Windows.
        "spotify_play", "spotify_next", "spotify_prev",
        # Defender y auditorias que lanzan PowerShell.
        "defender_scan", "defender_threats", "defender_quarantine",
        "win_updates", "perf_advisor", "sec_audit",
        # VirtualBox entero.
        "vbox_list", "vbox_info", "vbox_ip", "vbox_net", "vbox_start",
        "vbox_stop", "vbox_create", "vbox_exec", "vbox_read_file",
        "vbox_write_file", "vbox_unlock", "vbox_console", "vbox_type",
    }
    # Estas no son de Windows pero tampoco pueden funcionar en un contenedor
    # sin webcam: abren PowerShell para enumerar dispositivos DirectShow.
    _SIN_CAMARA = {"camara_estado", "camara_reparar"}

    def disponibles(self) -> list[dict]:
        """Lo que el modelo puede usar de verdad en esta máquina."""
        return [t for n, t in self._registry.items()
                if n not in self._solo_windows()]

    def no_disponibles(self) -> list[dict]:
        return [dict(t, motivo=motivo)
                for n, t in self._registry.items()
                for motivo in [self._motivo(n)] if motivo]

    def _solo_windows(self) -> set[str]:
        # En Windows no se filtra nada: todo lo de la lista funciona. Solo en
        # Linux (NAS, servidor) se retiran del catalogo las que no pueden.
        if os.name == "nt":
            return set()
        return self._SOLO_WINDOWS | self._SIN_CAMARA

    def _motivo(self, nombre: str) -> str:
        if nombre in self._SOLO_WINDOWS:
            return "Solo funciona en Windows."
        if nombre in self._SIN_CAMARA:
            return "Necesita una cámara y un escritorio; en un servidor no hay."
        return ""

    def _register_defaults(self) -> None:
        try:
            from tools import system_tools, web_tools, virtualbox_tools, campus_tools, raspberry_tools, code_tools, syshealth_tools, auto_tools, git_tools, translate_tools, opencode_tools, dev_task_tools, diag_tools, install_tools, github_tools, mobile_tools, camera_tools, memory_tools
            all_tools = (system_tools.TOOLS + system_tools.WINDOW_TOOLS +
                         system_tools.MEDIA_TOOLS + system_tools.NAV_TOOLS +
                         virtualbox_tools.VB_TOOLS +
                         campus_tools.CAMPUS_TOOLS +
                         raspberry_tools.RASPBERRY_TOOLS +
                         code_tools.CODE_TOOLS +
                         syshealth_tools.HEALTH_TOOLS +
                         auto_tools.AUTO_TOOLS +
                         git_tools.GIT_TOOLS +
                         translate_tools.TRANSLATE_TOOLS +
                         opencode_tools.OCODE_TOOLS +
                         dev_task_tools.DEV_TOOLS +
                         diag_tools.DIAG_TOOLS +
                         install_tools.INSTALL_TOOLS +
                         github_tools.TOOLS +
                         mobile_tools.MOBILE_TOOLS +
                         camera_tools.CAMERA_TOOLS +
                         memory_tools.MEMORY_TOOLS +
                         system_tools.danger_tools() +
                         system_tools.FILE_TOOLS +
                         web_tools.TOOLS)
            try:
                from tools import custom_tools as _custom
                all_tools = list(all_tools) + list(getattr(_custom, "CUSTOM_TOOLS", []) or [])
            except Exception as e:
                log.debug("custom_tools no cargadas: %s", e)
            for t in all_tools:
                if t["name"] not in self._registry:
                    self.register(t)
            log.info("ToolManager: %d herramientas registradas", len(self._registry))
        except Exception as e:
            log.error("Error registrando herramientas: %s", e)
            import traceback
            traceback.print_exc()

    def reload_custom(self) -> int:
        """Recarga en caliente las herramientas creadas por la autocorrección."""
        try:
            import importlib
            from tools import custom_tools as _custom
            importlib.reload(_custom)
            n = 0
            for t in list(getattr(_custom, "CUSTOM_TOOLS", []) or []):
                if t.get("name") and t["name"] not in self._registry:
                    self.register(t)
                    n += 1
            log.info("ToolManager: %d custom tools nuevas", n)
            return n
        except Exception as e:
            log.warning("reload_custom: %s", e)
            return 0

    def list_tools(self) -> list[dict]:
        return [{"name": t["name"], "risk_level": t.get("risk_level", "low"),
                 "requires_confirmation": bool(t.get("requires_confirmation")),
                 "parameters": t.get("parameters") or {}}
                for t in self._registry.values()]

    def _herramientas_de_una_vez(self) -> set:
        global _TRABAJO_UNA_VEZ
        if _TRABAJO_UNA_VEZ:
            return _TRABAJO_UNA_VEZ
        base = {"open_app", "open_file", "web_search", "web_fetch", "say",
                "analizar_archivo", "campus_courses", "campus_notas",
                "system_diagnose", "vbox_list", "memory_search"}
        # En un servidor, el trabajo de verdad es sobre ficheros, asi que
        # list_dir entra siempre en el catalogo. Y open_app / vbox_list salen:
        # abren cosas de Windows y no tienen sentido aqui, y lo que pasaba es
        # que el modelo las elegia en vez de mirar el NAS, y contestaba con
        # "Sin carpetas de apuntes, senor" cuando le preguntaban por /mnt/nas.
        if self._solo_windows():
            base = (base | {"list_dir"}) - {"open_app", "open_file", "vbox_list"}
        got = set()
        for t in self._registry.values():
            d = ((t.get("description") or "") + " " + t.get("name", "")).lower()
            if "solo" in d and "una" in d:
                got.add(t.get("name", ""))
        _TRABAJO_UNA_VEZ = got | base
        return _TRABAJO_UNA_VEZ

    def describe_for_prompt(self, consulta: str = "") -> str:
        """Las herramientas, pero solo las que el señor puede necesitar.

        Antes iban las 108 enteras, unas 3.700 palabras, siempre. Con eso el
        prompt se iba de 8.000 tokens y Groq lo rechazaba entero: la petición
        fallaba y la respuesta llegaba tarde y de otro proveedor, que es
        justamente lo que hacia que perdiera el hilo.

        Ahora van siempre los nombres (baratos) y la descripción completa
        solo de las que de verdad encajan con lo que acaba de decir.
        """
        registro = list(self.disponibles()) or list(self._registry.values())
        if not registro:
            return "sin herramientas"
        q = _norm_tokens(consulta)
        una_vez = self._herramientas_de_una_vez()
        relevantes, resto = set(), []
        for t in registro:
            nombre = t.get("name", "")
            # Sin consulta solo van las de un uso; con consulta, todas y el
            # filtro de relevancia se hace despues.
            if nombre in una_vez:
                relevantes.add(nombre)
            else:
                resto.append(t)
        if q:
            tokens_q = set(q)
            for t in resto:
                texto = _norm_tokens(f"{t.get('name','')} "
                                     f"{t.get('description','')} "
                                     f"{' '.join((t.get('parameters') or {}).keys())}")
                if tokens_q and len(tokens_q & set(texto)) >= 1:
                    relevantes.add(t.get("name", ""))
        lineas, otros = [], []
        for t in registro:
            nombre = t.get("name", "")
            desc = (t.get("description") or "").strip()
            if not desc:
                continue
            if nombre in relevantes:
                p = ", ".join(f"{k}: {v}"
                              for k, v in (t.get("parameters") or {}).items())
                riesgo = t.get("risk_level", "low")
                lineas.append(f"- {nombre}({p}) [riesgo: {riesgo}] :: {desc}")
            else:
                # Las que no encajan van solo con el nombre. Con la descripción
                # entera sumaban casi 8.000 caracteres y el proveedor rechazaba
                # la petición entera.
                otros.append(nombre)
        if otros:
            lineas.append("Otras herramientas (solo si de verdad hacen falta): "
                          + ", ".join(otros))
        return "\n".join(lineas)

    def execute_now_safe(self, name: str, params: dict) -> dict:
        """Versión bloqueante para lanzar desde hilos sin event loop activo."""
        loop = asyncio.new_event_loop()
        try:
            return loop.run_until_complete(self.execute(name, params))
        finally:
            loop.close()

    async def execute(self, name: str, params: dict) -> dict:
        """Ejecuta una herramienta pasando por el Risk Engine. Devuelve dict de resultado."""
        tool = self._registry.get(name)
        if not tool:
            self.risk.emit({"type": "action_status", "label": f"Herramienta {name}",
                            "state": "failed"})
            try:
                from memory import selfheal as _sh
                _sh.report_error("missing_tool", f"No tengo herramienta: {name}",
                                 f"params={str(params)[:300]}")
                _sh.fix_now()
            except Exception as exc:  # noqa: BLE001
                # El fallo ya se le dice a quien lo pidió (return de abajo); esto
                # es solo el diagnóstico extra. Si tampoco funciona, se anota:
                # si no, una herramienta que desaparece deja de ser rastreable.
                log.debug("autorreparacion no pudo registrar %s: %s", name, exc)
            return {"status": "failed", "message": f"No conozco esa herramienta: {name}"}

        self.risk.emit({"type": "action_status",
                        "label": tool.get("label", name), "state": "running"})

        ok = await self.risk.check(tool, params)
        if not ok:
            self.risk.emit({"type": "action_status",
                            "label": tool.get("label", name), "state": "cancelled"})
            db_audit = _record(tool, params, confirmed=False, result="cancelled")
            return {"status": "cancelled", "message": "Cancelado (sin confirmación)."}

        handler: Callable = tool.get("handler")
        try:
            if asyncio.iscoroutinefunction(handler):
                result = await handler(params)
            else:
                result = handler(params)
            self.risk.emit({"type": "action_status",
                            "label": tool.get("label", name), "state": "done"})
            _record(tool, params, confirmed=True, result="success", details=str(result)[:500])
            return {"status": "success", "result": result}
        except Exception as e:
            log.exception("Herramienta %s falló", name)
            self.risk.emit({"type": "action_status",
                            "label": tool.get("label", name), "state": "failed"})
            _record(tool, params, confirmed=True, result="failed", details=str(e))
            try:
                from memory import selfheal as _sh
                _sh.report_error(f"tool:{name}", e,
                                 f"params={str(params)[:300]}")
                _sh.fix_now()
            except Exception as exc:  # noqa: BLE001
                log.debug("autorreparacion no pudo registrar el fallo de %s: %s",
                          name, exc)
            return {"status": "failed", "message": str(e)}


def _record(tool: dict, params: dict, confirmed, result, details=""):
    from memory import sqlite_store as db
    risk = tool.get("risk_level") or "low"
    return db.add_audit(tool.get("name", "?"), params, risk, confirmed, result, details)