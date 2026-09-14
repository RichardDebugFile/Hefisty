from hefisty.config import Settings
from hefisty.knowledge.retrieval import Retriever
from hefisty.knowledge.store import Hit
from hefisty.lang import collections_for
from hefisty.orchestrator.core import detect_language


class FakeOllama:
    async def embed(self, model, inputs):
        return [[0.1, 0.2, 0.3] for _ in inputs]


class FakeStore:
    def __init__(self, by_collection):
        self._by = by_collection

    def search(self, collection, vector, k, score_min):
        hits = [h for h in self._by.get(collection, []) if h.score >= score_min]
        for h in hits:  # el store real etiqueta cada hit con su colección
            h.collection = collection
        return sorted(hits, key=lambda h: h.score, reverse=True)[:k]


def test_detect_language():
    assert detect_language("escribe una app Android con Compose") == "kotlin"
    assert detect_language("un archivo .kt de ejemplo") == "kotlin"
    assert detect_language("hola, ¿qué tal?") is None


def test_collections_for_dedup_and_order():
    assert collections_for("kotlin", ["proyecto"]) == ["kotlin", "proyecto", "patrones"]
    assert collections_for(None, []) == ["patrones"]
    assert collections_for(None, ["a", "b"]) == ["a", "b", "patrones"]
    assert collections_for("patrones", ["patrones"]) == ["patrones"]  # sin duplicados


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


async def test_retriever_balances_collections():
    # Caso real (eval §2, 13/09/2026): `skills` (método, genérico) puntuaba 0.76-0.78 para
    # cualquier tarea y ocupaba 4/6 huecos; la silueta de bug exacta en `patrones` (0.70)
    # se quedaba fuera. Con tope 2 por colección entra el conocimiento de dominio.
    s = Settings(retrieval_k=6, retrieval_score_min=0.4, retrieval_per_collection=2)
    store = FakeStore(
        {
            "skills": [Hit(f"s{i}", f"skill{i}.md", "", "", 0.78 - i * 0.005) for i in range(5)],
            "patrones": [
                Hit("p1", "10-formas-de-bug.md", "orden por texto", "", 0.70),
                Hit("p2", "02-gof.md", "", "", 0.60),
                Hit("p3", "03-conc.md", "", "", 0.55),
            ],
            "proyecto": [Hit("y1", "casos.md", "", "", 0.72)],
        }
    )
    out = await Retriever(s, FakeOllama(), store).retrieve(
        "bitacora desordenada",
        [
            "proyecto",
            "skills",
            "patrones",
        ],
    )
    sources = [h.source for h in out]
    assert len(out) == 6
    # Tope 2 por colección: entran skill0/skill1, casos, 10-formas y 02-gof (dominio) …
    assert "10-formas-de-bug.md" in sources and "02-gof.md" in sources and "casos.md" in sources
    # … y el hueco sobrante (proyecto solo tenía 1) se rellena con el mejor restante (skill2).
    assert sum(src.startswith("skill") for src in sources) == 3
    assert "03-conc.md" not in sources
    assert [h.score for h in out] == sorted((h.score for h in out), reverse=True)


async def test_retriever_no_cap_when_per_collection_zero():
    s = Settings(retrieval_k=3, retrieval_score_min=0.4, retrieval_per_collection=0)
    store = FakeStore(
        {
            "skills": [Hit(f"s{i}", f"skill{i}.md", "", "", 0.9 - i * 0.01) for i in range(4)],
            "patrones": [Hit("p1", "p.md", "", "", 0.5)],
        }
    )
    out = await Retriever(s, FakeOllama(), store).retrieve("x", ["skills", "patrones"])
    assert [h.source for h in out] == ["skill0.md", "skill1.md", "skill2.md"]


async def test_retriever_empty_when_no_embeddings():
    class NoEmbed:
        async def embed(self, model, inputs):
            return []

    r = Retriever(Settings(), NoEmbed(), FakeStore({}))
    assert await r.retrieve("x", ["kotlin"]) == []
