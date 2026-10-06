"""Lectura de contenido de archivos para el overlay (preview de documentos).

Soporta: texto plano (.txt/.md/.json/.csv/.log...), PDF (vía pypdf),
DOCX/ODT/PPTX/XLSX (son ZIP con XML; se extrae el texto con el stdlib)
y RTF (conjunto mínimo de control words).

Para DOCX y PDF también se devuelven imágenes inline (base64 data-URIs)
para que el overlay pueda mostrarlas junto al texto.
"""
import os
import re
import zipfile
import logging
import html as _htmllib

log = logging.getLogger("brain.file_reader")

MAX_CHARS = 60000
_MAX_IMAGES = 20
_MAX_IMG_BYTES = 512_000  # bytes; imágenes más grandes se redimensionan

_TEXT_EXTS = {
    ".txt", ".md", ".markdown", ".log", ".json", ".csv", ".tsv", ".xml",
    ".ini", ".cfg", ".conf", ".yaml", ".yml", ".py", ".js", ".ts", ".html",
    ".css", ".sh", ".bat", ".ps1", ".sql", ".rst", ".jsx", ".tsx", ".java",
    ".c", ".cpp", ".h", ".hpp", ".rs", ".go", ".rb", ".php", ".r", ".swift",
    ".kt", ".vue", ".svelte", ".toml", ".env", ".dockerfile", ".makefile",
    ".cs", ".scala", ".lua", ".pl", ".dart", ".ex", ".exs", ".erl", ".hs",
    ".clj", ".f", ".f90", ".pas", ".asm", ".m", ".mm",
}

_DOC_EXTS = {".pdf", ".docx", ".odt", ".pptx", ".xlsx", ".rtf"}


def _read_text(path: str) -> str:
    for enc in ("utf-8-sig", "utf-8", "latin-1"):
        try:
            with open(path, "r", encoding=enc, errors="strict") as f:
                return f.read(MAX_CHARS + 4000)
        except (UnicodeDecodeError, UnicodeError):
            continue
    with open(path, "r", encoding="utf-8", errors="replace") as f:
        return f.read(MAX_CHARS + 4000)


def _strip_xml_text(xml: str) -> str:
    # Quitar etiquetas y decodificar entidades básicas conservando párrafos.
    xml = xml.replace("</w:p>", "\n").replace("</text:p>", "\n")
    xml = re.sub(r"<[^>]+>", " ", xml)
    xml = xml.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    xml = xml.replace("&quot;", '"').replace("&apos;", "'").replace("&#10;", "\n")
    return re.sub(r"[ \t]{2,}", " ", xml)


def _read_docx(path: str) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("word/document.xml").decode("utf-8", "ignore")
    return _strip_xml_text(xml)


def _read_odt(path: str) -> str:
    with zipfile.ZipFile(path) as z:
        xml = z.read("content.xml").decode("utf-8", "ignore")
    return _strip_xml_text(xml)


def _read_pdf(path: str) -> str:
    from pypdf import PdfReader
    reader = PdfReader(path, strict=False)
    out = []
    for page in reader.pages[:40]:
        out.append(page.extract_text() or "")
    return "\n".join(out)


def _read_pptx(path: str) -> str:
    import xml.etree.ElementTree as ET
    ns = {"a": "http://schemas.openxmlformats.org/drawingml/2006/main"}
    parts = []
    with zipfile.ZipFile(path) as z:
        slides = [n for n in z.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", n)]
        slides.sort(key=lambda n: int(re.search(r"slide(\d+)", n).group(1)))
        for name in slides:
            raw = z.read(name)
            texts = []
            try:
                root = ET.fromstring(raw)
                texts = [el.text for el in root.findall(".//a:t", ns) if el.text and el.text.strip()]
            except Exception:
                texts = re.findall(rb"<a:t[^>]*>(.*?)</a:t>", raw, re.S)
                texts = [t.decode("utf-8", "ignore").strip() for t in texts if t.strip()]
            if texts:
                parts.append(" ".join(texts))
    return "\n\n".join(parts)


def _read_xlsx(path: str) -> str:
    import xml.etree.ElementTree as ET
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    shared = []
    with zipfile.ZipFile(path) as z:
        if "xl/sharedStrings.xml" in z.namelist():
            root = ET.fromstring(z.read("xl/sharedStrings.xml"))
            shared = [si.text or "" for si in root.findall(".//m:t", ns)]
        rows = []
        for name in z.namelist():
            if not re.match(r"xl/worksheets/sheet\d+\.xml$", name):
                continue
            root = ET.fromstring(z.read(name))
            for row in root.findall(".//m:row", ns):
                cells = []
                for c in row.findall("m:c", ns):
                    v = c.find("m:v", ns)
                    if v is None or v.text is None:
                        cells.append("")
                        continue
                    if c.get("t") == "s":
                        idx = int(v.text)
                        cells.append(shared[idx] if idx < len(shared) else "")
                    else:
                        cells.append(v.text)
                rows.append("\t".join(cells))
    return "\n".join(rows)


def _read_rtf(path: str) -> str:
    raw = open(path, "r", encoding="latin-1").read(MAX_CHARS + 4000)
    raw = re.sub(r"\\u([-]?\d+)", lambda m: chr(int(m.group(1)) % 65536), raw)
    raw = re.sub(r"\\'[0-9a-fA-F]{2}", "", raw)
    raw = re.sub(r"\\([a-z]+)(-?\d+)? ?", "", raw)
    raw = raw.replace("\\{", "{").replace("\\}", "}").replace("\\\\", "\\")
    return raw


# ------------------------------------------------------------------ #
#  Helpers para imágenes inline (base64 data-URIs)
# ------------------------------------------------------------------ #

def _downscale_image(raw: bytes, max_w: int = 800) -> bytes:
    """Redimensiona imagen con PIL si es mayor que max_w de ancho."""
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(raw))
        if img.width > max_w:
            ratio = max_w / img.width
            img = img.resize((max_w, int(img.height * ratio)), Image.LANCZOS)
        buf = io.BytesIO()
        fmt = "RGBA" if img.mode == "RGBA" else "RGB"
        img.convert(fmt).save(buf, "PNG", optimize=True)
        return buf.getvalue()
    except Exception:
        return raw


def _img_to_data_uri(raw: bytes) -> str:
    """Convierte bytes de imagen a data-URI (base64)."""
    try:
        from PIL import Image
        import io
        img = Image.open(io.BytesIO(raw))
        if img.mode == "RGBA":
            mime, fmt = "image/png", "PNG"
        else:
            img = img.convert("RGB")
            mime, fmt = "image/jpeg", "JPEG"
        buf = io.BytesIO()
        img.save(buf, fmt, quality=85)
        b64 = _b64enc(buf.getvalue())
    except Exception:
        mime = "image/png"
        b64 = _b64enc(raw)
    return f"data:{mime};base64,{b64}"


def _b64enc(data: bytes) -> str:
    import base64
    return base64.b64encode(data).decode()


# ------------------------------------------------------------------ #
#  DOCX → HTML con imágenes inline
# ------------------------------------------------------------------ #

def _read_docx_html(path: str) -> str:
    """Extrae DOCX a HTML con <p> + <img src='data:...'> inline."""
    import xml.etree.ElementTree as ET
    ns = {
        "w":  "http://schemas.openxmlformats.org/wordprocessingml/2006/main",
        "a":  "http://schemas.openxmlformats.org/drawingml/2006/main",
        "r":  "http://schemas.openxmlformats.org/officeDocument/2006/relationships",
        "wp": "http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing",
    }
    parts: list[str] = []
    img_count = 0

    with zipfile.ZipFile(path) as z:
        # -- 1) Recopilar imágenes del word/media/ --
        media: dict[str, str] = {}  # nombre_zip → data_uri
        for name in z.namelist():
            if not name.startswith("word/media/"):
                continue
            ext = name.rsplit(".", 1)[-1].lower()
            if ext in ("emf", "wmf"):
                continue
            try:
                raw = z.read(name)
                if len(raw) > _MAX_IMG_BYTES:
                    raw = _downscale_image(raw)
                media[name] = _img_to_data_uri(raw)
            except Exception:
                continue

        # -- 2) Relaciones rId → media path --
        rid_map: dict[str, str] = {}
        rels_key = "word/_rels/document.xml.rels"
        if rels_key in z.namelist():
            rels_text = z.read(rels_key).decode("utf-8", "ignore")
            for m in re.finditer(
                r'Id="(rId\d+)"[^>]*Target="([^"]+)"', rels_text
            ):
                rid, target = m.groups()
                target = target.replace("\\", "/")
                if not target.startswith("word/"):
                    target = "word/" + target
                rid_map[rid] = target

        # -- 3) Parsear document.xml --
        doc_xml = z.read("word/document.xml")
        root = ET.fromstring(doc_xml)
        for para in root.findall(".//w:body//w:p", ns):
            # Texto de las runs
            texts: list[str] = []
            for run in para.findall(".//w:r", ns):
                t_el = run.find("w:t", ns)
                if t_el is not None and t_el.text:
                    texts.append(_htmllib.escape(t_el.text))
            # Imágenes incrustadas (<a:blip>)
            imgs: list[str] = []
            for blip in para.findall(".//a:blip", ns):
                rid = blip.get(
                    "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}embed",
                    "",
                )
                target = rid_map.get(rid, "")
                if target in media and img_count < _MAX_IMAGES:
                    imgs.append(
                        f'<div class="di"><img src="{media[target]}" /></div>'
                    )
                    img_count += 1
            line = "".join(texts)
            if line.strip():
                parts.append(f"<p>{line}</p>")
            parts.append("".join(imgs))

    return "\n".join(parts)


# ------------------------------------------------------------------ #
#  PDF → HTML con imágenes inline
# ------------------------------------------------------------------ #

def _read_pdf_html(path: str) -> str:
    """Extrae PDF a HTML con texto + imágenes embebidas (pypdf ≥ 6)."""
    from pypdf import PdfReader
    reader = PdfReader(path, strict=False)
    parts: list[str] = []
    img_count = 0

    for idx, page in enumerate(reader.pages[:40]):
        # Texto de la página
        text = (page.extract_text() or "").strip()
        if text:
            escaped = _htmllib.escape(text).replace("\n", "<br>")
            parts.append(
                f'<p class="pdf-page"><span class="pg">Página {idx + 1}</span>{escaped}</p>'
            )
        # Imágenes embebidas en la página
        try:
            for img_file in page.images:
                if img_count >= _MAX_IMAGES:
                    break
                try:
                    raw = img_file.data
                    if len(raw) < 200:
                        continue
                    if len(raw) > _MAX_IMG_BYTES:
                        raw = _downscale_image(raw)
                    uri = _img_to_data_uri(raw)
                    parts.append(f'<div class="di"><img src="{uri}" /></div>')
                    img_count += 1
                except Exception:
                    continue
        except Exception:
            continue

    return "\n".join(parts)


# ------------------------------------------------------------------ #
#  API pública
# ------------------------------------------------------------------ #

def preview(path: str) -> dict:
    """Devuelve {ok, text?, html?, reason?, truncated?}."""
    try:
        if not path or not os.path.exists(path):
            return {"ok": False, "reason": "El archivo no existe"}
        ext = os.path.splitext(path)[1].lower()
        html_content: str | None = None

        if ext in _TEXT_EXTS:
            text = _read_text(path)
        elif ext == ".pdf":
            text = _read_pdf(path)
            try:
                html_content = _read_pdf_html(path)
            except Exception as e:
                log.debug("PDF html fallback: %s", e)
        elif ext == ".docx":
            text = _read_docx(path)
            try:
                html_content = _read_docx_html(path)
            except Exception as e:
                log.debug("DOCX html fallback: %s", e)
        elif ext == ".odt":
            text = _read_odt(path)
        elif ext == ".pptx":
            text = _read_pptx(path)
        elif ext == ".xlsx":
            text = _read_xlsx(path)
        elif ext == ".rtf":
            text = _read_rtf(path)
        else:
            return {"ok": False, "reason": f"No sé leer archivos .{ext or '?'} (puedo abrirlos, no previsualizarlos)"}
        text = re.sub(r"\n{3,}", "\n\n", (text or "").strip())
        has_html = bool(html_content)
        if not text and not has_html:
            return {"ok": False, "reason": "No encuentro texto extraíble en el documento"}
        truncated = len(text) > MAX_CHARS
        return {
            "ok": True,
            "text": text[:MAX_CHARS],
            "html": html_content[:MAX_CHARS * 2] if html_content else None,
            "truncated": truncated,
        }
    except Exception as e:
        log.warning("preview falló para %s: %s", path, e)
        return {"ok": False, "reason": f"No pude leer el documento: {e}"}