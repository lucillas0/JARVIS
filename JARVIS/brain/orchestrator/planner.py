"""Cerebro de JARVIS antes de delegar: qué se pide, sobre qué, con qué riesgos.

Este módulo es el que hace que JARVIS sea orquestador y no un pasamanos:

  1. Lee el proyecto de verdad (ficheros, stack, Git, dependencias, tests).
  2. Pide al modelo un diseño estructurado sobre ESA información real.
  3. Predice qué puede salir mal, con mitigación para cada riesgo.
  4. Escribe el prompt completo que recibirá OpenCode.

Si no hay modelo disponible, NO se inventa un diseño: se devuelve un plan
mínimo construido con datos reales del proyecto y se dice que el análisis lo
hizo JARVIS sin modelo. Preferimos un plan honesto a uno bonito y falso.
"""
from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from typing import Optional

log = logging.getLogger("brain.opencode.planner")

MAX_ARCHIVOS = 4000
NIVELES = ("alto", "medio", "bajo")

_STACK = (
    ("package.json", "Node.js"),
    ("pyproject.toml", "Python (pyproject)"),
    ("requirements.txt", "Python"),
    ("setup.py", "Python"),
    ("Cargo.toml", "Rust"),
    ("go.mod", "Go"),
    ("pom.xml", "Java (Maven)"),
    ("build.gradle", "Java (Gradle)"),
    ("composer.json", "PHP"),
    ("Gemfile", "Ruby"),
    ("docker-compose.yml", "Docker Compose"),
    ("Dockerfile", "Docker"),
)


# --------------------------------------------------------------------------- #
# 1. Lectura real del proyecto
# --------------------------------------------------------------------------- #
def _git(raiz: str, *args: str) -> str:
    try:
        r = subprocess.run(["git", "-C", raiz, *args], capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=5, check=False)
        return (r.stdout or "").strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError) as exc:
        log.debug("git %s: %s", " ".join(args), exc)
        return ""


def _dependencias_python(raiz: str) -> list[str]:
    salida = []
    for nombre in ("requirements.txt", "pyproject.toml"):
        ruta = os.path.join(raiz, nombre)
        if not os.path.isfile(ruta):
            continue
        try:
            texto = open(ruta, "r", encoding="utf-8", errors="replace").read()[:20000]
        except OSError as exc:
            # Si no se puede leer un requirements, el plan se hará sin saber
            # qué hay instalado. Se anota porque luego "no funciona" no
            # señala que el problema fue de lectura y no de la dependencia.
            log.debug("no se pudo leer %s para las dependencias: %s", nombre, exc)
            continue
        if nombre == "requirements.txt":
            for linea in texto.splitlines():
                linea = linea.split("#")[0].strip()
                if linea and "==" in linea:
                    salida.append(linea)
        else:
            for m in re.finditer(r'["\']([a-zA-Z0-9_.-]+)["\']\s*=\s*["\']([^"\']+)["\']', texto):
                salida.append(f"{m.group(1)}=={m.group(2)}")
        if salida:
            break
    return salida[:60]


def _dependencias_node(raiz: str) -> dict:
    ruta = os.path.join(raiz, "package.json")
    if not os.path.isfile(ruta):
        return {}
    try:
        datos = json.loads(open(ruta, "r", encoding="utf-8", errors="replace").read())
    except (OSError, ValueError):
        return {}
    if not isinstance(datos, dict):
        return {}
    return {"name": str(datos.get("name") or ""),
            "scripts": {k: str(v)[:120] for k, v in (datos.get("scripts") or {}).items()
                        if isinstance(v, str)},
            "dependencies": sorted(str(k) for k in (datos.get("dependencies") or {}))[:80],
            "devDependencies": sorted(str(k) for k in (datos.get("devDependencies") or {}))[:80]}


def _arbol(raiz: str) -> dict:
    """Recuento real de ficheros, sin meterse en node_modules ni venvs."""
    nivel0 = {"dirs": [], "files": []}
    por_extension: dict = {}
    tests = {"dirs": [], "files": []}
    total = 0
    ignorar = ("node_modules", "venv", ".venv", "dist", "build", "__pycache__", ".git",
               "qdrant", "wa-session", "dist-installer", "win-unpacked", "target",
               ".next", ".cache", "site-packages")
    for carpeta, carpetas, ficheros in os.walk(raiz):
        carpetas[:] = [c for c in carpetas if c not in ignorar and not c.startswith(".")]
        depth = carpeta[len(raiz):].count(os.sep)
        if depth == 0:
            nivel0["dirs"] = sorted(carpetas)[:40]
            nivel0["files"] = sorted(ficheros)[:60]
        for nombre in ficheros:
            total += 1
            ext = os.path.splitext(nombre)[1].lower()
            if ext:
                por_extension[ext] = por_extension.get(ext, 0) + 1
            bajo = os.path.join(carpeta, nombre).replace("\\", "/").lower()
            if "/test" in bajo or bajo.startswith("test") or "/spec" in bajo or "/__tests__" in bajo:
                tests["files"].append(os.path.relpath(os.path.join(carpeta, nombre), raiz))
            if len(tests["files"]) > 200:
                tests["files"] = tests["files"][:200]
        if total >= MAX_ARCHIVOS:
            break
    return {"root_dirs": nivel0["dirs"], "root_files": nivel0["files"],
            "total_files": total,
            "by_extension": dict(sorted(por_extension.items(), key=lambda kv: -kv[1])[:18]),
            "test_files": tests["files"][:40], "test_count": len(tests["files"])}


def scan_project(raiz: str) -> dict:
    """Hechos verificables del proyecto. Todo lo que sale aquí se ha leído."""
    raiz = os.path.abspath(raiz or ".")
    datos: dict = {"path": raiz, "exists": os.path.isdir(raiz), "stack": [],
                   "dependencies": {}, "git": {}, "tests": {}, "files": {},
                   "readme": "", "size_bytes": 0}
    if not datos["exists"]:
        datos["error"] = "La carpeta no existe."
        return datos
    for nombre, etiqueta in _STACK:
        if os.path.exists(os.path.join(raiz, nombre)):
            datos["stack"].append(etiqueta)
    datos["dependencies"]["python"] = _dependencias_python(raiz)
    node = _dependencias_node(raiz)
    if node:
        datos["dependencies"]["node"] = node
    datos["git"] = {"repo": bool(_git(raiz, "rev-parse", "--is-inside-work-tree")),
                    "branch": _git(raiz, "rev-parse", "--abbrev-ref", "HEAD"),
                    "cambios": len(_git(raiz, "status", "--porcelain").splitlines())}
    datos["files"] = _arbol(raiz)
    readme = ""
    for nombre in ("README.md", "readme.md", "README.rst", "README"):
        if os.path.isfile(os.path.join(raiz, nombre)):
            readme = nombre
            break
    datos["readme"] = readme
    comandos_test = []
    for nombre in ("pytest.ini", "tox.ini", "jest.config.js", "vitest.config.ts",
                   "playwright.config.ts", "pyproject.toml"):
        if os.path.exists(os.path.join(raiz, nombre)):
            comandos_test.append(nombre)
    scripts = (datos["dependencies"].get("node") or {}).get("scripts") or {}
    for clave, valor in scripts.items():
        if "test" in clave.lower():
            comandos_test.append(f"npm run {clave}")
    if any(k in scripts for k in ("build", "lint")):
        comandos_test.append("npm run build/lint")
    datos["tests"] = {"ficheros": datos["files"].get("test_count", 0),
                      "configuracion": comandos_test,
                      "hay_suite": bool(datos["files"].get("test_count") or comandos_test)}
    return datos


# --------------------------------------------------------------------------- #
# 2. Análisis con modelo (sobre los datos reales)
# --------------------------------------------------------------------------- #
_SISTEMA = """Eres el ingeniero jefe de JARVIS. JARVIS va a encargar el trabajo a
OpenCode, un agente de programación que trabaja con herramientas reales.

Recibes la petición del usuario y los HECHOS leídos del proyecto (estructura,
ficheros, stack, dependencias, Git, tests). Razona SOLO sobre esos hechos y
sobre la petición. Si un dato no está, no lo supongas: dilo en "incertidumbres".

Devuelve SOLO un JSON válido con esta forma exacta:
{
 "objetivo": "qué quiere el usuario, en una frase",
 "analisis": "qué has encontrado en el proyecto que condiciona el trabajo (2-4 frases)",
 "arquitectura": ["decisiones de arquitectura, cada una en una frase"],
 "componentes": [{"nombre":"","ruta":"dónde vive o dónde debería vivir","descripcion":""}],
 "dependencias": [{"nombre":"","motivo":"","ya_existe":false}],
 "pasos": [{"titulo":"","detalle":"qué hacer exactamente","archivos":["ruta"]}],
 "riesgos": [{"nivel":"alto|medio|bajo","titulo":"","impacto":"qué se rompe","mitigacion":"cómo se evita"}],
 "permisos": [{"accion":"bash|edit|network|other","motivo":"por qué hace falta"}],
 "tests": ["cómo se comprueba que funciona"],
 "criterios": ["condiciones de aceptación verificables"],
 "restricciones": ["lo que NO se debe tocar"],
 "incertidumbres": ["lo que no se puede saber sin preguntar"]
}

Reglas: entre 3 y 10 pasos, cada uno verificable. Al menos un test. Si el
proyecto ya tiene arquitectura, se reutiliza: no se rediseña por gusto."""

_USUARIO = """PETICIÓN DEL USUARIO:
{idea}

HECHOS DEL PROYECTO ({ruta}):
{facts}"""


def _facts_texto(datos: dict) -> str:
    lineas = [f"Carpeta: {datos.get('path')}"]
    if not datos.get("exists"):
        return "La carpeta no existe."
    if datos.get("stack"):
        lineas.append(f"Tecnologías detectadas: {', '.join(datos['stack'])}")
    git = datos.get("git") or {}
    lineas.append(f"Git: {'repositorio en la rama ' + str(git.get('branch')) if git.get('repo') else 'no es un repositorio'}"
                 + (f" con {git['cambios']} cambios sin confirmar" if git.get("cambios") else ""))
    ficheros = datos.get("files") or {}
    lineas.append(f"Ficheros totales: {ficheros.get('total_files')}")
    if ficheros.get("root_dirs"):
        lineas.append("Carpetas de primer nivel: " + ", ".join(ficheros["root_dirs"][:18]))
    if ficheros.get("root_files"):
        lineas.append("Ficheros de primer nivel: " + ", ".join(ficheros["root_files"][:25]))
    if ficheros.get("by_extension"):
        lineas.append("Por extensión: " + ", ".join(
            f"{k}×{v}" for k, v in list(ficheros["by_extension"].items())[:10]))
    node = (datos.get("dependencies") or {}).get("node") or {}
    if node:
        lineas.append(f"package.json ({node.get('name') or 'sin nombre'}) scripts: "
                      + (", ".join(f"{k}={v}" for k, v in list(node["scripts"].items())[:10]) or "ninguno"))
        if node.get("dependencies"):
            lineas.append("Dependencias node: " + ", ".join(node["dependencies"][:25]))
    py = (datos.get("dependencies") or {}).get("python") or []
    if py:
        lineas.append("Dependencias python: " + ", ".join(py[:25]))
    tests = datos.get("tests") or {}
    lineas.append(f"Tests: {tests.get('ficheros', 0)} ficheros de test, "
                  f"configuración: {', '.join(tests.get('configuracion') or []) or 'ninguna'}")
    lineas.append(f"README: {datos.get('readme') or 'no hay'}")
    return "\n".join(lineas)


def _json_de(texto: str) -> dict:
    """Saca el primer JSON completo de una respuesta de modelo."""
    if not texto:
        return {}
    limpio = texto.strip()
    if limpio.startswith("```"):
        limpio = re.sub(r"^```[a-zA-Z]*\n?", "", limpio)
        limpio = re.sub(r"```$", "", limpio.strip())
    inicio = limpio.find("{")
    fin = limpio.rfind("}")
    if inicio < 0 or fin <= inicio:
        return {}
    try:
        datos = json.loads(limpio[inicio:fin + 1])
    except ValueError:
        return {}
    return datos if isinstance(datos, dict) else {}


def _lista(valor, limite: int = 12, Largo: int = 240) -> list[str]:
    if valor is None:
        return []
    if isinstance(valor, str):
        valor = [valor]
    salida = []
    for item in (valor if isinstance(valor, list) else []):
        texto = " ".join(str(item).split())[:Largo]
        if texto and texto not in salida:
            salida.append(texto)
        if len(salida) >= limite:
            break
    return salida


def analizar(idea: str, ruta: str, segundos: float = 180.0) -> dict:
    """Analiza la idea sobre el proyecto real. Devuelve el diseño estructurado."""
    datos = scan_project(ruta)
    idea = (idea or "").strip()
    respuesta = ""
    error = ""
    try:
        from model_router import ModelRouter  # import local: el planner puede usarse solo

        r = ModelRouter().respond(
            [{"role": "system", "content": _SISTEMA},
             {"role": "user", "content": _USUARIO.format(
                 idea=idea[:4000], ruta=ruta, facts=_facts_texto(datos)[:9000])}],
            heavy=True, segundos=segundos)
        respuesta = str(r.get("reply") or "")
        if r.get("sin_claves") or r.get("agotado"):
            error = str(r.get("sin_claves") or "El modelo no está disponible.")[:200]
        if r.get("fallos"):
            error = "; ".join(str(f) for f in r["fallos"])[:200]
    except Exception as exc:  # noqa: BLE001 - sin modelo el plan sale de los hechos
        error = f"No se pudo consultar al modelo: {exc}"[:200]
        log.warning("análisis sin modelo: %s", exc)

    diseño = _json_de(respuesta)
    if diseño:
        fuente = "modelo"
    else:
        fuente = "datos reales (sin modelo disponible)"
        diseño = _plan_minimo(idea, datos)
    return {"idea": idea, "workspace": ruta, "scan": datos,
            "facts": _facts_texto(datos), "design": _normaliza(diseño),
            "source": fuente, "model_error": error}


def _plan_minimo(idea: str, datos: dict) -> dict:
    """Plan sin modelo: solo pasos y riesgos que se deducen de los hechos."""
    pasos = [{"titulo": "Revisar el proyecto y localizar dónde encaja el cambio",
              "detalle": f"Leer la estructura real de {datos.get('path')} antes de tocar nada.",
              "archivos": (datos.get("files") or {}).get("root_files", [])[:10]}]
    if (datos.get("dependencies") or {}).get("node", {}).get("scripts"):
        pasos.append({"titulo": "Preparar el entorno", "detalle": "Revisar scripts disponibles en package.json.",
                      "archivos": ["package.json"]})
    pasos.append({"titulo": "Implementar el cambio pedido", "detalle": idea[:300] or "Implementar la petición.",
                  "archivos": []})
    pasos.append({"titulo": "Comprobar que funciona", "detalle":
                  (datos.get("tests") or {}).get("configuracion", ["sin comando de tests detectado"]) and
                  "Ejecutar la comprobación existente." or "No hay suite de tests: comprobar a mano y decirlo.",
                  "archivos": []})
    riesgos = []
    if not (datos.get("git") or {}).get("repo"):
        riesgos.append({"nivel": "alto", "titulo": "El proyecto no está en Git",
                        "impacto": "No hay forma de deshacer los cambios ni de verlos.",
                        "mitigacion": "Hacer una copia de la carpeta antes de empezar."})
    if not (datos.get("tests") or {}).get("hay_suite"):
        riesgos.append({"nivel": "medio", "titulo": "No hay suite de tests en el proyecto",
                        "impacto": "Nada demuestra que el cambio no rompa lo que ya funcionaba.",
                        "mitigacion": "Comprobar el flujo a mano y dejarlo escrito."})
    riesgos.append({"nivel": "medio", "titulo": "Análisis sin modelo",
                    "impacto": "El plan se ha construido solo con los hechos del proyecto.",
                    "mitigacion": "Revisar el plan antes de aprobarlo."})
    return {"objetivo": idea, "analisis": "Plan mínimo construido con datos reales del proyecto.",
            "arquitectura": [f"Reutilizar la estructura existente de {datos.get('path')}"],
            "componentes": [], "dependencias": [], "pasos": pasos, "riesgos": riesgos,
            "permisos": [], "tests": ["Verificación manual del flujo pedido"],
            "criterios": ["El cambio pedido funciona en el entorno del proyecto"],
            "restricciones": ["No tocar credenciales ni ficheros protegidos"],
            "incertidumbres": ["Falta el análisis del modelo: el plan puede estar incompleto"]}


def _normaliza(diseño: dict) -> dict:
    """Deja el diseño con la forma que espera el resto del sistema."""
    riesgos = []
    for riesgo in (diseño.get("riesgos") or [])[:12]:
        if not isinstance(riesgo, dict):
            continue
        nivel = str(riesgo.get("nivel") or "medio").strip().lower()
        if nivel not in NIVELES:
            nivel = {"high": "alto", "critical": "alto", "medium": "medio",
                     "low": "bajo"}.get(nivel, "medio")
        titulo = " ".join(str(riesgo.get("titulo") or "").split())[:160]
        if not titulo:
            continue
        riesgos.append({"nivel": nivel, "titulo": titulo,
                        "impacto": " ".join(str(riesgo.get("impacto") or "").split())[:400],
                        "mitigacion": " ".join(str(riesgo.get("mitigacion") or "").split())[:400],
                        "origen": str(riesgo.get("origen") or "análisis")[:40]})
    pasos = []
    for i, paso in enumerate((diseño.get("pasos") or [])[:20], start=1):
        if isinstance(paso, dict):
            titulo = " ".join(str(paso.get("titulo") or "").split())[:160]
            if not titulo:
                continue
            pasos.append({"n": i, "titulo": titulo,
                          "detalle": " ".join(str(paso.get("detalle") or "").split())[:600],
                          "archivos": _lista(paso.get("archivos"), 8, 200),
                          "estado": "PENDIENTE"})
        elif isinstance(paso, str) and paso.strip():
            pasos.append({"n": i, "titulo": paso.strip()[:160], "detalle": "",
                          "archivos": [], "estado": "PENDIENTE"})
    componentes = []
    for comp in (diseño.get("componentes") or [])[:16]:
        if isinstance(comp, dict):
            componentes.append({"nombre": str(comp.get("nombre") or "")[:120],
                                "ruta": str(comp.get("ruta") or "")[:200],
                                "descripcion": str(comp.get("descripcion") or "")[:400]})
        elif isinstance(comp, str) and comp.strip():
            componentes.append({"nombre": comp.strip()[:120], "ruta": "",
                                "descripcion": ""})
    dependencias = []
    for dep in (diseño.get("dependencias") or [])[:16]:
        if isinstance(dep, dict):
            dependencias.append({"nombre": str(dep.get("nombre") or "")[:80],
                                 "motivo": str(dep.get("motivo") or "")[:240],
                                 "ya_existe": bool(dep.get("ya_existe"))})
        elif isinstance(dep, str) and dep.strip():
            dependencias.append({"nombre": dep.strip()[:80], "motivo": "", "ya_existe": False})
    permisos = []
    for perm in (diseño.get("permisos") or [])[:16]:
        if isinstance(perm, dict):
            permisos.append({"accion": str(perm.get("accion") or "bash")[:40],
                             "motivo": str(perm.get("motivo") or "")[:240]})
        elif isinstance(perm, str) and perm.strip():
            permisos.append({"accion": perm.strip()[:40], "motivo": ""})
    # Los niveles de riesgo se unifican a los cuatro canónicos
    # (low/medium/high/critical). El planner siempre escribió "alto/medio/bajo"
    # en Castellano, y eso obligaba a traducir en cada sitio que lo leía. Se
    # guarda además el texto original en 'nivel_texto' para que la interfaz
    # pueda seguir mostrando "Alto" sin volver a traducir.
    try:
        import decision_engine as _de
    except Exception:  # noqa: BLE001
        _de = None
    riesgos_canónicos = []
    for riesgo in riesgos:
        if not isinstance(riesgo, dict):
            continue
        original = str(riesgo.get("nivel") or "").strip()
        canonico = _de.normalizar_riesgo(original) if _de else original
        if canonico == "desconocido":
            # Sin nivel reconocible se trata como medio: se avisa, pero no se
            # bloquea la tarea por una etiqueta mal puesta.
            canonico = "medium"
        r = dict(riesgo)
        r["nivel_texto"] = original or canonico
        r["nivel"] = canonico
        riesgos_canónicos.append(r)

    return {
        "objetivo": " ".join(str(diseño.get("objetivo") or "").split())[:400],
        "analisis": " ".join(str(diseño.get("analisis") or "").split())[:1500],
        "arquitectura": _lista(diseño.get("arquitectura")),
        "componentes": componentes,
        "dependencias": dependencias,
        "pasos": pasos,
        "riesgos": riesgos_canónicos,
        "permisos": permisos,
        "tests": _lista(diseño.get("tests")),
        "criterios": _lista(diseño.get("criterios")),
        "restricciones": _lista(diseño.get("restricciones")),
        "incertidumbres": _lista(diseño.get("incertidumbres")),
    }


# --------------------------------------------------------------------------- #
# 3. Predicción de fallos
# --------------------------------------------------------------------------- #
def predecir_riesgos(diseno: dict, scan: dict, idea: str) -> list[dict]:
    """Riesgos del modelo + riesgos que se ven en los hechos del proyecto."""
    riesgos = list(diseno.get("riesgos") or [])
    ya = " ".join(r.get("titulo", "").lower() for r in riesgos)

    git = scan.get("git") or {}
    if not git.get("repo") and "git" not in ya:
        riesgos.append({"nivel": "alto", "titulo": "La carpeta no está en un repositorio Git",
                        "impacto": "Sin Git no hay historial: cualquier cambio se pierde sin vuelta atrás.",
                        "mitigacion": "Copiar la carpeta antes de empezar, o iniciar un repositorio.",
                        "origen": "proyecto"})
    elif git.get("cambios") and "cambios sin confirmar" not in ya:
        riesgos.append({"nivel": "medio",
                        "titulo": f"Hay {git['cambios']} cambios sin confirmar en el repositorio",
                        "impacto": "No se puede distinguir lo que ya estaba cambiado de lo que cambie OpenCode.",
                        "mitigacion": "Confirmar o guardar los cambios actuales antes de delegar.",
                        "origen": "proyecto"})

    tests = scan.get("tests") or {}
    if not tests.get("hay_suite") and "tests" not in ya:
        riesgos.append({"nivel": "medio", "titulo": "El proyecto no tiene suite de tests",
                        "impacto": "No hay forma automática de detectar que el cambio rompe algo.",
                        "mitigacion": "Pedir una comprobación manual explícita en los criterios de éxito.",
                        "origen": "proyecto"})

    texto = json.dumps(diseno, ensure_ascii=False).lower()
    for paquete in [d.get("nombre") for d in (diseno.get("dependencias") or []) if not d.get("ya_existe")]:
        if paquete and str(paquete).lower() not in texto.split("dependencias")[0]:
            riesgos.append({"nivel": "medio", "titulo": f"Dependencia nueva: {paquete}",
                            "impacto": "Puede no estar instalada o no existir para esta plataforma.",
                            "mitigacion": "Comprobar la versión antes de instalar y anotarla en el README.",
                            "origen": "diseño"})
            break

    permisos = {str(p.get("accion") or "").lower() for p in (diseno.get("permisos") or [])}
    if any(p in permisos for p in ("bash", "network", "other")):
        riesgos.append({"nivel": "bajo", "titulo": "Harán falta comandos del sistema o red",
                        "impacto": "Un comando puede descargar, instalar o reiniciar servicios.",
                        "mitigacion": "Cada permiso se pregunta en JARVIS Live; no se concede nada automáticamente.",
                        "origen": "diseño"})
    for riesgo in riesgos:
        if riesgo.get("nivel") not in NIVELES:
            riesgo["nivel"] = "medio"
    riesgos.sort(key=lambda r: NIVELES.index(r["nivel"]))
    return riesgos[:14]


# --------------------------------------------------------------------------- #
# 4. Estimación (aproximada y etiquetada como tal)
# --------------------------------------------------------------------------- #
def estimar(diseno: dict, riesgos: list[dict]) -> dict:
    """Estimación aproximada a partir del tamaño real del plan. Sin fingir precisión."""
    pasos = diseno.get("pasos") or []
    base = 2 + 2.5 * len(pasos)
    pesos = {"alto": 6, "medio": 3, "bajo": 1}
    carga = sum(pesos.get(r.get("nivel"), 2) for r in riesgos or [])
    minimo = int(base + carga)
    maximo = int(minimo * 1.9 + 6)
    return {"min": max(3, minimo), "max": max(6, maximo),
            "label": f"Estimación aproximada: {max(3, minimo)}-{max(6, maximo)} min",
            "basis": f"{len(pasos)} pasos y {len(riesgos or [])} riesgos previstos"}


# --------------------------------------------------------------------------- #
# 5. El prompt que recibirá OpenCode
# --------------------------------------------------------------------------- #
_SIN_TOCAR = ("keys.env", ".env", ".db", "*.db-wal", "*.db-shm", "auth.json",
              "creds.json", "credentials", "id_rsa", ".git", "node_modules")


def construir_prompt(idea: str, diseno: dict, scan: dict, riesgos: list[dict],
                     ruta: str, notas: Optional[list[str]] = None) -> str:
    """Prompt completo para OpenCode: qué hacer, dónde, con qué límites."""
    pasos = diseno.get("pasos") or []
    lineas = [
        "# TAREA ENCARGADA POR JARVIS",
        "",
        "JARVIS ha analizado el proyecto y te encarga este trabajo. Trabaja en la",
        "carpeta indicada y finishes el ciclo completo: leer, cambiar, probar y",
        "informar. Si algo no está claro, PREGUNTA (no lo adivines).",
        "",
        "## OBJETIVO",
        (diseno.get("objetivo") or idea or "").strip()[:1200] or idea.strip(),
        "",
        "## PETICIÓN ORIGINAL DEL USUARIO",
        idea.strip()[:2500],
        "",
        "## CONTEXTO REAL DEL PROYECTO",
        f"- Carpeta de trabajo: {ruta}",
        f"- Tecnologías detectadas: {', '.join(scan.get('stack') or []) or 'sin detectar'}",
        f"- Git: {'rama ' + str((scan.get('git') or {}).get('branch')) if (scan.get('git') or {}).get('repo') else 'sin repositorio'}",
        f"- Tests existentes: {int((scan.get('tests') or {}).get('ficheros') or 0)} ficheros"
        f"{', comandos: ' + ', '.join((scan.get('tests') or {}).get('configuracion') or []) if (scan.get('tests') or {}).get('configuracion') else ''}",
    ]
    if (scan.get("dependencies") or {}).get("node"):
        node = scan["dependencies"]["node"]
        lineas.append(f"- package.json scripts: {', '.join(f'{k}={v}' for k, v in list((node.get('scripts') or {}).items())[:10]) or 'ninguno'}")
    if (scan.get("dependencies") or {}).get("python"):
        lineas.append(f"- Dependencias Python: {', '.join(scan['dependencies']['python'][:20])}")
    if diseno.get("analisis"):
        lineas += ["", f"Análisis de JARVIS: {diseno['analisis']}"]
    if notas:
        lineas += ["", "## INDICACIONES DEL USUARIO PARA ESTE PLAN"] + \
                  [f"- {n}" for n in notas]

    if diseno.get("arquitectura"):
        lineas += ["", "## ARQUITECTURA A REUTILIZAR"] + \
                  [f"- {a}" for a in diseno["arquitectura"]]
    if diseno.get("componentes"):
        lineas += ["", "## COMPONENTES"] + \
                  [f"- {c['nombre']}" + (f" ({c['ruta']})" if c.get("ruta") else "") +
                   (f": {c['descripcion']}" if c.get("descripcion") else "") for c in diseno["componentes"]]
    if diseno.get("dependencias"):
        lineas += ["", "## DEPENDENCIAS"] + [
            f"- {d['nombre']}: {d.get('motivo') or 'necesaria'}"
            f"{' (ya existe en el proyecto)' if d.get('ya_existe') else ''}"
            for d in diseno["dependencias"]]

    lineas += ["", "## PLAN DE IMPLEMENTACIÓN (síguelo en este orden)"]
    for paso in pasos:
        titulo = paso.get("titulo") if isinstance(paso, dict) else str(paso)
        detalle = paso.get("detalle", "") if isinstance(paso, dict) else ""
        archivos = paso.get("archivos") if isinstance(paso, dict) else None
        lineas.append(f"{paso.get('n', '?') if isinstance(paso, dict) else '?'}. {titulo}")
        if detalle:
            lineas.append(f"   {detalle}")
        if archivos:
            lineas.append(f"   Ficheros: {', '.join(archivos)}")
    lineas.append("")
    lineas.append("Avanza UN paso cada vez. Si un paso depende de una decisión que no")
    lineas.append("puedes tomar tú (base de datos, puerto, formato, borrar algo), USA la")
    lineas.append("herramienta de pregunta y espera: JARVIS le enseñará la pregunta al")
    lineas.append("usuario y te devolverá su respuesta.")

    if riesgos:
        lineas += ["", "## RIESGOS PREVISTOS POR JARVIS (compruébalos, no los repitas)"]
        for riesgo in riesgos:
            marca = {"alto": "ALTO", "medio": "MEDIO", "bajo": "BAJO"}.get(riesgo.get("nivel"), "MEDIO")
            lineas.append(f"- [{marca}] {riesgo['titulo']}")
            if riesgo.get("impacto"):
                lineas.append(f"  Impacto: {riesgo['impacto']}")
            if riesgo.get("mitigacion"):
                lineas.append(f"  Mitigación: {riesgo['mitigacion']}")

    if diseno.get("restricciones"):
        lineas += ["", "## RESTRICCIONES"] + [f"- {r}" for r in diseno["restricciones"]]

    lineas += ["", "## QUÉ NO DEBES HACER",
               "- No salir de la carpeta de trabajo indicada.",
               "- No tocar credenciales ni ficheros de datos: " + ", ".join(_SIN_TOCAR),
               "- No borrar ficheros que no hayas creado tú en esta tarea.",
               "- No instalar dependencias pesadas sin decirlo antes.",
               "- No reescribir la arquitectura que ya existe: la reutilizas.",
               "- No inventar resultados: si un test no se ejecuta, dilo.",
               ]
    if (scan.get("tests") or {}).get("hay_suite") is False:
        lineas.append("- Aquí no hay suite de tests: comprueba el flujo a mano y descríbelo.")

    if diseno.get("tests"):
        lineas += ["", "## TESTS QUE DEBES EJECUTAR"] + [f"- {t}" for t in diseno["tests"]]
    if diseno.get("criterios"):
        lineas += ["", "## CRITERIOS DE ÉXITO"] + [f"- {c}" for c in diseno["criterios"]]
    if diseno.get("permisos"):
        lineas += ["", "## PERMISOS QUE VAS A NECESITAR"] + \
                  [f"- {p['accion']}: {p.get('motivo') or 'para llevar a cabo el plan'}"
                   for p in diseno["permisos"]]

    lineas += ["",
               "## REGLAS DE SEGURIDAD Y DE TRABAJO",
               "- Trabaja dentro del Permission Manager de OpenCode: si pide permiso,",
               "  deja que JARVIS lo conteste. Nunca lo concedas por tu cuenta.",
               "- Cuando te pregunten algo que implique borrar, migrar datos, cambiar",
               "  credenciales o servicios públicos, USA la pregunta y espera al usuario.",
               "- Ante un error: léelo, di qué lo causa y qué has tocado. No sigas",
               "  apilando cambios a ciegas.",
               "- Si un paso del plan no se puede hacer, PARA y explica cuál y por qué.",
               "- Al terminar informa en este orden: qué hiciste, qué ficheros",
               "  cambiaste (+ nuevo, ~ modificado, - borrado), qué tests corriste y",
               "  con qué resultado, y qué queda pendiente.",
               ""]
    return "\n".join(lineas)


# --------------------------------------------------------------------------- #
# 6. Modificar el plan a petición del usuario
# --------------------------------------------------------------------------- #
_REVISAR_SISTEMA = """Eres el ingeniero jefe de JARVIS. El usuario ha-retocado el plan
de una tarea. Devuelve SOLO el JSON completo del plan ya corregido, con la
misma forma que te di antes: objetivo, analisis, arquitectura, componentes,
dependencias, pasos (cada uno con n, titulo, detalle, archivos), riesgos
(nivel alto|medio|bajo, titulo, impacto, mitigacion), permisos, tests,
criterios, restricciones e incertidumbres.

Cambia lo que el usuario pida, conserva el resto del plan y no inventes
arquitectura nueva. Si la petición pide algo imposible, déjalo escrito en
"restricciones" o "incertidumbres"."""


def revisar(diseno: dict, nota: str, scan: dict, ruta: str,
            segundos: float = 120.0) -> tuple[dict, list[str], str]:
    """Aplica una indicación del usuario al plan. Devuelve (plan, cambios, error)."""
    nota = (nota or "").strip()
    if not nota:
        return diseno, [], "No hay indicación que aplicar."
    corregido = {}
    error = ""
    try:
        from model_router import ModelRouter

        r = ModelRouter().respond(
            [{"role": "system", "content": _REVISAR_SISTEMA},
             {"role": "user", "content": (
                 f"PLAN ACTUAL:\n{json.dumps(diseno, ensure_ascii=False)[:12000]}\n\n"
                 f"CARPETA: {ruta}\nHECHOS:\n{_facts_texto(scan)[:4000]}\n\n"
                 f"INDICACIÓN DEL USUARIO: {nota[:1500]}")}],
            heavy=True, segundos=segundos)
        corregido = _json_de(str(r.get("reply") or ""))
        if not corregido:
            error = "El modelo no ha devuelto un plan corregido."
    except Exception as exc:  # noqa: BLE001
        error = f"No se pudo consultar al modelo: {exc}"[:200]
        log.warning("revisión sin modelo: %s", exc)
    if not corregido:
        return diseno, [], error or "No se pudo rehacer el plan."

    normalizado = _normaliza(corregido)
    cambios = _diferencias(diseno, normalizado)
    return normalizado, cambios, ""


def _diferencias(antes: dict, despues: dict) -> list[str]:
    """Qué cambió de verdad entre dos planes (texto plano para el historial)."""
    salida = []
    titulos_antes = [p.get("titulo", "") for p in (antes.get("pasos") or [])]
    titulos_despues = [p.get("titulo", "") for p in (despues.get("pasos") or [])]
    for titulo in titulos_despues:
        if titulo not in titulos_antes:
            salida.append(f"Paso nuevo: {titulo}")
    for titulo in titulos_antes:
        if titulo not in titulos_despues:
            salida.append(f"Paso quitado: {titulo}")
    for clave, etiqueta in (("objetivo", "Objetivo"), ("criterios", "Criterios"),
                            ("restricciones", "Restricciones"), ("tests", "Tests")):
        a = antes.get(clave)
        b = despues.get(clave)
        if isinstance(a, list) or isinstance(b, list):
            if [str(x) for x in (a or [])] != [str(x) for x in (b or [])]:
                salida.append(f"{etiqueta} actualizados.")
        elif str(a or "") != str(b or ""):
            salida.append(f"{etiqueta} actualizado.")
    return salida[:12] or ["Plan revisado sin cambios de fondo."]