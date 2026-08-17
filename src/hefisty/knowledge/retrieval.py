"""Retriever: embebe la consulta y recupera top-k con umbral de una o varias colecciones."""

from __future__ import annotations

import asyncio

from ..config import Settings
from ..ollama_client import OllamaClient
from .store import Hit, KnowledgeStore


class Retriever:
    def __init__(self, settings: Settings, ollama: OllamaClient, store: KnowledgeStore) -> None:
        self._s = settings
        self._o = ollama
        self._store = store

    async def retrieve(self, query: str, collections: list[str]) -> list[Hit]:
        vecs = await self._o.embed(self._s.model_embed, [query])
        if not vecs:
            return []
        vec = vecs[0]
        hits: list[Hit] = []
        for col in collections:
            try:
                hits.extend(
                    await asyncio.to_thread(
                        self._store.search,
                        col,
                        vec,
                        self._s.retrieval_k,
                        self._s.retrieval_score_min,
                    )
                )
            except Exception:  # una colección inexistente/caída no anula al resto
                continue
        hits.sort(key=lambda h: h.score, reverse=True)
        return hits[: self._s.retrieval_k]
