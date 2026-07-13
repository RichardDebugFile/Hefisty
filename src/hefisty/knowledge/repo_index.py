"""Índice semántico del repo del usuario.

Embebe los archivos de código por bloque a una colección `repo__<nombre>` en Qdrant.
Reindexado incremental: un manifiesto relpath→mtime evita re-embeber archivos sin
cambios. Alimenta la herramienta `search_code` del Coder.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from ..config import Settings
from ..ollama_client import OllamaClient
from .chunking import chunk_text
from .loaders import CODE_EXT, load_file
from .store import KnowledgeStore

_EMBED_BATCH = 64
_SKIP_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "__pycache__",
    "dist",
    "build",
    ".pytest_cache",
    ".ruff_cache",
    ".mypy_cache",
    "data",
    ".idea",
    ".vscode",
}


@dataclass
class IndexResult:
    collection: str
    scanned: int
    changed: int
    chunks: int


def collection_name(root: Path) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", Path(root).resolve().name.lower()).strip("_")
    return f"repo__{slug or 'repo'}"


def _code_files(root: Path):
    for p in root.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in CODE_EXT:
            continue
        if any(part in _SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        yield p


async def index_repo(
    settings: Settings, ollama: OllamaClient, store: KnowledgeStore, root: Path
) -> IndexResult:
    root = Path(root).resolve()
    col = collection_name(root)
    manifest_path = settings.data_dir / "repo_index" / f"{col}.json"
    manifest: dict[str, float] = (
        json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    )
    new_manifest = dict(manifest)
    texts: list[str] = []
    payloads: list[dict] = []
    scanned = 0
    changed = 0
    for f in _code_files(root):
        scanned += 1
        rel = f.relative_to(root).as_posix()
        mtime = f.stat().st_mtime
        new_manifest[rel] = mtime
        if manifest.get(rel) == mtime:
            continue
        changed += 1
        text, _kind, lang = load_file(f)
        for ch in chunk_text(text, "code", settings.chunk_tokens, settings.chunk_overlap):
            texts.append(ch.text)
            payloads.append(
                {
                    "text": ch.text,
                    "source": rel,
                    "section": ch.section,
                    "language": lang,
                    "index": f"{rel}#{ch.index}",
                }
            )

    chunks = 0
    if texts:
        vectors: list[list[float]] = []
        for i in range(0, len(texts), _EMBED_BATCH):
            vectors.extend(await ollama.embed(settings.model_embed, texts[i : i + _EMBED_BATCH]))
        store.ensure(col, dim=len(vectors[0]))
        chunks = store.upsert(col, vectors, payloads)

    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(new_manifest), encoding="utf-8")
    return IndexResult(collection=col, scanned=scanned, changed=changed, chunks=chunks)
