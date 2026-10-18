"""Analizador estatico de JARVIS: busca problemas REALES en el codigo.

No comprueba si algo "parece" bien: mira el arbol sintactico. Cada hallazgo
lleva fichero, linea y por que importa, para que se pueda decidir sin releer
todo el codigo.

Lo que busca (los puntos del pase de hardening):
  - rutas duplicadas o inconsistentes (el mismo endpoint definido dos veces)
  - llamadas que BLOQUEAN el event loop dentro de async def
  - errores silenciados: except que se come la excepcion y devuelve algo
    ("funciona" cuando no funciono) -- el peor tipo de bug
  - colecciones que crecen sin limite (fugas de memoria)
  - secretos escritos en el codigo
  - bucles ocupados: while True con esperas cortisimas
  - tareas creadas y olvidadas (se pierden al_PTR o nunca se recuperan)
  - endpoints administrativos sin comprobacion de sesion

    python brain/hardening_audit.py            # informe
    python brain/hardening_audit.py --json     # para maquinas
    python brain/hardening_audit.py --critico  # solo lo que rompe
"""
from __future__ import annotations

import ast
import json
import re
import sys
from pathlib import Path

BRAIN = Path(__file__).resolve().parent
RAIZ = BRAIN.parent
# Código descargado de otro proyecto (ver _es_vendored). No es nuestro.
VENDOR = RAIZ / "docker" / "mem0" / "mem0-server"

# Ficheros que no son codigo nuestro (copias, backups, entornos virtuales).
IGNORAR_DIRS = {"__pycache__", "venv", ".venv", "node_modules", ".git",
                "dist", "build", ".pytest_cache", "htmlcov"}
IGNORAR_SUFIJOS = {".pyc", ".pyo", ".md", ".txt", ".json", ".yml", ".yaml"}

# Modulos donde tragarse una excepcion SÍ rompe seguridad o datos.
CRITICOS = {"risk_engine", "permission", "auth", "security", "tool_manager",
            "db", "sqlite_store", "store", "context_engine", "gateway",
            "orchestrator", "manager", "live_dashboard", "sessions"}

# Bloquean el event loop si se llaman dentro de async def.
BLOQUEANTES = {
    "time.sleep": "espera en el hilo del servidor",
    "urllib.request.urlopen": "peticion de red bloqueante",
    "requests.get": "peticion de red bloqueante",
    "requests.post": "peticion de red bloqueante",
    "subprocess.run": "proceso externo bloqueante",
    "subprocess.Popen": "proceso externo",
}

# Respuestas "de mentira" que un except puede devolver para disimular un fallo.
VALOR_FALSO = {
    "[]", "{}", '""', "''", "0", "False", "True", "None", "()",
    "list()", "dict()", "set()",
}

SECRETOS = re.compile(
    r"""(?ix)
    (?:api[_-]?key|secret|token|password|passwd|bearer)
    \s*[:=]\s*
    ["'][A-Za-z0-9_\-]{16,}["']
    """)

# Claves con prefijo reconocible. El patrón de arriba exige una asignación con
# comillas, y una clave pegada sola en una línea se le escapaba. Estos
# prefijos no admiten falsos positivos: no son palabras, son firmas.
PREFIJOS = re.compile(
    r"""(?xi)
    \b(
        gsk_          | sk-ant-  | sk-or-v1- | AIza        |
        ghp_ | gho_ | ghu_ | ghs_ | github_pat_ |
        xox[abprs]-  | glpat-   | dckr_pat_  | hf_ | shpat_
    )[A-Za-z0-9_\-]{24,}
    | \bsk-[A-Za-z0-9]{20,}          # OpenAI (sk- a secas, sin sufijo)
    | \bnpm_[A-Za-z0-9]{20,}         # npm
    """)

hallazgos: list[dict] = []


def anota(severidad: str, categoria: str, fichero: Path, linea: int,
          texto: str, por_que: str) -> None:
    relativo = fichero.relative_to(RAIZ) if RAIZ in fichero.parents else fichero
    hallazgos.append({
        "severidad": severidad, "categoria": categoria,
        "fichero": str(relativo).replace("\\", "/"), "linea": linea,
        "detalle": texto[:200], "por_que": por_que,
    })


def ficheros_python() -> list[Path]:
    salida = []
    for p in sorted(RAIZ.rglob("*.py")):
        if any(d in IGNORAR_DIRS for d in p.parts):
            continue
        if _es_vendored(p):
            continue          # código de otro proyecto, descargado al construir
        if p.suffix in IGNORAR_SUFIJOS:
            continue
        salida.append(p)
    return salida


def _es_vendored(p: Path) -> bool:
    """¿Este fichero es código de otro proyecto, descargado?

    `docker/mem0/mem0-server/` lo baja `bajar-mem0.sh` al construir y ni
    siquiera está en el repo (son 185 ficheros de mem0ai/mem0). Contar sus
    claves como secretos de JARVIS es un falso positivo: no son nuestras, no
    las cambiamos nosotros y arreglar «el problema» aquí tocaría código ajeno
    que se pierde en la siguiente actualización.
    """
    try:
        return VENDOR in p.parents
    except Exception:  # noqa: BLE001
        return False


def linea_de(fichero: Path, nodo: ast.AST) -> int:
    return getattr(nodo, "lineno", 0)


# --------------------------------------------------------------------------- #
# 1. Rutas duplicadas o inconsistentes
# --------------------------------------------------------------------------- #
def auditar_rutas() -> None:
    """Rutas repetidas.

    OJO con dos cosas quedahacen falsos positivos muy comunes, y que ya
    aparecieron aqui:
      - `/ui` y `/ui/` NO son la misma ruta: en FastAPI son dos rutas
        distintas, y responder las dos a proposito (una con barra y otra sin)
        es lo habitual. Por eso NO se normaliza la barra final.
      - un `@router.get("")` de un APIRouter con prefix="/live" acaba en "/live",
        no en "/". Por eso se busca el prefix del router en el mismo fichero.
    Sin esas dos correcciones, todo endpoint con sus dos variantes sale como
    duplicado y el informe deja de servir para nada.
    """
    vistas: dict[tuple, list] = {}
    for f in ficheros_python():
        texto = f.read_text(encoding="utf-8", errors="replace")
        try:
            arbol = ast.parse(texto)
        except SyntaxError as exc:
            anota("CRITICAL", "sin-taxis", f, exc.lineno or 0,
                  f"el fichero no compila: {exc.msg}", "no se puede importar")
            continue

        # Prefijos declarados: APIRouter(prefix="/live") o router = APIRouter(...)
        prefijos = {}
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name) \
                    and nodo.func.id.endswith("Router"):
                for kw in nodo.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                        prefijos.setdefault(nodo.func.id, str(kw.value.value))
        # app.include_router(router, prefix="/x")
        incluidos = []
        for nodo in ast.walk(arbol):
            if isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Attribute) \
                    and nodo.func.attr == "include_router":
                for kw in nodo.keywords:
                    if kw.arg == "prefix" and isinstance(kw.value, ast.Constant):
                        incluidos.append(str(kw.value.value))
        extra = incluidos[0] if incluidos else ""

        for nodo in ast.walk(arbol):
            if not isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in nodo.decorator_list:
                if not isinstance(deco, ast.Call) or not isinstance(deco.func, ast.Attribute):
                    continue
                metodo = deco.func.attr.lower()
                if metodo not in ("get", "post", "put", "delete", "patch",
                                  "websocket"):
                    continue
                if not deco.args or not isinstance(deco.args[0], ast.Constant):
                    continue
                ruta = str(deco.args[0].value)
                # Un router con prefix antepone ese prefijo a lo suyo.
                if deco.func.value.__class__ is ast.Name and not extra:
                    ruta = prefijos.get(deco.func.value.id, "") + ruta
                elif extra:
                    ruta = extra + ruta
                clave = (metodo, ruta)
                vistas.setdefault(clave, []).append((f, deco.lineno, nodo.name))

    for (metodo, ruta), sitios in sorted(vistas.items()):
        ficheros = {s[0] for s in sitios}
        if len(ficheros) > 1:
            detalle = " · ".join(f"{s[0].name}:{s[1]}" for s in sitios)
            anota("MEDIUM", "ruta-repetida", sitios[0][0], sitios[0][1],
                  f"{metodo.upper()} {ruta} en {len(sitios)} sitios ({detalle})",
                  "revisar: solo uno puede estar respondiendo")
        elif len(sitios) > 1:
            nombres = {s[2] for s in sitios}
            anota("HIGH", "ruta-duplicada", sitios[0][0], sitios[0][1],
                  f"{metodo.upper()} {ruta} definida {len(sitios)} veces "
                  f"(líneas {', '.join(str(s[1]) for s in sitios)}, "
                  f"funciones {sorted(nombres)})",
                  "solo se registra la primera; la otra es código muerto")


# --------------------------------------------------------------------------- #
# 2+3. Bloqueo del event loop y errores silenciados
# --------------------------------------------------------------------------- #
def _es_bloqueante(nodo: ast.AST) -> str:
    """Devuelve el nombre de la llamada si bloquea el hilo."""
    partes = []
    actual = nodo
    while isinstance(actual, ast.Attribute):
        partes.append(actual.attr)
        actual = actual.value
    if isinstance(actual, ast.Name):
        partes.append(actual.id)
    camino = ".".join(reversed(partes))
    for clave, motivo in BLOQUEANTES.items():
        if camino.endswith(clave) or camino == clave.split(".")[-1]:
            return f"{camino} ({motivo})"
    return ""


def auditar_async_y_except(f: Path, arbol: ast.AST) -> None:
    modulos = {n.name for n in ast.walk(arbol)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}

    for func in ast.walk(arbol):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue

        # --- ¿falla y finge? -------------------------------------------
        for hijo in ast.walk(func):
            if not isinstance(hijo, ast.ExceptHandler):
                continue
            if _esta_explicado(f, hijo):
                # `except: pass` con un comentario encima que explica por que
                # se traga el fallo es CODIGO BUENO: en auth.py y en
                # sqlite_store.py hay varios, y son deliberados.
                continue
            cuerpo = hijo.body
            # except: pass / continue / return  (el fallo desaparece)
            if len(cuerpo) == 1 and isinstance(cuerpo[0], (ast.Pass, ast.Continue)):
                # En un fichero que escribe estado (base de datos, misiones,
                # checkpoints) tragarse el fallo es un bug: se pierde informacion
                # y no se avisa. En una herramienta opcional es lo correcto: que
                # la camara no exista no puede tirar el cerebro.
                critico = _es_critico(f)
                nivel = "HIGH" if critico else "LOW"
                anota(nivel, "error-tragado", f, linea_de(f, hijo),
                      f"`except` que no hace nada en {func.name}()"
                      + (" (fichero que guarda estado)" if critico else ""),
                      "el error se pierde y quien llama cree que fue bien"
                      if critico else
                      "patron defensivo habitual en una pieza opcional")
                continue
            # except: return [] / "" / None  (falso exito)
            if len(cuerpo) == 1 and isinstance(cuerpo[0], ast.Return):
                valor = cuerpo[0].value
                devuelto = None
                if valor is None:
                    devuelto = "None"
                elif isinstance(valor, ast.Constant):
                    devuelto = repr(valor.value)
                elif isinstance(valor, ast.Call):
                    devuelto = ast.unparse(valor)
                if devuelto in VALOR_FALSO:
                    nivel = "HIGH" if _es_critico(f) else "LOW"
                    anota(nivel, "falso-exito", f, linea_de(f, hijo),
                          f"{func.name}() devuelve {devuelto} si falla",
                          "un fallo se disfraza de resultado vacío")

        # --- ¿bloquea el event loop? -----------------------------------
        if isinstance(func, ast.AsyncFunctionDef):
            for hijo in ast.walk(func):
                if not isinstance(hijo, ast.Call):
                    continue
                bloqueo = _es_bloqueante(hijo)
                if bloqueo:
                    anota("HIGH", "bloqueo-event-loop", f, linea_de(f, hijo),
                          f"{bloqueo} dentro de async {func.name}()",
                          "congela TODAS las peticiones mientras espera")


def _es_critico(f: Path) -> bool:
    return bool(CRITICOS & {f.stem, f.parent.name})


def _esta_explicado(f: Path, handler: ast.ExceptHandler) -> bool:
    """¿Hay un comentario que justifica tragarse esta excepcion?

    Un `except: pass` precededido de "# en NTFS chmod no hace nada" es
    codigo correcto: el autor dijo por que se lo come. Marcarlo como bug
    haria que el informe llenase de ruido y que nadie se lo creyera.
    Se mira el comentario de la linea anterior y el que empieza dentro del
    bloque except.
    """
    lineas = f.read_text(encoding="utf-8", errors="replace").splitlines()
    antes = lineas[handler.lineno - 2].strip() if handler.lineno >= 2 else ""
    if antes.startswith("#") and len(antes) > 1:
        return True
    # Se recorre TODO el bloque: el comentario puede estar dos o tres lineas
    # mas abajo, encima del `pass` (asi esta escrito en auth.py, donde explica
    # por que se acepta el token en memoria si no hay permisos de escritura).
    fin = max((getattr(h, "end_lineno", 0) or 0 for h in handler.body), default=0)
    for n in range(handler.lineno - 1, max(handler.lineno - 1, fin)):
        if n < 0 or n >= len(lineas):
            continue
        linea = lineas[n]
        if linea.strip().startswith("#") and len(linea.strip()) > 1:
            return True
        if "#" in linea and not linea.strip().startswith("#"):
            return True
    return False


# --------------------------------------------------------------------------- #
# 4. Colecciones sin límite
# --------------------------------------------------------------------------- #
def _crece_sobre_literal(arbol: ast.AST, nombre: str) -> bool:
    """¿Cada `append` de `nombre` está dentro de un `for` de tamaño conocido?

    `for x in ('a', 'b'): cosas.append(x)` mete dos elementos y termina. No es
    una fuga: el tope está escrito en el código y no depende de cuántas
    peticiones lleguen al proceso.
    """
    for nodo in ast.walk(arbol):
        if not isinstance(nodo, ast.For):
            continue
        # El iterable tiene que ser literal (tupla, lista o conjunto de
        # constantes), no una variable que pueda no terminar nunca.
        if not isinstance(nodo.iter, (ast.Tuple, ast.List, ast.Set)):
            if not _es_range_literal(nodo.iter):
                continue
        elif not _es_literal(nodo.iter):
            continue
        if any(ast.unparse(h.func.value) == nombre
               for h in ast.walk(nodo)
               if isinstance(h, ast.Call) and isinstance(h.func, ast.Attribute)
               and h.func.attr in ("append", "add", "update", "extend", "insert")):
            return True
    return False


def _es_literal(nodo: ast.AST) -> bool:
    """¿Es una constante, o una tupla/lista de constantes (anidada)?

    ((b'ic08', 256), (b'ic09', 512)) son cuatro constantes metidas en dos
    tuplas: sigue siendo un tope fijo y escribible en el código.
    """
    if isinstance(nodo, ast.Constant):
        return True
    if isinstance(nodo, (ast.Tuple, ast.List, ast.Set)):
        return bool(nodo.elts) and all(_es_literal(e) for e in nodo.elts)
    return False


def _es_range_literal(nodo: ast.AST) -> bool:
    """`range(3)` o `range(1, 9, 2)`: acotado si los tres argumentos son fijos.

    `range(n)` con `n` variable NO cuenta: ahí el bucle depende de un valor
    que no está escrito aquí.
    """
    if not (isinstance(nodo, ast.Call) and isinstance(nodo.func, ast.Name)
            and nodo.func.id == "range"):
        return False
    return bool(nodo.args) and all(isinstance(a, ast.Constant) for a in nodo.args)


def auditar_fugas(f: Path, arbol: ast.AST) -> None:
    for nodo in arbol.body:
        if not isinstance(nodo, ast.Assign) or len(nodo.targets) != 1:
            continue
        objetivo = nodo.targets[0]
        if not isinstance(objetivo, ast.Name):
            continue
        nombre = objetivo.id
        es_lista = isinstance(nodo.value, (ast.List, ast.Dict, ast.Set))
        if not es_lista:
            continue
        # Solo colecciones de MODULO: son las que viven todo el proceso.
        if nombre.startswith("_") and not nombre.isupper():
            continue

        crece = any(
            isinstance(h, ast.Call) and isinstance(h.func, ast.Attribute)
            and h.func.attr in ("append", "add", "update", "extend", "insert")
            and ast.unparse(h.func.value) == nombre
            for h in ast.walk(arbol))
        if not crece:
            continue
        # ¿Hay algo que las recorte?
        texto = ast.unparse(arbol)
        recorta = any(d in texto for d in (
            f"{nombre}.clear()", f"del {nombre}", f"{nombre}.pop(",
            f"{nombre}.remove(", f"{nombre}[:", f"{nombre}[-",
            f"{nombre}.popitem("))
        if not recorta:
            # Si los `append` están dentro de un `for` sobre una tupla o lista
            # LITERAL, el numero de vueltas se sabe al leer el codigo y no hay
            # crecimiento sin limite. Sin esta excepcion, cualquier script que
            #resserie un par de iconos aparece como una fuga de memoria.
            acotado = _crece_sobre_literal(arbol, nombre)
            if acotado:
                continue
            anota("MEDIUM", "fuga-memoria", f, nodo.lineno,
                  f"`{nombre}` crece sin que nada lo recorte",
                  "cada petición deja algo dentro y el proceso no baja")


# --------------------------------------------------------------------------- #
# 5. Bucles ocupados
# --------------------------------------------------------------------------- #
def auditar_bucles(f: Path, arbol: ast.AST) -> None:
    for func in ast.walk(arbol):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for bucle in ast.walk(func):
            if not isinstance(bucle, ast.While):
                continue
            prueba = ast.unparse(bucle.test)
            if "True" not in prueba:
                continue
            esperas = _esperas_del_bucle(bucle)
            if len(esperas) < 2:
                for valor in esperas:
                    if valor < 0.1:
                        anota("HIGH", "bucle-ocupado", f,
                              linea_de(f, bucle),
                              f"while True con espera fija de {valor}s en "
                              f"{func.name}()",
                              "consume CPU en vez de esperar")
                continue
            # Mas de una espera distinta: el bucle se adapta a si hay algo
            # que hacer. No es un bucle ocupado, pero conviene dejarlo escrito
            # para que se vea el ritmo corto que usa cuando si hay trabajo.
            anota("LOW", "espera-adaptativa", f,
                  linea_de(f, bucle),
                  f"while True en {func.name}() espera {sorted(esperas)}s segun "
                  f"haya trabajo o no",
                  "informativo: el ritmo corto solo se usa cuando hay algo que hacer")


def _esperas_del_bucle(bucle: ast.AST) -> set:
    """Valores de `sleep(...)`/`wait(...)` que aparecen en este bucle."""
    salida = set()
    for llamada in ast.walk(bucle):
        if not isinstance(llamada, ast.Call) or not llamada.args:
            continue
        if not ast.unparse(llamada.func).endswith(("sleep", "wait")):
            continue
        arg = llamada.args[0]
        if isinstance(arg, ast.Constant) and isinstance(arg.value, (int, float)):
            salida.add(float(arg.value))
    return salida


# --------------------------------------------------------------------------- #
# 6. Tareas creadas y olvidadas
# --------------------------------------------------------------------------- #
def auditar_tareas(f: Path, arbol: ast.AST) -> None:
    for func in ast.walk(arbol):
        if not isinstance(func, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        if not isinstance(func, ast.AsyncFunctionDef):
            continue
        cuerpo = ast.unparse(func)
        if "create_task" not in cuerpo:
            continue
        # ¿se guarda el resultado en algun sitio?
        guarda = re.search(r"=\s*[\w\.]*create_task\(", cuerpo) or \
            re.search(r"add_done_callback", cuerpo) or \
            re.search(r"await\s+[\w\.]*create_task\(", cuerpo)
        if not guarda:
            # No es un bug: desde Python 3.8 el bucle de eventos guarda una
            # referencia fuerte a la tarea, asi que no se pierde. Lo que si
            # cambia es que no hay un sitio desde donde cancelarla, y en el
            # caso del WebSocket es lo correcto (el trabajo se difunde a otros
            # clientes aunque se caiga quien la pidio).
            anota("LOW", "tarea-sin-manojo", f, func.lineno,
                  f"{func.name}() lanza una tarea sin guardar la referencia",
                  "no se puede cancelar desde ahi (informativo, no es un fallo)")


# --------------------------------------------------------------------------- #
# 7. Secretos en el código
# --------------------------------------------------------------------------- #
def auditar_secretos(ficheros: list[Path] | None = None) -> None:
    """Busca claves escritas en el código.

    `ficheros` se puede pasar para poder probarla sin tocar el repo entero.
    """
    permitidos = re.compile(r"(example|plantilla|placeholder|changeme|todo|cambia|"
                            r"tu_clave|aqui|xxx+|\.\.\.|<.*>|prueba|falsa|fake|"
                            r"dummy|test|ejemplo|no_valida|no_existe)", re.I)
    objetivo = (ficheros if ficheros is not None else
                ficheros_python() + list(RAIZ.glob("*.yml")) + list(RAIZ.glob("*.yaml")))
    for f in objetivo:
        texto = f.read_text(encoding="utf-8", errors="replace")
        for num, linea in enumerate(texto.splitlines(), 1):
            if linea.lstrip().startswith("#"):
                continue
            for hallazgo in SECRETOS.finditer(linea):
                trozo = linea[hallazgo.start():hallazgo.end()]
                if permitidos.search(trozo):
                    continue
                if f.name.endswith(".yml") or f.name.endswith(".yaml"):
                    continue          # las variables llegan por entorno
                anota("CRITICAL", "secreto-en-codigo", f, num,
                      "clave escrita en el fichero: "
                      + trozo[:40] + "...",
                      "quien clonee el repo tiene la clave; rotala")
                break
            # Claves con prefijo reconocible, esterEAK donde esten: sueltas en
            # una línea, sin comillas y sin nombre de variable. El patrón de
            # arriba solo las pilla si hay un `api_key = "..."`, y una clave
            # pegada a mano en un fichero se cuela por ahí.
            for hallazgo in PREFIJOS.finditer(linea):
                trozo = hallazgo.group(0)
                if permitidos.search(linea[max(0, hallazgo.start() - 60):
                                            hallazgo.end()]):
                    continue
                # Solo se enseña el principio: la clave entera no sale por
                # pantalla ni queda en el informe.
                anota("CRITICAL", "clave-de-proveedor-en-codigo", f, num,
                      f"clave de proveedor con prefijo «{trozo[:6]}…»"
                      f" escrita en el fichero ({len(trozo)} caracteres)",
                      "quien clonee el repo tiene la clave y puede usarla; "
                      "rotala en la web del proveedor")
                break


# --------------------------------------------------------------------------- #
# 8. Endpoints administrativos sin sesión
# --------------------------------------------------------------------------- #
def auditar_auth() -> None:
    """Endpoints de /api/ sin comprobacion de sesion propia.

    Si el brain tiene un middleware que exige el token a TODO /api/, las
    comprobaciones por endpoint sobran: es lo que se corrigio en este pase
    (antes `/api/board/terminal` abria una shell sin pedir nada). Con el
    middleware puesto, avisar de cada endpoint seria ruido; si no esta, se
    avisa endpoint a endpoint, que es el agujero real.
    """
    middleware_pone = False
    for f in ficheros_python():
        if "main.py" not in f.name:
            continue
        texto = f.read_text(encoding="utf-8", errors="replace")
        if "_token_en_las_api" in texto and '@app.middleware("http")' in texto:
            middleware_pone = True
    if middleware_pone:
        print("  (la proteccion de /api/ la pone el middleware _token_en_las_api; "
              "los endpoints sin check propio ya no son un agujero)")
        return

    for f in ficheros_python():
        texto = f.read_text(encoding="utf-8", errors="replace")
        if '"/api/' not in texto and "'/api/" not in texto:
            continue
        try:
            arbol = ast.parse(texto)
        except SyntaxError:
            continue
        # ¿el fichero tiene alguna comprobacion de sesion?
        if not re.search(r"_authorized|check_auth|require_token|verificar_token|"
                         r"Depends\(.*auth|X-Jarvis-Token|session", texto, re.I):
            continue
        for nodo in ast.walk(arbol):
            if not isinstance(nodo, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            for deco in nodo.decorator_list:
                ruta = ""
                if isinstance(deco, ast.Call) and deco.args and \
                        isinstance(deco.args[0], ast.Constant):
                    ruta = str(deco.args[0].value)
                if not ruta.startswith("/api/"):
                    continue
                cuerpo = ast.unparse(nodo)
                if not re.search(r"_authorized|Depends|token|session", cuerpo, re.I):
                    anota("MEDIUM", "endpoint-sin-comprobar", f, nodo.lineno,
                          f"{ruta} (en {nodo.name}) no comprueba sesion en la "
                          f"propia funcion",
                          "depende de que el router padre la compruebe: "
                          "si cambia, esta queda abierta")


# --------------------------------------------------------------------------- #
# 9. Marcadores pendientes
# --------------------------------------------------------------------------- #
def auditar_todos() -> None:
    patron = re.compile(r"#\s*(TODO|FIXME|XXX|HACK|PENDING)\b[:(]?\s*(.*)")
    for f in ficheros_python():
        for num, linea in enumerate(
                f.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            m = patron.search(linea)
            if m:
                anota("LOW", "pendiente", f, num,
                      f"{m.group(1)}: {m.group(2)[:80] or '(sin detalle)'}",
                      "quedó sin hacer a propósito")


# --------------------------------------------------------------------------- #
def main() -> int:
    print("AUDITORIA DE HARDENING · analisis estatico del codigo real\n")
    for f in ficheros_python():
        try:
            arbol = ast.parse(f.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError as exc:
            anota("CRITICAL", "sin-taxis", f, exc.lineno or 0,
                  f"el fichero no compila: {exc.msg}", "no se puede importar")
            continue
        auditar_async_y_except(f, arbol)
        auditar_fugas(f, arbol)
        auditar_bucles(f, arbol)
        auditar_tareas(f, arbol)
    auditar_rutas()
    auditar_secretos()
    auditar_auth()
    auditar_todos()

    orden = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, "OPTIMIZATION": 4}
    hallazgos.sort(key=lambda h: (orden.get(h["severidad"], 9), h["categoria"],
                                  h["fichero"], h["linea"]))
    cuenta: dict = {}
    for h in hallazgos:
        cuenta[h["severidad"]] = cuenta.get(h["severidad"], 0) + 1

    if "--json" in sys.argv:
        print(json.dumps(hallazgos, ensure_ascii=False, indent=2))
        return 0

    actual = None
    for h in hallazgos:
        if "--critico" in sys.argv and h["severidad"] not in ("CRITICAL", "HIGH"):
            continue
        if h["severidad"] != actual:
            actual = h["severidad"]
            print(f"\n{'=' * 70}\n{actual}\n{'=' * 70}")
        print(f"  {h['categoria']:22} {h['fichero']}:{h['linea']}")
        print(f"      {h['detalle']}")
        print(f"      -> {h['por_que']}")

    print(f"\n{'=' * 70}")
    print("total:", len(hallazgos), cuenta)
    return 0


if __name__ == "__main__":
    sys.exit(main())