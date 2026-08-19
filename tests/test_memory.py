"""Tests de la memoria de largo plazo (Fase 4). LLM y Qdrant fakeados."""

import json
import math
import re
from datetime import UTC, datetime, timedelta

from hefisty.config import Settings
from hefisty.knowledge.store import Hit
from hefisty.memory import MemoryService, MemoryStore

# --- fakes ---------------------------------------------------------------- #

def _vec(text: str, dim: int = 64) -> list[float]:
    """Embedding fake: bolsa de palabras normalizada (overlap de tokens ~ coseno)."""
    v = [0.0] * dim
    for tok in re.findall(r"\w+", text.lower()):
        v[hash(tok) % dim] += 1.0
    norm = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / norm for x in v]


def _cos(a: list[float], b: list[float]) -> float:
    return sum(x * y for x, y in zip(a, b, strict=True))


class FakeOllama:
    def __init__(self, chat_json: str = ""):
        self.chat_json = chat_json

    async def embed(self, model, inputs):
        return [_vec(t) for t in inputs]

    async def chat(self, model, messages, *, keep_alive="10m", fmt=None, options=None):
        return self.chat_json


class FakeVecStore:
    """Sustituto de KnowledgeStore para memoria: coseno sobre vectores en memoria."""

    def __init__(self):
        self.points: dict[str, tuple[list[float], dict]] = {}

    def ensure(self, collection, dim):
        pass

    def upsert(self, collection, vectors, payloads):
        for vec, pl in zip(vectors, payloads, strict=True):
            self.points[pl["index"]] = (vec, pl)
        return len(payloads)

    def search(self, collection, vector, k, score_min):
        scored = []
        for _idx, (vec, pl) in self.points.items():
            s = _cos(vector, vec)
            if s >= score_min:
                scored.append((s, pl))
        scored.sort(key=lambda x: x[0], reverse=True)
        return [
            Hit(text=pl["text"], source=pl["source"], section=pl["section"], language="", score=s)
            for s, pl in scored[:k]
        ]

    def delete_point(self, collection, index_str):
        self.points.pop(index_str, None)


def _settings(**over) -> Settings:
    base = dict(
        memory_confidence_min=0.6,
        memory_dedup_min=0.8,
        memory_score_min=0.3,
        memory_decay_days=30,
    )
    base.update(over)
    return Settings(**base)


def _service(tmp_path, chat_json=""):
    store = MemoryStore(tmp_path / "mem.db")
    svc = MemoryService(_settings(), FakeOllama(chat_json), store, FakeVecStore())
    return store, svc


# --- tests ---------------------------------------------------------------- #

def test_memory_store_crud(tmp_path):
    store = MemoryStore(tmp_path / "m.db")
    m = store.add("prefiere inyección por constructor", "estilo")
    assert store.get(m.id).fact == "prefiere inyección por constructor"
    store.mark_used([m.id])
    assert store.get(m.id).uses == 1
    assert store.set_pinned(m.id, True)
    assert store.get(m.id).pinned is True
    assert store.forget(m.id) is True
    assert store.get(m.id) is None


async def test_consolidate_filters_trivia(tmp_path):
    payload = json.dumps({
        "memories": [
            {"fact": "su app principal se llama Kairos y está en Kotlin", "category": "proyecto",
             "confidence": 0.95},
            {"fact": "hoy dijo hola", "category": "trivial", "confidence": 0.2},
        ]
    })
    store, svc = _service(tmp_path, payload)
    n = await svc.consolidate([{"role": "user", "content": "mi app Kairos en Kotlin"}])
    assert n == 1  # solo el de alta confianza
    facts = [m.fact for m in store.list()]
    assert any("Kairos" in f for f in facts)
    assert not any("hola" in f for f in facts)


async def test_consolidate_updates_instead_of_duplicating(tmp_path):
    store, svc = _service(tmp_path)
    svc._o.chat_json = json.dumps({"memories": [
        {"fact": "su app principal se llama Kairos y está en Kotlin", "confidence": 0.9}]})
    await svc.consolidate([{"role": "user", "content": "app Kairos"}])
    svc._o.chat_json = json.dumps({"memories": [
        {"fact": "su app principal se llama Atlas y está en Kotlin", "confidence": 0.9}]})
    await svc.consolidate([{"role": "user", "content": "ahora se llama Atlas"}])
    mems = store.list()
    assert len(mems) == 1  # actualizó, no duplicó
    assert "Atlas" in mems[0].fact and "Kairos" not in mems[0].fact


async def test_recall_returns_and_marks_used(tmp_path):
    store, svc = _service(tmp_path)
    svc._o.chat_json = json.dumps({"memories": [
        {"fact": "prefiere inyección por constructor", "confidence": 0.9}]})
    await svc.consolidate([{"role": "user", "content": "inyección por constructor"}])
    facts = await svc.recall("inyección por constructor")
    assert any("constructor" in f for f in facts)
    assert store.list()[0].uses == 1  # recall marcó el recuerdo como usado


async def test_decay_archives_unused_but_not_pinned_or_used(tmp_path):
    store, svc = _service(tmp_path)
    sin_uso = store.add("dato viejo sin uso", "x")
    usado = store.add("dato usado", "x")
    fijado = store.add("dato fijado", "x")
    store.mark_used([usado.id])
    store.set_pinned(fijado.id, True)
    n = svc.decay(now=datetime.now(UTC) + timedelta(days=60))
    assert n == 1  # solo el sin uso
    assert store.get(sin_uso.id).archived is True
    assert store.get(usado.id).archived is False
    assert store.get(fijado.id).archived is False
    assert [m.id for m in store.list()] == [fijado.id, usado.id]  # archivados fuera del listado
