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
    # Excluye symlinks que apunten fuera del workspace.
    return sorted(
        p.relative_to(ws).as_posix() for p in ws.glob(patron) if p.is_file() and _inside(ws, p)
    )


def grep(workspace: Path, regex: str, ruta: str = ".", max_resultados: int = 200) -> list[str]:
    """Líneas que casan `regex` bajo `ruta`. Formato: `archivo:linea: contenido`."""
    base = _resolve(workspace, ruta)
    ws = Path(workspace).resolve()
    try:
        pat = re.compile(regex)
    except re.error as exc:
        raise ToolError(f"Regex inválida: {exc}") from exc
    files = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
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
