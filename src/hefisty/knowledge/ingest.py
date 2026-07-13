"""Pipeline de ingesta: fuentes → chunks → embeddings (Ollama) → Qdrant."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..ollama_client import OllamaClient
from .chunking import chunk_text
from .loaders import iter_sources, load_file
from .store import KnowledgeStore

_EMBED_BATCH = 64


@dataclass
class IngestResult:
    collection: str
    files: int
    chunks: int


async def ingest_path(
    settings: Settings,
    ollama: OllamaClient,
    store: KnowledgeStore,
    collection: str,
    path: Path,
    language: str = "",
) -> IngestResult:
    root = Path(path)
    texts: list[str] = []
    payloads: list[dict] = []
    files = 0
    for src in iter_sources(root):
        text, kind, lang = load_file(src)
        files += 1
        rel = src.relative_to(root).as_posix() if src.is_relative_to(root) else src.name
        for ch in chunk_text(text, kind, settings.chunk_tokens, settings.chunk_overlap):
            texts.append(ch.text)
            payloads.append(
                {
                    "text": ch.text,
                    "source": rel,
                    "section": ch.section,
                    "language": language or lang,
                    "index": f"{rel}#{ch.index}",
                }
            )
    if not texts:
        return IngestResult(collection, files, 0)

    vectors: list[list[float]] = []
    for i in range(0, len(texts), _EMBED_BATCH):
        vectors.extend(await ollama.embed(settings.model_embed, texts[i : i + _EMBED_BATCH]))

    store.ensure(collection, dim=len(vectors[0]))
    n = store.upsert(collection, vectors, payloads)
    return IngestResult(collection, files, n)
