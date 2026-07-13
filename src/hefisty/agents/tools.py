"""Herramientas de archivo del Coder, restringidas al workspace configurado.

Toda ruta se resuelve y se valida contra el workspace: cualquier intento de salir
(`..`, rutas absolutas, symlinks que escapen) se rechaza. La ejecución de código
queda para la Fase 3.
"""

from __future__ import annotations

from pathlib import Path, PureWindowsPath


class ToolError(Exception):
    """Uso inválido de una herramienta (ruta fuera del workspace, no existe, etc.)."""


def _resolve(workspace: Path, rel: str) -> Path:
    # Rechaza de entrada rutas absolutas o con unidad (C:..., \\server, /etc): el Coder
    # solo direcciona con rutas relativas dentro del workspace.
    if Path(rel).is_absolute() or PureWindowsPath(rel).drive or PureWindowsPath(rel).is_absolute():
        raise ToolError(f"Ruta no relativa no permitida: {rel}")
    workspace = Path(workspace).resolve()
    target = (workspace / rel).resolve()
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
