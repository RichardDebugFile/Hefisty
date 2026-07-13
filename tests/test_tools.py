import pytest

from hefisty.agents.tools import (
    ToolError,
    escribir_archivo,
    leer_archivo,
    listar_directorio,
)


def test_write_then_read(tmp_path):
    msg = escribir_archivo(tmp_path, "a/b.txt", "hola")
    assert "escrito" in msg
    assert leer_archivo(tmp_path, "a/b.txt") == "hola"


def test_list_directory(tmp_path):
    escribir_archivo(tmp_path, "x.txt", "1")
    escribir_archivo(tmp_path, "sub/y.txt", "2")
    listing = listar_directorio(tmp_path)
    assert "x.txt" in listing
    assert "sub/" in listing


def test_escape_is_blocked(tmp_path):
    with pytest.raises(ToolError):
        leer_archivo(tmp_path, "../secreto.txt")
    with pytest.raises(ToolError):
        escribir_archivo(tmp_path, "../../evil.txt", "x")


def test_read_missing_file(tmp_path):
    with pytest.raises(ToolError):
        leer_archivo(tmp_path, "nope.txt")
