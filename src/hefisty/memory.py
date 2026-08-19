"""Memoria de largo plazo de Hefisty (Fase 4).

Recuerdos atómicos sobre el usuario que persisten entre sesiones: quién es, en qué
trabaja, sus preferencias y correcciones recurrentes. Dos almacenes coordinados:

- **SQLite** (`memories`, junto a las sesiones): el hecho, categoría, origen, usos,
  fijado y archivado — la fuente de verdad y lo que se lista/gestiona.
- **Qdrant** (colección `memoria`): un embedding por recuerdo para recuperación
  semántica en cada turno.

El frontal **consolida** (extrae hechos atómicos filtrando lo trivial) cada N turnos;
en cada turno se **recuperan** los top-k relevantes; los no usados **decaen** y se
archivan (salvo los fijados). Todo en `data/`, nunca sale de la máquina.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from .knowledge.store import KnowledgeStore
from .ollama_client import OllamaClient

MEMORY_COLLECTION = "memoria"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS memories (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    fact         TEXT NOT NULL,
    category     TEXT NOT NULL DEFAULT 'general',
    origin       TEXT,
    created_at   TEXT NOT NULL,
    last_used_at TEXT,
    uses         INTEGER NOT NULL DEFAULT 0,
    pinned       INTEGER NOT NULL DEFAULT 0,
    archived     INTEGER NOT NULL DEFAULT 0
);
"""

CONSOLIDATE_SCHEMA = {
    "type": "object",
    "properties": {
        "memories": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "fact": {"type": "string"},
                    "category": {"type": "string"},
                    "confidence": {"type": "number"},
                },
                "required": ["fact", "confidence"],
            },
        }
    },
    "required": ["memories"],
}

CONSOLIDATE_INSTRUCTION = (
    "Extrae de la conversación SOLO hechos duraderos sobre el usuario o su trabajo que "
    "valga la pena recordar en futuras sesiones: su nombre, sus proyectos y en qué lenguaje "
    "están, preferencias de estilo, decisiones técnicas estables y correcciones recurrentes. "
    "Cada recuerdo debe ser un hecho atómico y autocontenido. IGNORA saludos, charla trivial, "
    "preguntas puntuales y cualquier cosa efímera. Si no hay nada que memorizar, devuelve una "
    'lista vacía. Responde SOLO JSON: {"memories":[{"fact":"...","category":"...",'
    '"confidence":0.0}]} con confidence entre 0 y 1.'
)


def _now() -> datetime:
    return datetime.now(UTC)


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds")


@dataclass
class Memory:
    id: int
    fact: str
    category: str
    created_at: str
    last_used_at: str | None
    uses: int
    pinned: bool
    archived: bool
    origin: str | None = None


class MemoryStore:
    """Almacén sincrónico de recuerdos (sqlite3). Los llamadores async usan to_thread."""

    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._path)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _row(r: sqlite3.Row) -> Memory:
        return Memory(
            id=r["id"],
            fact=r["fact"],
            category=r["category"],
            created_at=r["created_at"],
            last_used_at=r["last_used_at"],
            uses=r["uses"],
            pinned=bool(r["pinned"]),
            archived=bool(r["archived"]),
            origin=r["origin"],
        )

    def add(self, fact: str, category: str = "general", origin: str | None = None) -> Memory:
        now = _iso(_now())
        with self._connect() as con:
            cur = con.execute(
                "INSERT INTO memories (fact, category, origin, created_at, uses, pinned, archived) "
                "VALUES (?, ?, ?, ?, 0, 0, 0)",
                (fact, category, origin, now),
            )
            mem_id = int(cur.lastrowid or 0)
        return Memory(mem_id, fact, category, now, None, 0, False, False, origin)

    def get(self, mem_id: int) -> Memory | None:
        with self._connect() as con:
            r = con.execute("SELECT * FROM memories WHERE id = ?", (mem_id,)).fetchone()
        return self._row(r) if r else None

    def list(self, include_archived: bool = False) -> list[Memory]:
        q = "SELECT * FROM memories"
        if not include_archived:
            q += " WHERE archived = 0"
        q += " ORDER BY pinned DESC, id DESC"
        with self._connect() as con:
            return [self._row(r) for r in con.execute(q).fetchall()]

    def update_fact(self, mem_id: int, fact: str, category: str) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE memories SET fact = ?, category = ?, archived = 0 WHERE id = ?",
                (fact, category, mem_id),
            )

    def mark_used(self, ids: list[int]) -> None:
        if not ids:
            return
        now = _iso(_now())
        with self._connect() as con:
            con.executemany(
                "UPDATE memories SET uses = uses + 1, last_used_at = ? WHERE id = ?",
                [(now, i) for i in ids],
            )

    def set_pinned(self, mem_id: int, pinned: bool) -> bool:
        with self._connect() as con:
            cur = con.execute(
                "UPDATE memories SET pinned = ? WHERE id = ?", (1 if pinned else 0, mem_id)
            )
            return cur.rowcount > 0

    def archive(self, mem_id: int) -> None:
        with self._connect() as con:
            con.execute("UPDATE memories SET archived = 1 WHERE id = ?", (mem_id,))

    def forget(self, mem_id: int) -> bool:
        with self._connect() as con:
            cur = con.execute("DELETE FROM memories WHERE id = ?", (mem_id,))
            return cur.rowcount > 0


class MemoryService:
    """Coordina consolidación (frontal), recuperación (embeddings) y olvido."""

    def __init__(
        self,
        settings: Any,
        ollama: OllamaClient,
        store: MemoryStore,
        knowledge: KnowledgeStore,
    ) -> None:
        self._s = settings
        self._o = ollama
        self._store = store
        self._k = knowledge

    async def _embed(self, text: str) -> list[float] | None:
        vecs = await self._o.embed(self._s.model_embed, [text])
        return vecs[0] if vecs else None

    def _upsert(self, mem: Memory, vec: list[float]) -> None:
        self._k.ensure(MEMORY_COLLECTION, len(vec))
        self._k.upsert(
            MEMORY_COLLECTION,
            [vec],
            [{
                "index": str(mem.id),
                "text": mem.fact,
                "source": str(mem.id),
                "section": mem.category,
                "language": "",
            }],
        )

    async def consolidate(self, messages: list[dict], origin: str | None = None) -> int:
        """El frontal extrae recuerdos atómicos de la conversación. Devuelve cuántos
        se guardaron o actualizaron. Lo trivial se descarta por el filtro del prompt y
        por el umbral de confianza."""
        convo = "\n".join(
            f"{m.get('role')}: {m.get('content', '')}" for m in messages if m.get("content")
        )
        if not convo.strip():
            return 0
        raw = await self._o.chat(
            self._s.model_frontal,
            [
                {"role": "system", "content": CONSOLIDATE_INSTRUCTION},
                {"role": "user", "content": convo},
            ],
            keep_alive=self._s.keep_alive,
            fmt=CONSOLIDATE_SCHEMA,
        )
        try:
            items = json.loads(raw).get("memories", [])
        except (json.JSONDecodeError, TypeError, AttributeError):
            return 0
        saved = 0
        for it in items:
            fact = (it.get("fact") or "").strip()
            conf = it.get("confidence", 0)
            if not fact or conf < self._s.memory_confidence_min:
                continue
            category = (it.get("category") or "general").strip() or "general"
            if await self._store_or_update(fact, category, origin):
                saved += 1
        return saved

    async def _store_or_update(self, fact: str, category: str, origin: str | None) -> bool:
        vec = await self._embed(fact)
        if vec is None:
            return False
        # ¿Ya existe un recuerdo casi igual? -> actualizar (no duplicar).
        hits = self._k.search(MEMORY_COLLECTION, vec, 1, self._s.memory_dedup_min)
        if hits and hits[0].source.isdigit():
            mem_id = int(hits[0].source)
            self._store.update_fact(mem_id, fact, category)
            mem = self._store.get(mem_id)
            if mem:
                self._upsert(mem, vec)
            return True
        mem = self._store.add(fact, category, origin)
        self._upsert(mem, vec)
        return True

    async def recall(self, query: str, k: int | None = None) -> list[str]:
        """Top-k recuerdos relevantes a lo que se habla. Marca los usados."""
        vec = await self._embed(query)
        if vec is None:
            return []
        hits = self._k.search(
            MEMORY_COLLECTION, vec, k or self._s.memory_recall_k, self._s.memory_score_min
        )
        ids = [int(h.source) for h in hits if h.source.isdigit()]
        self._store.mark_used(ids)
        return [h.text for h in hits]

    async def add_manual(self, fact: str, category: str = "manual") -> Memory | None:
        vec = await self._embed(fact)
        if vec is None:
            return None
        mem = self._store.add(fact, category, origin="manual")
        self._upsert(mem, vec)
        return mem

    def forget(self, mem_id: int) -> bool:
        self._k.delete_point(MEMORY_COLLECTION, str(mem_id))
        return self._store.forget(mem_id)

    def decay(self, now: datetime | None = None) -> int:
        """Archiva recuerdos no fijados, sin usos y más viejos que la ventana. Los archivados
        salen de Qdrant para dejar de recuperarse. Devuelve cuántos archivó."""
        now = now or _now()
        window = timedelta(days=self._s.memory_decay_days)
        archived = 0
        for mem in self._store.list(include_archived=False):
            if mem.pinned or mem.uses > 0:
                continue
            created = datetime.fromisoformat(mem.created_at)
            if now - created >= window:
                self._store.archive(mem.id)
                self._k.delete_point(MEMORY_COLLECTION, str(mem.id))
                archived += 1
        return archived
