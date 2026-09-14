"""Retriever: embebe la consulta y recupera top-k con umbral de una o varias colecciones.

La fusión entre colecciones está EQUILIBRADA: cada colección aporta como mucho
`retrieval_per_collection` chunks al top-k (los huecos sobrantes se rellenan con los mejores
restantes). Sin ese tope, una colección redactada en genérico (las `skills` de método) puntúa
alto para cualquier tarea y expulsa al conocimiento de dominio que sí resolvía el caso.
"""

from __future__ import annotations

import asyncio
from collections import Counter

from ..config import Settings
from ..ollama_client import OllamaClient
from .store import Hit, KnowledgeStore


def balance_hits(hits: list[Hit], k: int, per_collection: int) -> list[Hit]:
    """Top-k por score con tope por colección. `per_collection <= 0` = sin tope."""
    ordered = sorted(hits, key=lambda h: h.score, reverse=True)
    if per_collection <= 0:
        return ordered[:k]
    chosen: list[Hit] = []
    deferred: list[Hit] = []
    counts: Counter[str] = Counter()
    for h in ordered:
        if counts[h.collection] < per_collection:
            chosen.append(h)
            counts[h.collection] += 1
        else:
            deferred.append(h)
    if len(chosen) < k:
        chosen.extend(deferred[: k - len(chosen)])
    return sorted(chosen[:k], key=lambda h: h.score, reverse=True)


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
        return balance_hits(hits, self._s.retrieval_k, self._s.retrieval_per_collection)
