import pytest

from hefisty.agents import tools
from hefisty.config import Settings
from hefisty.knowledge.loaders import load_jsonl_pairs
from hefisty.semantic_cache import SemanticCache


def test_load_jsonl_pairs(tmp_path):
    p = tmp_path / "qa.jsonl"
    p.write_text(
        '{"u":"¿qué es X?","a":"X es Y"}\n\n{"u":"otra","a":"resp"}\nlinea-invalida\n',
        encoding="utf-8",
    )
    assert load_jsonl_pairs(p) == [("¿qué es X?", "X es Y"), ("otra", "resp")]


class _FakeStore:
    def __init__(self):
        self.deleted: list = []

    def delete_point(self, col, idx):
        self.deleted.append((col, idx))


async def test_semantic_cache_delete_targets_point():
    sc = SemanticCache(Settings(), None, _FakeStore())
    await sc.delete("hola quién eres")
    assert sc._store.deleted == [("semcache", "sem:hola quién eres")]


def test_grep_skips_escaping_symlink(tmp_path):
    outside = tmp_path.parent / "outside_grep"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("PASSWORD=zzz", encoding="utf-8")
    (tmp_path / "in.txt").write_text("contenido normal", encoding="utf-8")
    link = tmp_path / "leak.txt"
    try:
        link.symlink_to(outside / "secret.txt")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks no permitidos en este entorno")
    # grep no debe leer el archivo enlazado que apunta fuera del workspace
    assert tools.grep(tmp_path, "PASSWORD") == []


def test_glob_skips_escaping_symlink(tmp_path):
    outside = tmp_path.parent / "outside_glob"
    outside.mkdir(exist_ok=True)
    (outside / "x.py").write_text("secreto", encoding="utf-8")
    link = tmp_path / "link.py"
    try:
        link.symlink_to(outside / "x.py")
    except (OSError, NotImplementedError):
        pytest.skip("symlinks no permitidos en este entorno")
    assert "link.py" not in tools.glob(tmp_path, "*.py")
