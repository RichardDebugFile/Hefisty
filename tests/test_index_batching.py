"""El índice del repo y la ingesta suben a Qdrant por LOTES (un upsert único con un repo
grande supera el límite de 32 MB). Regresión del bug encontrado indexando un repo real.
"""

from hefisty.config import Settings
from hefisty.knowledge.ingest import ingest_path
from hefisty.knowledge.repo_index import index_repo


class FakeOllama:
    async def embed(self, model, inputs):
        return [[0.1, 0.2, 0.3] for _ in inputs]


class CountingStore:
    def __init__(self):
        self.upsert_calls = 0
        self.points = 0

    def ensure(self, collection, dim):
        pass

    def upsert(self, collection, vectors, payloads):
        self.upsert_calls += 1
        self.points += len(payloads)
        return len(payloads)


async def test_index_repo_upserts_in_batches(tmp_path, monkeypatch):
    monkeypatch.setattr("hefisty.knowledge.repo_index._EMBED_BATCH", 2)
    for i in range(5):
        (tmp_path / f"f{i}.py").write_text(f"x = {i}\n", encoding="utf-8")
    store = CountingStore()
    res = await index_repo(Settings(data_dir=tmp_path / "data"), FakeOllama(), store, tmp_path)
    assert res.chunks == 5
    assert store.points == 5
    assert store.upsert_calls == 3  # lotes de 2 -> 2+2+1, no un upsert único


async def test_ingest_path_upserts_in_batches(tmp_path, monkeypatch):
    monkeypatch.setattr("hefisty.knowledge.ingest._EMBED_BATCH", 2)
    src = tmp_path / "src"
    src.mkdir()
    for i in range(5):
        (src / f"d{i}.md").write_text(f"# doc {i}\n\ncontenido {i}\n", encoding="utf-8")
    store = CountingStore()
    res = await ingest_path(Settings(), FakeOllama(), store, "col", src, language="col")
    assert store.points == res.chunks >= 5
    assert store.upsert_calls >= 3  # por lotes, no un upsert único
