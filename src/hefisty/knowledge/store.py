"""Almacén de conocimiento sobre Qdrant: una colección por diccionario.

Wrapper síncrono (qdrant-client). Los llamadores async lo usan vía `asyncio.to_thread`
o desde comandos CLU one-shot.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointIdsList, PointStruct, VectorParams

# Namespace estable para IDs deterministas (reingesta idempotente).
_NS = uuid.UUID("00000000-0000-0000-0000-00000000d1cc")


@dataclass
class Hit:
    text: str
    source: str
    section: str
    language: str
    score: float


class KnowledgeStore:
    def __init__(self, url: str) -> None:
        self._c = QdrantClient(url=url)

    def ensure(self, collection: str, dim: int) -> None:
        if not self._c.collection_exists(collection):
            self._c.create_collection(
                collection,
                vectors_config=VectorParams(size=dim, distance=Distance.COSINE),
            )

    def upsert(self, collection: str, vectors: list[list[float]], payloads: list[dict]) -> int:
        points = [
            PointStruct(
                id=str(uuid.uuid5(_NS, f"{collection}:{pl['index']}")),
                vector=vec,
                payload=pl,
            )
            for vec, pl in zip(vectors, payloads, strict=True)
        ]
        self._c.upsert(collection, points=points)
        return len(points)

    def search(self, collection: str, vector: list[float], k: int, score_min: float) -> list[Hit]:
        if not self._c.collection_exists(collection):
            return []
        res = self._c.query_points(
            collection, query=vector, limit=k, score_threshold=score_min
        ).points
        return [
            Hit(
                text=p.payload.get("text", ""),
                source=p.payload.get("source", ""),
                section=p.payload.get("section", ""),
                language=p.payload.get("language", ""),
                score=p.score,
            )
            for p in res
        ]

    def delete(self, collection: str) -> bool:
        if self._c.collection_exists(collection):
            self._c.delete_collection(collection)
            return True
        return False

    def delete_point(self, collection: str, index_str: str) -> None:
        """Borra un punto por su índice determinista (mismo esquema que upsert)."""
        if not self._c.collection_exists(collection):
            return
        pid = str(uuid.uuid5(_NS, f"{collection}:{index_str}"))
        self._c.delete(collection, points_selector=PointIdsList(points=[pid]))

    def collections(self) -> list[tuple[str, int]]:
        names = [c.name for c in self._c.get_collections().collections]
        return sorted((name, self._c.count(name).count) for name in names)

    def count(self, collection: str) -> int:
        if not self._c.collection_exists(collection):
            return 0
        return self._c.count(collection).count
