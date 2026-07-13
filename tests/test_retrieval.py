from hefisty.config import Settings
from hefisty.knowledge.retrieval import Retriever
from hefisty.knowledge.store import Hit
from hefisty.orchestrator.core import detect_language


class FakeOllama:
    async def embed(self, model, inputs):
        return [[0.1, 0.2, 0.3] for _ in inputs]


class FakeStore:
    def __init__(self, by_collection):
        self._by = by_collection

    def search(self, collection, vector, k, score_min):
        hits = [h for h in self._by.get(collection, []) if h.score >= score_min]
        return sorted(hits, key=lambda h: h.score, reverse=True)[:k]


def test_detect_language():
    assert detect_language("escribe una app Android con Compose") == "kotlin"
    assert detect_language("un archivo .kt de ejemplo") == "kotlin"
    assert detect_language("hola, ¿qué tal?") is None


async def test_retriever_filters_by_threshold_and_orders():
    s = Settings(retrieval_k=6, retrieval_score_min=0.5)
    store = FakeStore(
        {
            "kotlin": [
                Hit("a", "f1", "s1", "kotlin", 0.9),
                Hit("b", "f2", "s2", "kotlin", 0.6),
                Hit("c", "f3", "s3", "kotlin", 0.3),  # bajo el umbral
            ]
        }
    )
    r = Retriever(s, FakeOllama(), store)
    out = await r.retrieve("consulta", ["kotlin"])
    assert [h.source for h in out] == ["f1", "f2"]


async def test_retriever_empty_when_no_embeddings():
    class NoEmbed:
        async def embed(self, model, inputs):
            return []

    r = Retriever(Settings(), NoEmbed(), FakeStore({}))
    assert await r.retrieve("x", ["kotlin"]) == []
