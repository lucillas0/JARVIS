"""Tool Manager (sección 12.3 de la especificación).

El LLM nunca llama a un handler directo: propone {"tool": ..., "parameters": {...}},
el orchestrator valida contra el Risk Engine, y solo entonces se ejecuta.
"""
import asyncio
import logging
from typing import Any, Awaitable, Callable

from risk_engine import RiskEngine

log = logging.getLogger("tool_manager")


class ToolManager:
    def __init__(self, risk_engine: RiskEngine):
        self.risk = risk_engine
        self._registry: dict[str, dict] = {}
        self._register_defaults()

    def register(self, tool: dict) -> None:
        """tool = {"name","risk_level","requires_confirmation","parameters",
                  "confirm_text","handler"}"""
        self._registry[tool["name"]] = tool

    def _register_defaults(self) -> None:
        try:
            from tools import system_tools, web_tools, virtualbox_tools, campus_tools, raspberry_tools, code_tools, syshealth_tools, auto_tools, git_tools, translate_tools, opencode_tools
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
                         system_tools.danger_tools() +
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
        return [{"name": t["name"], "risk_level": t["risk_level"],
                 "requires_confirmation": t["requires_confirmation"],
                 "parameters": t["parameters"]}
                for t in self._registry.values()]

    def describe_for_prompt(self) -> str:
        lines = []
        for t in self._registry.values():
            params = ", ".join(f"{k}: {v}" for k, v in t["parameters"].items())
            lines.append(f"- {t['name']}({params}) [riesgo: {t['risk_level']}] :: "
                         f"{t.get('description', '')}")
        return "\n".join(lines)

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
            except Exception:
                pass
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
            except Exception:
                pass
            return {"status": "failed", "message": str(e)}


def _record(tool: dict, params: dict, confirmed, result, details=""):
    from memory import sqlite_store as db
    risk = tool.get("risk_level") or "low"
    return db.add_audit(tool.get("name", "?"), params, risk, confirmed, result, details)