"""Herramientas de archivo del Coder, restringidas al workspace configurado.

Toda ruta se resuelve y se valida contra el workspace: cualquier intento de salir
(`..`, rutas absolutas, symlinks que escapen) se rechaza. La ejecución de código
queda para la Fase 3.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path, PureWindowsPath
from typing import Any


class ToolError(Exception):
    """Uso inválido de una herramienta (ruta fuera del workspace, no existe, etc.)."""


# Carpetas de ruido que grep/glob NO deben recorrer: control de versiones, artefactos de
# build y dependencias. Sin esto, grep entra a `.git/` (índice binario) y a `build/`, gasta
# rondas del Coder y ensucia los resultados con basura no-fuente.
_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "build",
        "target",
        "dist",
        ".gradle",
        ".venv",
        "__pycache__",
        ".idea",
        ".next",
        "coverage",
        "bin",
        "out",
    }
)


def _skipped(rel: Path) -> bool:
    """True si la ruta relativa cae bajo una carpeta de ruido."""
    return any(part in _SKIP_DIRS for part in rel.parts)


# Extensiones de texto/código que grep recorre. Filtrar por extensión es MUCHO más barato
# que abrir cada archivo para olfatear binarios: en un repo de ~13k archivos, grep sobre la
# raíz pasó de ~90 s (abría todo) a <2 s. Los binarios (imágenes, .jar, `.git/index`) quedan
# fuera por no estar en la lista. El modelo aún puede grepear un archivo puntual por su ruta.
_TEXT_EXTS = frozenset(
    {
        ".kt",
        ".kts",
        ".java",
        ".py",
        ".pyi",
        ".xml",
        ".json",
        ".gradle",
        ".properties",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".vue",
        ".md",
        ".txt",
        ".yaml",
        ".yml",
        ".sql",
        ".html",
        ".css",
        ".scss",
        ".sh",
        ".bat",
        ".cfg",
        ".ini",
        ".toml",
        ".pro",
        ".graphql",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cs",
        ".go",
        ".rb",
        ".rs",
        ".php",
        ".swift",
        ".dart",
    }
)
# Tope de tamaño en el RECORRIDO (un archivo pedido por su ruta se lee siempre). Generoso a
# propósito: los logs de evidencia (logcat .txt), los bundles .js de web y los .json de labels
# pesan 1-3 MB y son justo lo que hay que leer para diagnosticar un flujo. Con el pre-filtro de
# una pasada, grepearlos es barato; saltárselos daba falsos "sin resultados".
_MAX_GREP_BYTES = 8 * 1024 * 1024


def _grep_candidate(rel: Path, abs_path: Path) -> bool:
    """True si el archivo debe entrar a grep: no es ruido, es de texto y no es gigante."""
    if _skipped(rel) or abs_path.suffix.lower() not in _TEXT_EXTS:
        return False
    try:
        return abs_path.stat().st_size <= _MAX_GREP_BYTES
    except OSError:
        return False


def _resolve(workspace: Path, rel: str) -> Path:
    # SANITIZADOR DE RUTAS: única puerta de entrada de toda ruta suministrada por el modelo.
    # Rechaza rutas absolutas/con unidad y, tras resolver symlinks, exige que el destino quede
    # DENTRO del workspace. Por eso los file-ops que usan su resultado están marcados # NOSONAR
    # (path injection): el path ya viene validado aquí, no directo del dato no confiable.
    # Rechaza de entrada rutas absolutas o con unidad (C:..., \\server, /etc): el Coder
    # solo direcciona con rutas relativas dentro del workspace.
    if Path(rel).is_absolute() or PureWindowsPath(rel).drive or PureWindowsPath(rel).is_absolute():
        raise ToolError(f"Ruta no relativa no permitida: {rel}")
    workspace = Path(workspace).resolve()
    target = (workspace / rel).resolve()  # NOSONAR: validada abajo contra el workspace
    if target != workspace and workspace not in target.parents:
        raise ToolError(f"Ruta fuera del workspace: {rel}")
    return target


def leer_archivo(workspace: Path, ruta: str) -> str:
    p = _resolve(workspace, ruta)
    if not p.is_file():
        raise ToolError(f"No existe el archivo: {ruta}")
    return p.read_text(encoding="utf-8")  # NOSONAR: path validado por _resolve()


def escribir_archivo(workspace: Path, ruta: str, contenido: str) -> str:
    p = _resolve(workspace, ruta)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(contenido, encoding="utf-8")  # NOSONAR: path validado por _resolve()
    return f"escrito: {ruta} ({len(contenido)} chars)"


def listar_directorio(workspace: Path, ruta: str = ".") -> list[str]:
    p = _resolve(workspace, ruta)
    if not p.is_dir():
        raise ToolError(f"No es un directorio: {ruta}")
    return sorted(e.name + ("/" if e.is_dir() else "") for e in p.iterdir())  # NOSONAR: _resolve()


# --- Navegación de código (todas confinadas al workspace) ---


def _inside(ws: Path, p: Path) -> bool:
    """True si `p` (tras resolver symlinks) sigue dentro del workspace."""
    rp = p.resolve()
    return rp == ws or ws in rp.parents


def _walk_files(base: Path, ws: Path) -> list[Path]:
    """Archivos bajo `base`, **podando en el recorrido** las carpetas de ruido.

    Clave para el rendimiento: `rglob("*")` enumera TODO y filtra después. En el repo objetivo
    eso son 91.708 rutas, de las cuales `build/` aporta 88.400 (96%). Podando con os.walk no
    se desciende siquiera a esas carpetas.
    """
    out: list[Path] = []
    for root, dirs, filenames in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]  # poda in-place: no desciende
        rootp = Path(root)
        for fn in filenames:
            p = rootp / fn
            if _inside(ws, p):  # descarta symlinks que escapen del workspace
                out.append(p)
    return out


def _glob_to_regex(patron: str) -> re.Pattern[str]:
    """Traduce un patrón glob a regex sobre la ruta relativa posix.
    `**` cruza separadores, `*`/`?` no. Permite casar sin recorrer el árbol de nuevo."""
    parts = patron.split("/")
    chunks: list[str] = []
    for i, seg in enumerate(parts):
        last = i == len(parts) - 1
        if seg == "**":
            chunks.append(".*" if last else "(?:.*/)?")
            continue
        seg_re = ""
        for ch in seg:
            if ch == "*":
                seg_re += "[^/]*"
            elif ch == "?":
                seg_re += "[^/]"
            else:
                seg_re += re.escape(ch)
        chunks.append(seg_re if last else seg_re + "/")
    return re.compile("^" + "".join(chunks) + "$")


def _glob_once(ws: Path, patron: str, files: list[Path] | None = None) -> list[str]:
    try:
        rx = _glob_to_regex(patron)
    except re.error:
        return []
    if files is None:
        files = _walk_files(ws, ws)
    out: list[str] = []
    for p in files:
        rel = p.relative_to(ws).as_posix()
        if rx.match(rel):
            out.append(rel)
    return sorted(out)


def _glob_variants(patron: str) -> list[str]:
    """Variantes tolerantes del patrón. El modelo escribe patrones con semántica de shell
    (`**/dir/**`, `*dir*/*sub*`) que pathlib expande distinto y devuelve vacío en silencio,
    gastando rondas. Se reintenta con las formas que casi siempre son lo que quería."""
    alts: list[str] = []
    p = patron.rstrip("/")
    if p.endswith("/**"):  # "a/**" en pathlib no incluye los archivos: hace falta "/**/*"
        alts.append(p + "/*")
    if not p.startswith("**/"):  # buscar en cualquier profundidad, no solo en la raíz
        alts.append("**/" + p)
        if p.endswith("/**"):
            alts.append("**/" + p + "/*")
    if "/" not in p and not p.startswith("*"):  # nombre suelto -> por todo el árbol
        alts.append(f"**/*{p}*")
    return alts


def glob(workspace: Path, patron: str) -> list[str]:
    """Rutas de archivo (relativas) que casan el patrón glob dentro del workspace."""
    if ".." in patron or patron.startswith(("/", "\\")) or PureWindowsPath(patron).drive:
        raise ToolError(f"Patrón no permitido: {patron}")
    ws = Path(workspace).resolve()
    files = _walk_files(ws, ws)  # un solo recorrido podado, reutilizado por las variantes
    out = _glob_once(ws, patron, files)
    if out:
        return out
    for alt in _glob_variants(patron):  # reintento tolerante antes de rendirse
        out = _glob_once(ws, alt, files)
        if out:
            return out
    return []


def grep(
    workspace: Path,
    regex: str,
    ruta: str = ".",
    max_resultados: int = 200,
    contexto: int = 0,
) -> list[str]:
    """Líneas que casan `regex` bajo `ruta`. Formato: `archivo:linea: contenido`.

    Con `contexto=N` devuelve además las N líneas de alrededor de cada hit (como `grep -C`),
    para ver el código que rodea al match sin una segunda llamada a read_range: el modelo
    acertaba el archivo pero fallaba la ventana al leer."""
    base = _resolve(workspace, ruta)
    ws = Path(workspace).resolve()
    # Una ruta inexistente (typo del modelo) devolvía "(sin resultados)": una señal FALSA que le
    # hace concluir "aquí no hay nada" y seguir por mal camino. Debe fallar ruidosamente.
    if not base.exists():
        raise ToolError(f"No existe la ruta: {ruta}")
    try:
        pat = re.compile(regex)
    except re.error as exc:
        raise ToolError(f"Regex inválida: {exc}") from exc
    if base.is_file():
        # Archivo puntual pedido por su ruta: respétalo tal cual (el modelo eligió).
        files = [base]
    else:
        # Recorrido PODADO (no desciende a build/.git/…) + filtro por extensión y tamaño.
        files = [p for p in _walk_files(base, ws) if _grep_candidate(p.relative_to(ws), p)]
    # Pre-filtro: una sola pasada de regex sobre TODO el contenido (con MULTILINE, para que
    # ^/$ conserven la semántica por línea). Solo si hay match se parte en líneas. Sin esto
    # eran millones de `pat.search(line)` — un grep sin resultados sobre ~1700 .kt tardaba 75 s.
    try:
        prefilter = re.compile(regex, re.MULTILINE)
    except re.error:  # ya validada arriba; por si el flag cambia la compilación
        prefilter = pat
    out: list[str] = []
    for f in files:
        if not _inside(ws, f):  # symlink que escapa del workspace
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="replace")  # NOSONAR
        except OSError:
            continue
        if not prefilter.search(content):
            continue
        rel = f.relative_to(ws).as_posix()
        lines = content.splitlines()
        for i, line in enumerate(lines, 1):
            if not pat.search(line):
                continue
            if contexto > 0:
                lo, hi = max(1, i - contexto), min(len(lines), i + contexto)
                out.append(f"{rel}:{i}:")
                for j in range(lo, hi + 1):
                    marca = ">" if j == i else " "
                    out.append(f"  {marca}{j}: {lines[j - 1][:200]}")
            else:
                out.append(f"{rel}:{i}: {line.strip()[:200]}")
            if len(out) >= max_resultados:
                return out
    return out


# Declaraciones de alto nivel (Kotlin/Java/Python/TS/JS/…): fun/class/object/interface/enum/def.
# Da el "esqueleto" de un archivo con sus números de línea sin leerlo entero: el modelo salta a
# la función correcta con read_range. Deliberadamente NO lista val/var (serían ruido de campos).
_DECL_RE = re.compile(
    r"^\s*(?:@[\w.]+\s*)*"
    r"(?:(?:public|private|protected|internal|open|final|abstract|sealed|data|inner|"
    r"companion|static|suspend|override|inline|operator|infix|export|external)\s+)*"
    r"(?:fun|class|object|interface|enum\s+class|enum|def|function)\b"
)


def outline(workspace: Path, ruta: str, max_items: int = 200) -> list[str]:
    """Esqueleto de un archivo: sus declaraciones (`linea: declaración`), sin leerlo completo."""
    p = _resolve(workspace, ruta)
    if not p.is_file():
        raise ToolError(f"No existe el archivo: {ruta}")
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()  # NOSONAR: _resolve()
    out: list[str] = []
    for i, line in enumerate(lines, 1):
        if _DECL_RE.match(line):
            out.append(f"{i}: {line.strip()[:160]}")
            if len(out) >= max_items:
                break
    return out


def _har_entries(p: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))  # NOSONAR: _resolve()
    except (json.JSONDecodeError, OSError) as exc:
        raise ToolError(f"No se pudo leer el HAR {p.name}: {exc}") from exc
    entries = data.get("log", {}).get("entries")
    if not isinstance(entries, list):
        raise ToolError(f"{p.name} no parece un HAR (falta log.entries)")
    return entries


def har(workspace: Path, ruta: str, filtro: str = "", indice: int | None = None) -> str:
    """Consulta un `.har` sin volcarlo (suelen pesar >1 MB: no caben en el contexto).

    - Sin `indice`: lista las peticiones `#i  METHOD status  url` (filtradas por `filtro`,
      subcadena sobre la URL).
    - Con `indice`: muestra esa petición en detalle — headers relevantes, query, body del
      request y principio de la respuesta. Es lo que hace falta para comparar web vs mobile.
    """
    p = _resolve(workspace, ruta)
    if not p.is_file():
        raise ToolError(f"No existe el archivo: {ruta}")
    entries = _har_entries(p)

    if indice is None:
        out: list[str] = []
        for i, e in enumerate(entries):
            req, res = e.get("request", {}), e.get("response", {})
            url = req.get("url", "")
            if filtro and filtro.lower() not in url.lower():
                continue
            out.append(f"#{i}  {req.get('method', '?')} {res.get('status', '?')}  {url[:180]}")
            if len(out) >= 200:
                out.append("… (más entradas; acota con `filtro`)")
                break
        total = len(entries)
        cab = f"{p.name}: {total} peticiones" + (f" · filtro={filtro!r}" if filtro else "")
        return cab + "\n" + ("\n".join(out) or "(ninguna casa el filtro)")

    if not 0 <= indice < len(entries):
        raise ToolError(f"indice fuera de rango (0..{len(entries) - 1})")
    e = entries[indice]
    req, res = e.get("request", {}), e.get("response", {})
    interesantes = {"component", "authorization", "content-type", "accept", "x-", "cookie"}

    def _heads(hs: list[dict[str, Any]]) -> list[str]:
        out = []
        for h in hs or []:
            n = str(h.get("name", "")).lower()
            if any(n.startswith(k) or n == k for k in interesantes):
                v = str(h.get("value", ""))
                out.append(f"    {h.get('name')}: {v[:120]}")
        return out

    partes = [
        f"#{indice}  {req.get('method', '?')} {res.get('status', '?')}  {req.get('url', '')}",
        "  request headers:",
        *_heads(req.get("headers", [])),
    ]
    qs = req.get("queryString") or []
    if qs:
        partes.append("  query: " + ", ".join(f"{q.get('name')}={q.get('value')}" for q in qs))
    body = (req.get("postData") or {}).get("text")
    if body:
        partes += ["  request body:", "    " + body[:1500]]
    rtext = (res.get("content") or {}).get("text")
    if rtext:
        partes += ["  response body (inicio):", "    " + rtext[:800]]
    return "\n".join(partes)


def read_range(workspace: Path, ruta: str, inicio: int, fin: int) -> str:
    """Lee las líneas [inicio, fin] (1-indexadas) con número de línea."""
    p = _resolve(workspace, ruta)
    if not p.is_file():
        raise ToolError(f"No existe el archivo: {ruta}")
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()  # NOSONAR: _resolve()
    inicio = max(1, inicio)
    fin = min(len(lines), fin)
    return "\n".join(f"{i}: {lines[i - 1]}" for i in range(inicio, fin + 1))


def edit(workspace: Path, ruta: str, texto_viejo: str, texto_nuevo: str) -> str:
    """Reemplazo exacto único verificable. Falla si `texto_viejo` no es único."""
    p = _resolve(workspace, ruta)
    if not p.is_file():
        raise ToolError(f"No existe el archivo: {ruta}")
    content = p.read_text(encoding="utf-8")  # NOSONAR: path validado por _resolve()
    n = content.count(texto_viejo)
    if n == 0:
        raise ToolError(f"Texto a reemplazar no encontrado en {ruta}")
    if n > 1:
        raise ToolError(f"Texto no único en {ruta} ({n} ocurrencias); añade contexto")
    p.write_text(content.replace(texto_viejo, texto_nuevo, 1), encoding="utf-8")  # NOSONAR
    return f"editado: {ruta}"
