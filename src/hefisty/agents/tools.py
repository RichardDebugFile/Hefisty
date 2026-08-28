"""Herramientas de archivo del Coder, restringidas al workspace configurado.

Toda ruta se resuelve y se valida contra el workspace: cualquier intento de salir
(`..`, rutas absolutas, symlinks que escapen) se rechaza. La ejecución de código
queda para la Fase 3.
"""

from __future__ import annotations

import re
from pathlib import Path, PureWindowsPath


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
# Aunque sea texto, un archivo enorme (bundle minificado, dump) infla grep y el contexto.
_MAX_GREP_BYTES = 512 * 1024


def _grep_candidate(rel: Path, abs_path: Path) -> bool:
    """True si el archivo debe entrar a grep: no es ruido, es de texto y no es gigante."""
    if _skipped(rel) or abs_path.suffix.lower() not in _TEXT_EXTS:
        return False
    try:
        return abs_path.stat().st_size <= _MAX_GREP_BYTES
    except OSError:
        return False


def _resolve(workspace: Path, rel: str) -> Path:
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
    return p.read_text(encoding="utf-8")


def escribir_archivo(workspace: Path, ruta: str, contenido: str) -> str:
    p = _resolve(workspace, ruta)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(contenido, encoding="utf-8")
    return f"escrito: {ruta} ({len(contenido)} chars)"


def listar_directorio(workspace: Path, ruta: str = ".") -> list[str]:
    p = _resolve(workspace, ruta)
    if not p.is_dir():
        raise ToolError(f"No es un directorio: {ruta}")
    return sorted(e.name + ("/" if e.is_dir() else "") for e in p.iterdir())


# --- Navegación de código (todas confinadas al workspace) ---


def _inside(ws: Path, p: Path) -> bool:
    """True si `p` (tras resolver symlinks) sigue dentro del workspace."""
    rp = p.resolve()
    return rp == ws or ws in rp.parents


def glob(workspace: Path, patron: str) -> list[str]:
    """Rutas de archivo (relativas) que casan el patrón glob dentro del workspace."""
    if ".." in patron or patron.startswith(("/", "\\")) or PureWindowsPath(patron).drive:
        raise ToolError(f"Patrón no permitido: {patron}")
    ws = Path(workspace).resolve()
    # Excluye symlinks que apunten fuera del workspace y carpetas de ruido (.git/build/…).
    return sorted(
        rel.as_posix()
        for p in ws.glob(patron)
        if p.is_file() and _inside(ws, p) and not _skipped(rel := p.relative_to(ws))
    )


def grep(workspace: Path, regex: str, ruta: str = ".", max_resultados: int = 200) -> list[str]:
    """Líneas que casan `regex` bajo `ruta`. Formato: `archivo:linea: contenido`."""
    base = _resolve(workspace, ruta)
    ws = Path(workspace).resolve()
    try:
        pat = re.compile(regex)
    except re.error as exc:
        raise ToolError(f"Regex inválida: {exc}") from exc
    if base.is_file():
        # Archivo puntual pedido por su ruta: respétalo tal cual (el modelo eligió).
        files = [base]
    else:
        # Recorrido: filtra ruido/binarios/gigantes por extensión y tamaño (rápido, sin abrir).
        files = [
            p
            for p in base.rglob("*")
            if p.is_file() and _inside(ws, p) and _grep_candidate(p.relative_to(ws), p)
        ]
    out: list[str] = []
    for f in files:
        if not _inside(ws, f):  # symlink que escapa del workspace
            continue
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        rel = f.relative_to(ws).as_posix()
        for i, line in enumerate(lines, 1):
            if pat.search(line):
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
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    out: list[str] = []
    for i, line in enumerate(lines, 1):
        if _DECL_RE.match(line):
            out.append(f"{i}: {line.strip()[:160]}")
            if len(out) >= max_items:
                break
    return out


def read_range(workspace: Path, ruta: str, inicio: int, fin: int) -> str:
    """Lee las líneas [inicio, fin] (1-indexadas) con número de línea."""
    p = _resolve(workspace, ruta)
    if not p.is_file():
        raise ToolError(f"No existe el archivo: {ruta}")
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
    inicio = max(1, inicio)
    fin = min(len(lines), fin)
    return "\n".join(f"{i}: {lines[i - 1]}" for i in range(inicio, fin + 1))


def edit(workspace: Path, ruta: str, texto_viejo: str, texto_nuevo: str) -> str:
    """Reemplazo exacto único verificable. Falla si `texto_viejo` no es único."""
    p = _resolve(workspace, ruta)
    if not p.is_file():
        raise ToolError(f"No existe el archivo: {ruta}")
    content = p.read_text(encoding="utf-8")
    n = content.count(texto_viejo)
    if n == 0:
        raise ToolError(f"Texto a reemplazar no encontrado en {ruta}")
    if n > 1:
        raise ToolError(f"Texto no único en {ruta} ({n} ocurrencias); añade contexto")
    p.write_text(content.replace(texto_viejo, texto_nuevo, 1), encoding="utf-8")
    return f"editado: {ruta}"
