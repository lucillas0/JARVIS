"""Recuerdos de JARVIS: qué sabe, de dónde lo sacó y por qué lo recuerda.

**Memoria NO es contexto.** Este módulo guarda (y la memoria conversacional de
`memory/` sigue donde estaba). Aquí vive lo que JARVIS afirma saber a medio
plazo con estructura suficiente para poder preguntarle:

    - de dónde salió (procedencia) y quién lo produjo (origen);
    - si es un hecho, una preferencia, una inferencia o está obsoleto;
    - a qué proyecto, misión o decisión pertenece;
    - cuánto pesa (relevancia) y por qué se recuperó.

Tres decisiones deliberadas:

1. **No se guarda cada mensaje.** Un mensaje es una interacción, no un hecho.
   Lo que entra aquí pasa por `es_util()`, que exige que sea una preferencia,
   un dato con procedencia, o algo que el señor dijo que quiere recordar. Sin
   ese filtro, la memoria se llena de ruido y luego no sirve para nada.

2. **Una inferencia NUNCA pisa un hecho.** Si llega una inferencia que
   contradice un hecho confirmado, se guarda como contradictoria y se avisa, en
   lugar de sobrescribir. Perder un dato comprobado es peor que tener dos
   cosas que no encajan.

3. **La relevancia se explica.** Cada recuperación devuelve POR QUÉ Sunshine
   salió ese recuerdo (texto, recencia, proyecto, uso), para poder responder
   "¿por qué has dicho eso?" con algo mejor que un "porque sí".

La tabla y los índices están en `memory/sqlite_store.py`; la migración es
automática al abrir la base.
"""
from __future__ import annotations

import json
import logging
import re
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Optional

# Cuando JARVIS lo importa, brain/ ya está en el path. Cuando se ejecuta
# suelto (`python memory/recuerdos.py`), no: sin esto el import de abajo falla.
_RAIZ = Path(__file__).resolve().parent.parent
if str(_RAIZ) not in sys.path:
    sys.path.insert(0, str(_RAIZ))

from memory import sqlite_store as db  # noqa: E402

log = logging.getLogger("brain.memoria")

_candado = threading.RLock()

# --------------------------------------------------------------------------- #
# Tipos. 'hecho' y 'preferencia' son afirmaciones; 'inferencia' NO lo es.
# --------------------------------------------------------------------------- #
HECHO = "hecho"
PREFERENCIA = "preferencia"
INFERENCIA = "inferencia"
OBSOLETO = "obsoleto"
TIPOS = (HECHO, PREFERENCIA, INFERENCIA, OBSOLETO)

ACTIVO = "activo"
ARCHIVADO = "archivado"
ELIMINADO = "eliminado"

# Categorías de uso general. No son cerradas: se puede pasar cualquiera.
CATEGORIAS = ("personal", "proyecto", "tecnico", "preferencias", "servicios")

# --------------------------------------------------------------------------- #
# Lo que hace que algo sea digno de guardarse
# --------------------------------------------------------------------------- #
# Frases con las que el señor dice "acuérdate de esto".
_MARCA_MEMORIA = re.compile(
    r"\b(acu[eé]rd|recuerda|memoriza|no olvides|ap[uú]ntalo|"
    r"de aqui en adelante|a partir de ahora|siempre que|prefiero que|"
    r"me gusta que|nunca me|puedes usar|preferencias?[:.])\b", re.I)

# Frases que son claramente de un momento, no para siempre.
_ES_TRANSITORIO = re.compile(
    r"\b(ahora mismo|por ahora|de momento|esta semana|hoy|ayer|"
    r"en cuanto|voy a|estoy con|pronto)\b", re.I)

# Cuánto pesa cada tipo a la hora de recuperar.
PESO_TIPO = {HECHO: 1.0, PREFERENCIA: 0.95, INFERENCIA: 0.45, OBSOLETO: 0.1}


def es_util(texto: str, tipo: str = HECHO, origen: str = "") -> tuple[bool, str]:
    """¿Merece la pena guardar esto como recuerdo?

    Devuelve (sí/no, motivo) para que quien llama pueda explicar la decisión
    y no se guarden cosas a ciegas.
    """
    t = (texto or "").strip()
    if len(t) < 4:
        return False, "demasiado corto"
    if len(t) > 4000:
        return False, "demasiado largo para un recuerdo"
    if tipo == PREFERENCIA:
        return True, "es una preferencia del señor"
    if tipo == INFERENCIA:
        return True, "se marca como inferencia, no como hecho"
    if _MARCA_MEMORIA.search(t):
        return True, "el señor pidió recordarlo"
    # Un hecho con procedencia (de una misión, de un documento) vale.
    if origen and origen not in ("chat", "texto"):
        return True, f"viene de {origen}, que es una fuente verificable"
    if _ES_TRANSITORIO.search(t):
        return False, "es de un momento concreto, no un dato a medio plazo"
    return False, "no pide recordarse y no tiene procedencia verificable"


# --------------------------------------------------------------------------- #
def _fila(fila) -> dict:
    d = dict(fila)
    for campo, defecto in (("metadata", {}), ("recursos", []),
                           ("restricciones", {})):
        if campo in d and isinstance(d[campo], str) and d[campo]:
            try:
                d[campo] = json.loads(d[campo])
            except (ValueError, TypeError):
                d[campo] = defecto
    return d


def _guardar(fila: dict) -> None:
    columnas = ("id", "contenido", "tipo", "categoria", "origen", "procedencia",
                "proyecto", "mission_id", "decision_id", "metadata", "relevancia",
                "estado", "creado", "actualizado", "accessed", "veces_usado",
                "contradice")
    valores = []
    for c in columnas:
        v = fila.get(c)
        if isinstance(v, (dict, list)):
            v = json.dumps(v, ensure_ascii=False)
        valores.append(v)
    with _candado:
        with db.get_conn() as c:
            c.execute(
                f"INSERT OR REPLACE INTO recuerdos ({','.join(columnas)}) "
                f"VALUES ({','.join('?' * len(columnas))})", valores)
            c.commit()


# --------------------------------------------------------------------------- #
# ESCRIBIR
# --------------------------------------------------------------------------- #
def recordar(texto: str, tipo: str = HECHO, *, categoria: str = "",
             origen: str = "chat", procedencia: str = "", proyecto: str = "",
             mission_id: str = "", decision_id: str = "",
             metadata: Optional[dict] = None, relevancia: float = 0.5,
             forzar: bool = False) -> dict:
    """Guarda un recuerdo. Devuelve {ok, id, motivo}."""
    if tipo not in TIPOS:
        tipo = HECHO
    if not forzar:
        vale, motivo = es_util(texto, tipo, origen)
        if not vale:
            return {"ok": False, "motivo": motivo,
                    "detalle": "no se guarda: no cumple el criterio de utilidad"}
    texto = (texto or "").strip()
    # Duplicados: si ya hay un recuerdo casi igual y activo, no se duplica.
    existente = _duplicado(texto)
    if existente:
        ahora = time.time()
        with _candado:
            with db.get_conn() as c:
                c.execute("UPDATE recuerdos SET actualizado=?, veces_usado=veces_usado+1 "
                          "WHERE id=?", (ahora, existente["id"]))
                c.commit()
        return {"ok": True, "id": existente["id"], "duplicado": True,
                "motivo": "ya se sabía; se refrescó en vez de duplicar"}

    ahora = time.time()
    nuevo = {
        "id": "rec-" + uuid.uuid4().hex[:12],
        "contenido": texto, "tipo": tipo,
        "categoria": categoria or (PREFERENCIA if tipo == PREFERENCIA else "tecnico"),
        "origen": origen, "procedencia": procedencia,
        "proyecto": proyecto, "mission_id": mission_id, "decision_id": decision_id,
        "metadata": metadata or {}, "relevancia": float(relevancia),
        "estado": ACTIVO, "creado": ahora, "actualizado": ahora,
        "accessed": 0, "veces_usado": 0, "contradice": "",
    }

    # Contradicción: una inferencia NUNCA pisa un hecho confirmado.
    choque = _buscar_contradiccion(texto, tipo)
    if choque:
        nuevo["contradice"] = choque["id"]
        nuevo["relevancia"] = min(nuevo["relevancia"], 0.3)
        nuevo["metadata"] = {**nuevo["metadata"], "contradiccion": {
            "id": choque["id"], "contenido": choque["contenido"][:300],
            "tipo": choque["tipo"]}}
    _guardar(nuevo)
    if choque:
        _registrar_conflicto(nuevo, choque)
    return {"ok": True, "id": nuevo["id"], "duplicado": False,
            "contradice": choque["id"] if choque else ""}


def _duplicado(texto: str) -> Optional[dict]:
    """¿Ya hay un recuerdo activo que diga exactamente esto?

    Se compara normalizando en Python, no con un `=` en SQL: el texto guardado
    es el que escribió el señor (con su falta y su acentuación) y la clave de
    comparación es normalizada, así que buscar por igualdad no encontraba
    nunca nada y los duplicados se colaban.
    """
    clave = _clave(texto)
    if not clave:
        return None
    with _candado:
        with db.get_conn() as c:
            filas = c.execute(
                "SELECT * FROM recuerdos WHERE estado='activo' "
                "ORDER BY creado DESC LIMIT 200").fetchall()
    for f in filas:
        if _clave(f["contenido"]) == clave:
            return _fila(f)
    return None


def _clave(texto: str) -> str:
    """Forma de comparar dos recuerdos para saber si dicen lo mismo."""
    t = re.sub(r"[^a-z0-9áéíóúñ ]+", " ", (texto or "").lower())
    return " ".join(t.split())


def _buscar_contradiccion(texto: str, tipo: str) -> Optional[dict]:
    """¿Hay un hecho confirmado que diga lo contrario?

    Se busca un hecho/preferencia activo con las mismas palabras clave y
    contenido distinto. Es una heurística honesta: no sabe negaciones, así
    que avisa de "esto se parece pero dice otra cosa" y deja que la interfaz
    lo muestre. No decide nada por su cuenta.
    """
    if tipo not in (INFERENCIA, OBSOLETO):
        return None
    palabras = [p for p in re.findall(r"[a-záéíóúñ]{4,}", (texto or "").lower())][:12]
    if len(palabras) < 3:
        return None
    consulta = " OR ".join("contenido LIKE ?" for _ in palabras[:6])
    with _candado:
        with db.get_conn() as c:
            fila = c.execute(
                f"SELECT * FROM recuerdos WHERE estado='activo' AND tipo IN "
                f"('hecho','preferencia') AND ({consulta}) LIMIT 1",
                tuple(f"%{p}%" for p in palabras[:6])).fetchone()
    return _fila(fila) if fila else None


def _registrar_conflicto(nuevo: dict, choque: dict) -> None:
    """Deja constancia del conflicto: no se borra el hecho, se anota."""
    try:
        db.add_audit("memory.conflict", {"nuevo": nuevo["id"], "otro": choque["id"]},
                     "low", None, "success", "contradiccion_detectada")
    except Exception as exc:  # noqa: BLE001
        log.debug("no pude auditar la contradicción: %s", exc)
    log.info("Recuerdos en conflicto: %s frente a %s (se conservan los dos)",
             nuevo["id"], choque["id"])


# --------------------------------------------------------------------------- #
# LEER
# --------------------------------------------------------------------------- #
def obtener(rec_id: str) -> Optional[dict]:
    with _candado:
        with db.get_conn() as c:
            fila = c.execute("SELECT * FROM recuerdos WHERE id=?",
                             (rec_id,)).fetchone()
    if not fila:
        return None
    d = _fila(fila)
    ahora = time.time()
    with _candado:
        with db.get_conn() as c:
            c.execute("UPDATE recuerdos SET accessed=? WHERE id=?", (ahora, rec_id))
            c.commit()
    return d


def listar(*, estado: str = ACTIVO, tipo: str = "", categoria: str = "",
           proyecto: str = "", mission_id: str = "", decision_id: str = "",
           limite: int = 50, orden: str = "relevancia") -> list[dict]:
    """Lista recuerdos con los filtros que pide el diseño."""
    cond = ["estado = ?"]
    vals: list = [estado]
    for campo, valor in (("tipo", tipo), ("categoria", categoria),
                         ("proyecto", proyecto), ("mission_id", mission_id),
                         ("decision_id", decision_id)):
        if valor:
            cond.append(f"{campo} = ?")
            vals.append(valor)
    orden_sql = {"relevancia": "relevancia DESC, creado DESC",
                 "reciente": "creado DESC",
                 "usado": "veces_usado DESC, creado DESC"}.get(orden, "relevancia DESC")
    vals.append(max(1, min(500, int(limite))))
    with _candado:
        with db.get_conn() as c:
            filas = c.execute(
                f"SELECT * FROM recuerdos WHERE {' AND '.join(cond)} "
                f"ORDER BY {orden_sql} LIMIT ?", tuple(vals)).fetchall()
    return [_fila(f) for f in filas]


# --------------------------------------------------------------------------- #
# RECUPERAR POR CONTEXTO (esto es lo que usa el Context Engine)
# --------------------------------------------------------------------------- #
# Ponderaciones. Estan en un sitio solo y son configurables, para que subir o
# bajar el peso de la recencia sea cambiar un numero, no reescribir la función.
PESOS = {
    "texto": 0.45,        # coincidencia de palabras
    "recencia": 0.20,     # lo reciente pesa más
    "proyecto": 0.15,     # mismo proyecto/misión
    "uso": 0.10,          # lo que ya se usó alguna vez
    "tipo": 0.10,         # hechos por encima de inferencias
}

# Medio vida de la bonificación por recencia. Con 30 días, un recuerdo de hace
# mes y medio ya no suma.
MEDIA_VIDA_DIAS = 30.0

# Cuántos recuerdos se devuelven como mucho: es lo que acaba en el prompt.
TOPE_POR_DEFECTO = 5

# Por debajo de esta puntuación, el recuerdo NO se devuelve. Un recuerdo que no
# comparte ni una palabra con lo que se pregunta y no es del mismo trabajo se
# queda por debajo, por muy antiguo o muy usado que esté. Sin este corte, "lo
# he usado mil veces" terminaba ganando a "habla de lo mismo", que es justo al
# revés de lo que sirve un buscador de recuerdos.
MIN_PUNTUACION = 0.25


def recuperar(consulta: str, *, proyecto: str = "", mission_id: str = "",
              topes: int = TOPE_POR_DEFECTO) -> list[dict]:
    """Los recuerdos más pertinentes para esta consulta, con su porqué.

    Devuelve además 'por_que' en cada uno: cómo se calculó su puntuación. Sin
    eso, recuperarparecido es indistinguible de recordar por costumbre.
    """
    if not (consulta or "").strip():
        return []
    palabras = [p for p in re.findall(r"[a-záéíóúñ]{4,}", consulta.lower())]
    ahora = time.time()
    candidatos = listar(estado=ACTIVO, limite=200, orden="relevancia")

    puntuados = []
    for r in candidatos:
        por_que = {}
        contenido = (r["contenido"] or "").lower()
        # Texto: cuántas de las palabras de la consulta aparecen.
        comunes = [p for p in palabras if p in contenido]
        por_que["texto"] = (len(comunes) / max(1, len(palabras))) if palabras else 0.0
        # Recencia: decae con vida media, y NUNCA premia lo antiguo.
        dias = (ahora - float(r.get("creado") or ahora)) / 86400.0
        por_que["recencia"] = 0.5 ** (max(0.0, dias) / MEDIA_VIDA_DIAS)
        # Proyecto/misión: que sea de lo mismo.
        mismo = 0.0
        if proyecto and r.get("proyecto") == proyecto:
            mismo += 0.6
        if mission_id and r.get("mission_id") == mission_id:
            mismo += 1.0
        por_que["proyecto"] = min(1.0, mismo)
        # Uso, con techo: aparecer mil veces no te hace relevante para siempre.
        por_que["uso"] = min(1.0, float(r.get("veces_usado") or 0) / 5.0)
        # Tipo: un hecho pesa más que una inferencia.
        por_que["tipo"] = PESO_TIPO.get(r.get("tipo", HECHO), 0.5)

        total = sum(PESOS[k] * por_que[k] for k in PESOS)
        # El recuerdo ya guardado es un dato más: no se recalcula como verdad.
        total = min(1.0, total * 0.7 + float(r.get("relevancia") or 0.5) * 0.3)
        # Y un recuerdo que no tiene NADA que ver con lo que se pregunta no
        # entra por mucho que sea antiguo, muy usado o muy relevante: si no
        # comparte ni una palabra ni es del mismo trabajo, se queda por debajo
        # del corte. Sin esto, "usado muchas veces" acababa ganando a "habla
        # de lo mismo", que es justo al revés de lo útil.
        if por_que["texto"] <= 0.0 and por_que["proyecto"] <= 0.0:
            total = min(total, 0.15)
        puntuados.append((total, r, por_que))

    puntuados.sort(key=lambda x: -x[0])
    salida = []
    for total, r, por_que in puntuados:
        if total < MIN_PUNTUACION:
            # No llega al corte: se deja fuera y se dice cuántos se descartaron,
            # para que "no recuerdo nada" no parezca "no tengo recuerdos".
            continue
        item = dict(r)
        item["puntuacion"] = round(total, 4)
        item["por_que"] = {k: round(v, 3) for k, v in por_que.items()}
        item["motivo"] = _explicar(por_que)
        salida.append(item)
        if len(salida) >= max(1, min(20, topes)):
            break
    _marcar_usados([i["id"] for i in salida])
    return salida


def _explicar(por_que: dict) -> str:
    partes = []
    if por_que["texto"] > 0.3:
        partes.append(f"habla de lo mismo ({por_que['texto']:.0%})")
    if por_que["proyecto"] > 0.4:
        partes.append("es del mismo trabajo")
    if por_que["recencia"] > 0.6:
        partes.append("es reciente")
    if por_que["uso"] > 0.4:
        partes.append("se ha usado antes")
    if not partes:
        partes.append("guardado como relevante")
    return "; ".join(partes)


def _marcar_usados(ids: list) -> None:
    if not ids:
        return
    ahora = time.time()
    with _candado:
        with db.get_conn() as c:
            c.execute(f"UPDATE recuerdos SET veces_usado = veces_usado + 1, "
                      f"accessed = ? WHERE id IN ({','.join('?' * len(ids))})",
                      tuple([ahora] + list(ids)))
            c.commit()


# --------------------------------------------------------------------------- #
# MODIFICAR
# --------------------------------------------------------------------------- #
def actualizar(rec_id: str, **campos) -> dict:
    permitidos = ("contenido", "tipo", "categoria", "proyecto", "mission_id",
                  "decision_id", "metadata", "relevancia", "estado", "origen",
                  "procedencia")
    cambios = {k: v for k, v in campos.items() if k in permitidos}
    if not cambios:
        return {"ok": False, "error": "No hay nada que cambiar."}
    if "estado" in cambios and cambios["estado"] not in (ACTIVO, ARCHIVADO, ELIMINADO):
        return {"ok": False, "error": "Estado no válido."}
    if "tipo" in cambios and cambios["tipo"] not in TIPOS:
        return {"ok": False, "error": "Tipo no válido."}
    if isinstance(cambios.get("metadata"), dict):
        cambios["metadata"] = json.dumps(cambios["metadata"], ensure_ascii=False)
    asignaciones = ", ".join(f"{k}=?" for k in cambios)
    with _candado:
        with db.get_conn() as c:
            c.execute(f"UPDATE recuerdos SET {asignaciones}, actualizado=? "
                      f"WHERE id=?", tuple(cambios.values()) + (time.time(), rec_id))
            c.commit()
    return {"ok": True, "id": rec_id, "cambios": list(cambios)}


def archivar(rec_id: str) -> dict:
    """Archivar no es borrar: el recuerdo sigue ahí, pero no se recupera."""
    return actualizar(rec_id, estado=ARCHIVADO)


def olvidar(rec_id: str) -> dict:
    """Borrado lógico. Se conserva el registro de que existió."""
    return actualizar(rec_id, estado=ELIMINADO)


def olvidar_todo(confirmado: bool = False) -> dict:
    if not confirmado:
        return {"ok": False,
                "error": "Hay que confirmar: esto borra todos los recuerdos."}
    with _candado:
        with db.get_conn() as c:
            n = c.execute("SELECT COUNT(*) FROM recuerdos").fetchone()[0]
            c.execute("UPDATE recuerdos SET estado='eliminado'")
            c.commit()
    return {"ok": True, "eliminados": n}


# --------------------------------------------------------------------------- #
def resumen() -> dict:
    with _candado:
        with db.get_conn() as c:
            total = c.execute("SELECT COUNT(*) FROM recuerdos").fetchone()[0]
            activos = c.execute("SELECT COUNT(*) FROM recuerdos WHERE estado='activo'"
                                ).fetchone()[0]
            por_tipo = {r[0]: r[1] for r in c.execute(
                "SELECT tipo, COUNT(*) FROM recuerdos WHERE estado='activo' "
                "GROUP BY tipo").fetchall()}
            por_categoria = {r[0]: r[1] for r in c.execute(
                "SELECT COALESCE(NULLIF(categoria,''),'sin_categoria'), COUNT(*) "
                "FROM recuerdos WHERE estado='activo' GROUP BY 1").fetchall()}
            conflictos = c.execute(
                "SELECT COUNT(*) FROM recuerdos WHERE contradice != ''").fetchone()[0]
    return {"total": total, "activos": activos, "por_tipo": por_tipo,
            "por_categoria": por_categoria, "contradicciones": conflictos,
            "pesos": PESOS}


if __name__ == "__main__":
    print("MEMORIA ESTRUCTURADA · prueba rápida")
    r = recordar("El señor prefiere que le hable en español", tipo=PREFERENCIA,
                 origen="preferencia", forzar=True)
    print("  guardar:", r)
    print("  recuperar:", [x["motivo"] for x in recuperar("español")])
    print("  ruido:", recordar("hola"))
    print("  resumen:", resumen())