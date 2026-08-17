"""Carga de fuentes: Markdown, HTML, PDF, código y texto → texto plano + tipo."""

from __future__ import annotations

import json
from collections.abc import Iterator
from html.parser import HTMLParser
from pathlib import Path

MARKDOWN_EXT = {".md", ".markdown"}
HTML_EXT = {".html", ".htm"}
CODE_EXT = {
    ".py",
    ".kt",
    ".kts",
    ".java",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".go",
    ".rs",
    ".c",
    ".cpp",
    ".cs",
    ".rb",
    ".sql",
    ".sh",
}
TEXT_EXT = {".txt"}
JSONL_EXT = {".jsonl"}
SUPPORTED = MARKDOWN_EXT | HTML_EXT | CODE_EXT | TEXT_EXT | JSONL_EXT | {".pdf"}


def load_jsonl_pairs(path: Path) -> list[tuple[str, str]]:
    """Pares Q&A `{"u":pregunta,"a":respuesta}` (una línea = un par). Devuelve (u, a)."""
    pairs: list[tuple[str, str]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        u = obj.get("u") or obj.get("pregunta") or obj.get("q") or ""
        a = obj.get("a") or obj.get("respuesta") or ""
        if u:
            pairs.append((u, a))
    return pairs


class _HTMLText(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self._skip = 0
        self.parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag in ("script", "style"):
            self._skip += 1

    def handle_endtag(self, tag: str) -> None:
        if tag in ("script", "style") and self._skip:
            self._skip -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip and data.strip():
            self.parts.append(data.strip())


def _html_to_text(html: str) -> str:
    parser = _HTMLText()
    parser.feed(html)
    return "\n".join(parser.parts)


def _pdf_to_text(path: Path) -> str:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return "\n\n".join(page.extract_text() or "" for page in reader.pages)


def load_file(path: Path) -> tuple[str, str, str]:
    """Devuelve (texto, kind, language). kind ∈ {markdown, text, code}."""
    ext = path.suffix.lower()
    if ext in MARKDOWN_EXT:
        return path.read_text(encoding="utf-8", errors="replace"), "markdown", ""
    if ext in HTML_EXT:
        return _html_to_text(path.read_text(encoding="utf-8", errors="replace")), "text", ""
    if ext == ".pdf":
        return _pdf_to_text(path), "text", ""
    if ext in CODE_EXT:
        return path.read_text(encoding="utf-8", errors="replace"), "code", ext.lstrip(".")
    return path.read_text(encoding="utf-8", errors="replace"), "text", ""


def iter_sources(root: Path) -> Iterator[Path]:
    for p in sorted(root.rglob("*")):
        if p.is_file() and p.suffix.lower() in SUPPORTED:
            yield p
