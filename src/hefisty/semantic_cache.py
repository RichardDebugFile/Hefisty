"""Cache semántica (L1) sobre Qdrant: hit por similitud del embedding de la query.

Complementa la cache exacta para parafraseos. Solo para turnos conversacionales /
consultas de conocimiento — NUNCA para tareas del Coder sobre el workspace (el estado
del repo cambia entre peticiones idénticas). Resiliente: si Qdrant/embeddings fallan,
se comporta como cache vacía.
"""

from __future__ import annotations

import asyncio
import json

from .config import Settings
from .knowledge.store import KnowledgeStore
from .ollama_client import OllamaClient


class SemanticCache:
    def __init__(
        self,
        settings: Settings,
        ollama: OllamaClient,
        store: KnowledgeStore,
        collection: str = "semcache",
    ) -> None:
        self._s = settings
        self._o = ollama
        self._store = store
        self._col = collection

    async def get(self, query: str) -> dict | None:
        try:
            vecs = await self._o.embed(self._s.model_embed, [query])
            if not vecs:
                return None
            hits = await asyncio.to_thread(
                self._store.search, self._col, vecs[0], 1, self._s.semantic_threshold
            )
        except Exception:
            return None
        if not hits:
            return None
        try:
            return json.loads(hits[0].text)
        except (json.JSONDecodeError, ValueError):
            return None

    async def put(self, query: str, value: dict) -> None:
        try:
            vecs = await self._o.embed(self._s.model_embed, [query])
            if not vecs:
                return
            payload = {
                "text": json.dumps(value),
                "source": query[:80],
                "section": "",
                "language": "",
                "index": f"sem:{query}",
            }
            await asyncio.to_thread(self._store.ensure, self._col, len(vecs[0]))
            await asyncio.to_thread(self._store.upsert, self._col, [vecs[0]], [payload])
        except Exception:
            pass

    async def delete(self, query: str) -> None:
        """Invalida la entrada semántica de una query (para el 👎)."""
        try:
            await asyncio.to_thread(self._store.delete_point, self._col, f"sem:{query}")
        except Exception:
            pass
