"""Troceo estructurado para RAG.

Markdown: corta por headers (`#`..`######`) y, si una sección es grande, la parte por
tamaño con solape. Código/texto: ventanas por tamaño con solape. El tamaño se aproxima
en caracteres (≈4 por token) para no depender de un tokenizador pesado.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CHARS_PER_TOKEN = 4
_HEADER = re.compile(r"^(#{1,6})\s+(.*)$")


@dataclass
class Chunk:
    text: str
    section: str
    index: int


def _split_by_size(text: str, max_chars: int, overlap: int) -> list[str]:
    text = text.strip()
    if len(text) <= max_chars:
        return [text] if text else []
    out: list[str] = []
    start = 0
    step = max(1, max_chars - overlap)
    while start < len(text):
        piece = text[start : start + max_chars].strip()
        if piece:
            out.append(piece)
        if start + max_chars >= len(text):
            break
        start += step
    return out


def _markdown_sections(text: str) -> list[tuple[str, str]]:
    sections: list[tuple[str, str]] = []
    header = ""
    buf: list[str] = []
    for line in text.splitlines():
        m = _HEADER.match(line.strip())
        if m:
            if buf:
                sections.append((header, "\n".join(buf).strip()))
            header = m.group(2).strip()
            buf = [line]
        else:
            buf.append(line)
    if buf:
        sections.append((header, "\n".join(buf).strip()))
    return [(h, t) for h, t in sections if t]


def chunk_text(
    text: str, kind: str, max_tokens: int = 512, overlap_tokens: int = 64
) -> list[Chunk]:
    max_chars = max_tokens * CHARS_PER_TOKEN
    overlap = overlap_tokens * CHARS_PER_TOKEN
    pairs: list[tuple[str, str]] = []
    if kind == "markdown":
        for header, body in _markdown_sections(text):
            for piece in _split_by_size(body, max_chars, overlap):
                pairs.append((header, piece))
    if not pairs:
        for piece in _split_by_size(text, max_chars, overlap):
            pairs.append(("", piece))
    return [Chunk(text=t, section=h, index=i) for i, (h, t) in enumerate(pairs)]
