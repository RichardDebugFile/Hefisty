import pytest

from hefisty.agents.tools import ToolError, edit, glob, grep, read_range


def test_glob_top_and_recursive(tmp_path):
    (tmp_path / "a.py").write_text("x", encoding="utf-8")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.py").write_text("y", encoding="utf-8")
    assert glob(tmp_path, "*.py") == ["a.py"]
    assert "sub/b.py" in glob(tmp_path, "**/*.py")


def test_glob_escape_blocked(tmp_path):
    with pytest.raises(ToolError):
        glob(tmp_path, "../*")


def test_grep_finds_line(tmp_path):
    (tmp_path / "a.py").write_text("def foo():\n    pass\n", encoding="utf-8")
    res = grep(tmp_path, r"def \w+")
    assert res and res[0].startswith("a.py:1:")


def test_read_range(tmp_path):
    (tmp_path / "a.txt").write_text("l1\nl2\nl3\nl4\n", encoding="utf-8")
    out = read_range(tmp_path, "a.txt", 2, 3)
    assert "2: l2" in out and "3: l3" in out and "l1" not in out


def test_edit_unique_replacement(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("hola mundo", encoding="utf-8")
    edit(tmp_path, "a.txt", "mundo", "planeta")
    assert f.read_text(encoding="utf-8") == "hola planeta"


def test_edit_non_unique_fails(tmp_path):
    (tmp_path / "a.txt").write_text("x x x", encoding="utf-8")
    with pytest.raises(ToolError):
        edit(tmp_path, "a.txt", "x", "y")


def test_edit_missing_text_fails(tmp_path):
    (tmp_path / "a.txt").write_text("hola", encoding="utf-8")
    with pytest.raises(ToolError):
        edit(tmp_path, "a.txt", "chau", "hey")
