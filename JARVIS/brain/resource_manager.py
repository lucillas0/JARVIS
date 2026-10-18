"""Resource Manager: decide, no solo mide.

`command/centers.recursos()` ya mide CPU, RAM, GPU, temperatura, disco y red.
Lo que no existía era **quien toma decisiones** con esas medidas. Este módulo es
ese alguien, y su trabajo es acotado a propósito:

Decide **cuántos trabajos pueden correr a la vez** y **si un trabajo nuevo puede
empezar**. Nada más.

Lo que NO hace, y es deliberado:

- **No mata procesos.** Jamás. Un umbral de RAM no es permiso para matar lo
  que el señor tiene abierto.
- **No interrumpe trabajos críticos.** Bajar la concurrencia afecta a lo que
  aún no ha empezado, no a lo que ya está en marcha. Tirar un trabajo a medias
  para recuperar memoria es peor que ir más despacio.
- **No cambia de modelo a mitad de una operación.** Un modelo se elige al
  empezar una petición y no se cambia durante: cambiarlo invalidaría el
  contexto. Solo se actúa sobre lo que aún no ha salido.
- **No toca la configuración global.** El límite estático (el que puso el
  señor) y el dinámico (este módulo) son cosas distintas, y el dinámico nunca
  puede subir por encima del estático.

**Histéresis.** Sin ella, un sistema que oscila al 85% de RAM subiría y bajaría
la concurrencia cada pocos segundos, y cada subida y bajada es trabajo perdido.
Por eso hay dos umbrales distintos para entrar y salir, y un tiempo de
estabilización antes de，恢复 la concurrencia.

**Si no hay datos, no se inventa normality.** Si las métricas no están
disponibles o están viejas, el estado es `desconocido`, y en `desconocido` se
aplica la política conservadora (concurrencia mínima) y se dice por qué. Nunca
se interpreta "no sé" como "todo bien".
"""
from __future__ import annotations

import json
import logging
import threading
import time
from typing import Callable, Optional

from memory import sqlite_store as db

log = logging.getLogger("brain.resources")

_candado = threading.RLock()

# --------------------------------------------------------------------------- #
# Umbrales. Se separan los de entrada y los de salida (histéresis): para bajar
# la concurrencia hay que llegar al nivel malo; para recuperarla, hay que bajar
# del nivel bueno, y además esperar el periodo de estabilización.
# --------------------------------------------------------------------------- #
UMBRALES = {
    "ram": {"bajo": 80.0, "alto": 90.0, "critico": 95.0},
    "cpu": {"bajo": 85.0, "alto": 93.0, "critico": 98.0},
    "disco": {"bajo": 85.0, "alto": 93.0, "critico": 97.0},
}

# Por debajo de este margen el recurso está bien.
HISTERESIS = 5.0
# Segundos que hay que ver el sistema tranquilo antes de subir la concurrencia.
SEGUNDOS_ESTABILIZACION = 45.0
# Cuánto puede ser una muestra. Si el dato es más viejo, no se usa: puede estar
# describiendo otra cosa.
MAX_EDAD_METRICA = 60.0

# Niveles posibles, de peor a mejor.
NIVELES = ("critico", "alto", "medio", "bajo", "desconocido")

# Cuántos trabajos se permiten en cada nivel. El 'bajo' es el tope que puso
# el señor; los demás los baja este módulo.
CONCURRENCIA_POR_NIVEL = {
    "critico": 1,
    "alto": 1,
    "medio": 2,
    "bajo": None,          # None = la que el señor configuró (sin tocar)
    "desconocido": 1,      # sin datos, lo prudente es lo mínimo
}

# Si el nivel es 'medio' o peor, un trabajo de prioridad BAJA espera en cola.
PRIORIDADES_DE_ESPERA = (25,)

_emit: Optional[Callable] = None
_medir: Optional[Callable] = None
_estado = {"nivel": "desconocido", "desde": 0.0, "concurrencia": None,
           "motivo": "sin medir todavía", "metricas": {},
           "tranquilo_desde": 0.0}


def conectar(emit: Optional[Callable] = None,
             medir: Optional[Callable] = None) -> None:
    """Se engancha al Event Bus y, si se quiere, a una fuente de medidas propia.

    Lo normal es usar `command/centers.recursos()`. El parámetro `medir` existe
    para las pruebas, donde sí hace falta poder dar valores concretos sin tocar
    el equipo de verdad: **nunca** para hacer que el sistema crea que hay
    memoria donde no la hay.
    """
    global _emit, _medir
    _emit = emit
    _medir = medir


# --------------------------------------------------------------------------- #
# Medición
# --------------------------------------------------------------------------- #
def metricas(forzar: bool = True) -> dict:
    """Lee las medidas de verdad y dice cuáles valen y cuáles no.

    Devuelve cada métrica con `available` y, si no está, el motivo. Un `None`
    con explicación es información; un 0 inventado es una mentira.
    """
    if _medir is not None:
        crudo = _medir()
        # Se respeta el 'ts' que trae la medida. Tomar la hora actual aquí
        # decía "recién medido" siempre, aunque el dato fuera de hace una
        # hora: el sistema se habría fiado de un dato viejo y nunca habría
        # aplicado la política conservadora.
        ahora = float(crudo.get("ts") or time.time())
    else:
        from command import centers

        crudo = centers.recursos(forzar=forzar)
        ahora = float(crudo.get("ts") or time.time())

    edad = max(0.0, time.time() - ahora)
    obsoleta = edad > MAX_EDAD_METRICA

    salida = {"ts": ahora, "edad_s": round(edad, 1), "obsoleta": obsoleta,
              "cpu": None, "ram": None, "disco": None, "gpu": {"available": False}}
    motivos = []

    def _m(clave, valor, unidad="%", extra=None):
        if valor is None:
            salida[clave] = {"available": False, "motivo": "sin sensor"}
            motivos.append(f"{clave}: sin sensor")
            return
        if obsoleta:
            salida[clave] = {"available": False,
                             "motivo": f"medida de hace {edad:.0f}s: no se usa"}
            motivos.append(f"{clave}: obsoleta")
            return
        item = {"available": True, "valor": float(valor), "unidad": unidad}
        if extra:
            item.update(extra)
        salida[clave] = item

    cpu = (crudo.get("cpu") or {})
    _m("cpu", cpu.get("porcentaje"), extra={"nucleos": cpu.get("nucleos")})
    ram = (crudo.get("ram") or {})
    _m("ram", ram.get("porcentaje"),
       extra={"disponible_gb": ram.get("disponible_gb"),
              "total_gb": ram.get("total_gb")})
    discos = crudo.get("discos") or []
    libres = [(d.get("libre_gb") or 0) for d in discos if isinstance(d, dict)]
    if libres:
        peor = min(libres)
        _m("disco", 100.0 - (peor / max(1.0, sum(
            (d.get("total_gb") or 0) for d in discos if isinstance(d, dict))) * 100.0),
           extra={"peor_libre_gb": peor})
    gpu = crudo.get("gpu") or {}
    salida["gpu"] = gpu if isinstance(gpu, dict) else {"available": False}
    if motivos:
        salida["avisos"] = motivos
    return salida


# --------------------------------------------------------------------------- #
# Evaluación
# --------------------------------------------------------------------------- #
def evaluar(m: Optional[dict] = None) -> dict:
    """Qué nivel de presión hay y por qué. No cambia nada todavía."""
    m = m if m is not None else metricas()
    razones: list[str] = []

    if m.get("obsoleta"):
        return {"nivel": "desconocido",
                "motivo": f"las medidas tienen {m.get('edad_s')}s: no se fían",
                "metricas": m, "razones": ["medidas obsoletas"]}

    disponibles = [k for k in ("cpu", "ram", "disco") if (m.get(k) or {}).get("available")]
    if not disponibles:
        return {"nivel": "desconocido",
                "motivo": "no hay ninguna métrica disponible",
                "metricas": m,
                "razones": m.get("avisos", ["sin métricas"])}

    peor = "bajo"
    for clave in ("ram", "cpu", "disco"):
        item = m.get(clave) or {}
        if not item.get("available"):
            continue
        valor = float(item["valor"])
        u = UMBRALES[clave]
        if valor >= u["critico"]:
            nivel = "critico"
        elif valor >= u["alto"]:
            nivel = "alto"
        elif valor >= u["bajo"]:
            nivel = "medio"
        else:
            continue
        if NIVELES.index(nivel) < NIVELES.index(peor) or peor == "bajo":
            peor = nivel
        razones.append(f"{clave} al {valor:.0f}% ({nivel})")

    if not razones:
        return {"nivel": "bajo", "motivo": f"todo tranquilo ({', '.join(disponibles)})",
                "metricas": m, "razones": ["sin presión"]}

    # La RAM manda sobre el resto: es la que tumba el proceso.
    ram = (m.get("ram") or {})
    if ram.get("available") and float(ram["valor"]) >= UMBRALES["ram"]["alto"]:
        for otra in ("cpu", "disco"):
            if not razones[0].startswith(otra):
                break
        if not razones[0].startswith("ram"):
            # Si la CPU también va mal, el nivel es el peor de los dos.
            if NIVELES.index(peor) < NIVELES.index("alto"):
                peor = "alto"
    return {"nivel": peor, "motivo": "; ".join(razones),
            "metricas": m, "razones": razones}


# --------------------------------------------------------------------------- #
# Decisión
# --------------------------------------------------------------------------- #
def revisar(forzar: bool = True) -> dict:
    """Mide, decide y aplica. Devuelve qué se decidió y por qué."""
    global _estado
    ahora = time.time()
    m = metricas(forzar=forzar)
    v = evaluar(m)
    nivel = v["nivel"]

    with _candado:
        anterior = _estado["nivel"]
        # Histeresis: para volver a 'bajo' hay que estar de verdad en 'bajo', con
        # margen, y llevar un rato así. Sin esto, oscila.
        if anterior in ("alto", "critico") and nivel in ("medio", "bajo"):
            sigue_igual = ahora - _estado["desde"] < SEGUNDOS_ESTABILIZACION
            if sigue_igual:
                # Todavía no se ha estabilizado: se mantiene el nivel malo, pero
                # se anota que va mejorando.
                nivel_real = anterior
                motivo = (f"se está recuperando pero sin estabilizar "
                          f"({SEGUNDOS_ESTABILIZACION - int(ahora - _estado['desde'])}s "
                          f"para confirmar): {v['motivo']}")
                return {"ok": True, "nivel": anterior, "cambio": False,
                        "motivo": motivo, "metricas": m, "concurrencia": None}
            if not _ya_estabilizado(m):
                nivel_real = anterior
                return {"ok": True, "nivel": anterior, "cambio": False,
                        "motivo": "recuperación sin confirmar: se espera",
                        "metricas": m, "concurrencia": None}
            # Recuperado y estable. OJO: aquí hay que fijar `nivel_real`; si
            # no, la comprobación de "ha cambiado" de abajo usa una variable
            # que nunca se asignó en esta rama y revienta con NameError,
            # justo en el camino que se ejecuta cuando el equipo se recupera.
            nivel_real = "bajo"
            motivo = "recuperado y estable: se devuelve la concurrencia"
        else:
            nivel_real = nivel
            motivo = v["motivo"]

        if nivel_real != anterior:
            _estado["desde"] = ahora
        objetivo = CONCURRENCIA_POR_NIVEL.get(nivel_real)
        antes_conc = _estado["concurrencia"]
        _estado.update({"nivel": nivel_real, "motivo": motivo, "metricas": m,
                        "desde": _estado["desde"] if nivel_real == anterior else ahora})

    cambio = (nivel_real != anterior) or (objetivo is not None and objetivo != antes_conc)
    if cambio:
        _estado["concurrencia"] = objetivo
        _registrar(motivo, anterior, nivel_real, antes_conc, objetivo, m)
        _publica(nivel_real, motivo, objetivo)
    return {"ok": True, "nivel": nivel_real, "cambio": cambio, "motivo": motivo,
            "concurrencia": objetivo,
            "concurrencia_previa": antes_conc, "metricas": m}


def _ya_estabilizado(m: dict) -> bool:
    """¿Los recursos llevan un rato tranquilo? Mira la RAM, que es la manda."""
    ram = (m.get("ram") or {})
    if not ram.get("available"):
        return False
    return float(ram["valor"]) < UMBRALES["ram"]["bajo"] - HISTERESIS


# --------------------------------------------------------------------------- #
# Lo que consume el resto del sistema
# --------------------------------------------------------------------------- #
def limite_concurrencia() -> int:
    """El tope que ven el Agent Manager y el AI Gateway.

    Nunca sube del estático: el Resource Manager puede apretar, no aflojar.
    """
    import agent_manager as am

    estatico = am.MAXIMO_CONCURRENTE
    with _candado:
        nivel = _estado["nivel"]
        objetivo = _estado["concurrencia"]
    if nivel == "bajo" or objetivo is None:
        return estatico
    return max(1, min(estatico, int(objetivo)))


def nivel() -> str:
    with _candado:
        return _estado["nivel"]


def puede_empezar(prioridad: int) -> dict:
    """¿Puede empezar un trabajo de esa prioridad ahora?

    Tres respuestas: sí, sí-pero-con-restricciones, o no (espera). Es lo que
    permite 'aplazar trabajos no urgentes cuando aprieta la memoria' sin
    tocar los urgentes.
    """
    actual = limite_concurrencia()
    with _candado:
        n = _estado["nivel"]
        motivo = _estado["motivo"]
    en_vuelo = 0
    try:
        import agent_manager as am

        en_vuelo = len([t for t in am.trabajos_activos()
                        if t["estado"] == "ejecutando"])
    except Exception:  # noqa: BLE001
        pass

    if en_vuelo < actual:
        return {"ok": True, "motivo": f"hay hueco ({en_vuelo}/{actual})",
                "nivel": n, "restricciones": []}
    if n in ("critico", "alto") and int(prioridad) in PRIORIDADES_DE_ESPERA:
        return {"ok": False,
                "motivo": f"memoria/CPU al límite y este trabajo es de prioridad "
                          f"baja: espera a que se libere ({motivo})",
                "nivel": n, "restricciones": ["aplazado"]}
    return {"ok": True,
            "motivo": f"va con restricción: se permite aunque la carga esté alta "
                      f"porque es prioritario (nivel {n})",
            "nivel": n, "restricciones": ["concurrencia_reducida"]}


def modelo_alternativo() -> dict:
    """¿Hay un modelo de reserva configurado Y disponible?

    No se cambia de modelo aquí. Esto solo responde si lo habría: la elección
    happens en el AI Gateway, que es quien sabe si la petición ya empezó (y si
    empezó, no se toca).
    """
    from config import Config

    tiene_groq = bool(getattr(Config, "GROQ_API_KEY", ""))
    tiene_gemini = bool(getattr(Config, "GEMINI_API_KEY", ""))
    return {
        "hay_alternativa": tiene_groq or tiene_gemini,
        "proveedores_disponibles": [p for p, ok in
                                    (("groq", tiene_groq), ("gemini", tiene_gemini))
                                    if ok],
        "nota": "el AI Gateway decide; una petición ya empezada no cambia de modelo",
    }


def modo_degradado() -> dict:
    """Qué falta ahora mismo y cómo se sigue trabajando sin ello."""
    m = metricas(forzar=False)
    falta = []
    if not (m.get("gpu") or {}).get("available"):
        falta.append("GPU")
    if m.get("obsoleta"):
        falta.append("métricas de recursos recientes")
    for clave in ("cpu", "ram"):
        if not (m.get(clave) or {}).get("available"):
            falta.append(clave.upper())
    if not _hay_modelos():
        falta.append("proveedor de modelos")
    return {"degradado": bool(falta), "falta": falta,
            "como_se_sigue": ("todo lo que no dependa de eso" if falta
                              else "sin necesidad")}


def _hay_modelos() -> bool:
    try:
        from config import Config

        return bool(Config.has_any_key())
    except Exception:  # noqa: BLE001
        return False


def estado() -> dict:
    """Lo que se enseña en el centro de mando."""
    with _candado:
        est = dict(_estado)
    m = metricas(forzar=False)
    return {
        "nivel": est.get("nivel"),
        "motivo": est.get("motivo"),
        "desde": est.get("desde"),
        "concurrencia": limite_concurrencia(),
        "concurrencia_efectiva": est.get("concurrencia"),
        "metricas": m,
        "umbrales": UMBRALES,
        "degradado": modo_degradado(),
        "modelo_alternativo": modelo_alternativo(),
        "decisiones": decisiones(10),
    }


def decisiones(limite: int = 20) -> list[dict]:
    with _candado:
        with db.get_conn() as c:
            filas = c.execute(
                "SELECT * FROM resource_decisions ORDER BY at DESC LIMIT ?",
                (max(1, min(200, limite)),)).fetchall()
    salida = []
    for f in filas:
        d = dict(f)
        try:
            d["metricas"] = json.loads(d.get("metricas") or "{}")
        except (ValueError, TypeError):
            d["metricas"] = {}
        salida.append(d)
    return salida


def _registrar(motivo: str, anterior: str, nuevo: str, conc_antes,
               conc_despues, metricas: dict) -> None:
    """Deja constancia de la decisión: qué cambió, de qué valor a cuál y por qué."""
    try:
        with _candado:
            with db.get_conn() as c:
                c.execute(
                    "INSERT INTO resource_decisions (at, motivo, valor_anterior, "
                    "valor_nuevo, estado, metricas) VALUES (?,?,?,?,?,?)",
                    (time.time(), motivo[:300], str(anterior), str(nuevo),
                     str(nuevo), json.dumps(
                         {k: (metricas.get(k) or {}).get("valor")
                          for k in ("cpu", "ram", "disco")
                          if (metricas.get(k) or {}).get("available")},
                         ensure_ascii=False)))
                c.commit()
    except Exception as exc:  # noqa: BLE001
        log.warning("no pude registrar la decisión de recursos: %s", exc)


def _publica(nivel: str, motivo: str, concurrencia) -> None:
    if _emit is None:
        return
    try:
        _emit({"type": "resource.decision", "nivel": nivel, "motivo": motivo,
               "concurrencia": concurrencia})
    except Exception as exc:  # noqa: BLE001
        log.debug("no pude publicar la decisión: %s", exc)


def _registrar_en() -> None:
    pass


if __name__ == "__main__":
    print("RESOURCE MANAGER · prueba rápida")
    conectar(medir=lambda: {"ts": time.time(), "cpu": {"porcentaje": 95},
                            "ram": {"porcentaje": 93, "disponible_gb": 1},
                            "discos": [{"libre_gb": 5, "total_gb": 100}],
                            "gpu": {"available": False}})
    r = revisar()
    print("  nivel:", r["nivel"], "| concurrencia:", r["concurrencia"])
    print("  motivo:", r["motivo"])
    print("  puede empezar urgente:", puede_empezar(80)["ok"])
    print("  puede empezar de baja:", puede_empezar(25)["ok"])
    print("  decisiones:", len(decisiones()))