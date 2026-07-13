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


def test_absolute_path_blocked(tmp_path):
    outside = tmp_path.parent / "outside.txt"
    with pytest.raises(ToolError):
        leer_archivo(tmp_path, str(outside))
    with pytest.raises(ToolError):
        escribir_archivo(tmp_path, "/etc/passwd", "x")


def test_drive_relative_blocked(tmp_path):
    with pytest.raises(ToolError):
        leer_archivo(tmp_path, "C:evil.txt")


def test_symlink_escape_blocked(tmp_path):
    outside = tmp_path.parent / "outside_dir"
    outside.mkdir(exist_ok=True)
    (outside / "secret.txt").write_text("s", encoding="utf-8")
    link = tmp_path / "link"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks no permitidos en este entorno")
    with pytest.raises(ToolError):
        leer_archivo(tmp_path, "link/secret.txt")
