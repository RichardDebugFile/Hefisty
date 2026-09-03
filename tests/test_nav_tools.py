import pytest

from hefisty.agents.tools import ToolError, edit, glob, grep, har, outline, read_range


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


def test_grep_skips_noise_dirs_and_binaries(tmp_path):
    # El objetivo real: un .kt con el símbolo.
    (tmp_path / "Target.kt").write_text("val userScorePercentage = 1\n", encoding="utf-8")
    # Ruido que grep NO debe recorrer: .git binario y build/.
    git = tmp_path / ".git"
    git.mkdir()
    (git / "index").write_bytes(b"\x00\x01percentage\x00binary")
    build = tmp_path / "build"
    build.mkdir()
    (build / "gen.kt").write_text("val userScorePercentage = 999\n", encoding="utf-8")
    # Binario por extensión (imagen): fuera del allowlist.
    (tmp_path / "logo.png").write_bytes(b"\x89PNG\r\n\x1a\nPercentage")

    res = grep(tmp_path, "Percentage")
    assert res == ["Target.kt:1: val userScorePercentage = 1"]


def test_grep_ignores_non_text_extension(tmp_path):
    (tmp_path / "a.kt").write_text("match here\n", encoding="utf-8")
    (tmp_path / "b.bin").write_text("match here\n", encoding="utf-8")
    res = grep(tmp_path, "match")
    assert res == ["a.kt:1: match here"]


def test_grep_explicit_file_respected_regardless_of_extension(tmp_path):
    # Si el modelo apunta a un archivo puntual por su ruta, se respeta aunque no sea del allowlist.
    (tmp_path / "notes.log").write_text("boom\n", encoding="utf-8")
    res = grep(tmp_path, "boom", "notes.log")
    assert res == ["notes.log:1: boom"]


def test_grep_reads_big_logs(tmp_path):
    # Los logs de evidencia (logcat .txt, bundles .js, .json de labels) pesan 1-3 MB y SON el
    # objetivo del diagnóstico: antes se saltaban por el tope de 512 KB -> falso "sin resultados".
    (tmp_path / "logcat.txt").write_text("x" * (1024 * 1024) + "\nNEEDLE\n", encoding="utf-8")
    assert grep(tmp_path, "NEEDLE") == ["logcat.txt:2: NEEDLE"]


def test_grep_skips_absurdly_huge_files(tmp_path):
    # Sigue habiendo un tope, pero muy por encima de un log normal.
    (tmp_path / "dump.txt").write_text("y" * (9 * 1024 * 1024), encoding="utf-8")
    (tmp_path / "small.txt").write_text("NEEDLE\n", encoding="utf-8")
    assert grep(tmp_path, "NEEDLE") == ["small.txt:1: NEEDLE"]


def test_outline_lists_declarations(tmp_path):
    src = (
        "package com.x\n"
        "import y\n"
        "class Foo {\n"
        "    private fun bar() {}\n"
        "    val ignored = 1\n"
        "    override suspend fun baz(): Int = 2\n"
        "}\n"
        "object Singleton\n"
        "enum class Color { RED }\n"
    )
    (tmp_path / "Foo.kt").write_text(src, encoding="utf-8")
    res = outline(tmp_path, "Foo.kt")
    joined = "\n".join(res)
    assert "3: class Foo {" in res
    assert any("fun bar" in r for r in res) and any("fun baz" in r for r in res)
    assert "object Singleton" in joined and "enum class Color" in joined
    assert "ignored" not in joined  # val/var no se listan (serían ruido)


def test_glob_skips_noise_dirs(tmp_path):
    (tmp_path / "a.kt").write_text("x", encoding="utf-8")
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    res = glob(tmp_path, "**/*")
    assert "a.kt" in res and not any(".git" in r for r in res)


def test_glob_tolerant_variants(tmp_path):
    # El modelo escribe patrones con semántica de shell; pathlib devolvía vacío en silencio.
    pkg = tmp_path / "app" / "pedidos" / "detalle"
    pkg.mkdir(parents=True)
    (pkg / "Utils.kt").write_text("class U", encoding="utf-8")
    assert glob(tmp_path, "**/pedidos/**") == ["app/pedidos/detalle/Utils.kt"]
    assert glob(tmp_path, "pedidos/**") == ["app/pedidos/detalle/Utils.kt"]
    assert "app/pedidos/detalle/Utils.kt" in glob(tmp_path, "Utils.kt")


def test_grep_prefilter_keeps_anchors(tmp_path):
    # El pre-filtro usa MULTILINE: ^/$ deben seguir casando por línea, no solo al inicio.
    (tmp_path / "a.kt").write_text("primera\nval x = 1\nultima\n", encoding="utf-8")
    assert grep(tmp_path, r"^val x") == ["a.kt:2: val x = 1"]
    assert grep(tmp_path, r"ultima$") == ["a.kt:3: ultima"]
    assert grep(tmp_path, r"^nada") == []


def test_grep_with_context(tmp_path):
    # contexto=N muestra el código alrededor del hit (como grep -C), marcando la línea.
    (tmp_path / "a.kt").write_text("l1\nl2\nval target = 1\nl4\nl5\n", encoding="utf-8")
    res = grep(tmp_path, "target", contexto=2)
    joined = "\n".join(res)
    assert "a.kt:3:" in joined
    assert ">3: val target = 1" in joined  # la línea del hit va marcada
    assert " 1: l1" in joined and " 5: l5" in joined  # contexto arriba y abajo
    # sin contexto, formato clásico de una línea
    assert grep(tmp_path, "target") == ["a.kt:3: val target = 1"]


def test_grep_nonexistent_path_raises(tmp_path):
    # Un typo en la ruta devolvía "(sin resultados)" -> señal falsa. Debe fallar ruidosamente.
    (tmp_path / "a.kt").write_text("hit\n", encoding="utf-8")
    with pytest.raises(ToolError):
        grep(tmp_path, "hit", "no/existe/esta/ruta")
    assert grep(tmp_path, "hit") == ["a.kt:1: hit"]  # la ruta buena sigue funcionando


def test_har_lists_and_details(tmp_path):
    # Los .har que adjunta el usuario pesan >1 MB: hay que consultarlos sin volcarlos.
    import json as _json

    har_doc = {
        "log": {
            "entries": [
                {
                    "request": {
                        "method": "GET",
                        "url": "https://x/api/v1/otra",
                        "headers": [],
                    },
                    "response": {"status": 200},
                },
                {
                    "request": {
                        "method": "POST",
                        "url": "https://x/api/v1/recursos/crear",
                        "headers": [{"name": "component", "value": "abc123"}],
                        "postData": {"text": '{"transactionCode":"12"}'},
                    },
                    "response": {"status": 200, "content": {"text": '{"result":"KO"}'}},
                },
            ]
        }
    }
    (tmp_path / "web.har").write_text(_json.dumps(har_doc), encoding="utf-8")

    listado = har(tmp_path, "web.har", filtro="recursos/crear")
    assert "#1  POST 200" in listado and "api/v1/recursos" in listado
    assert "otra" not in listado  # el filtro descarta las demás

    detalle = har(tmp_path, "web.har", indice=1)
    assert "component: abc123" in detalle  # header clave para comparar web vs mobile
    assert '"transactionCode":"12"' in detalle  # body del request
    assert '"result":"KO"' in detalle  # respuesta


def test_har_rejects_non_har(tmp_path):
    (tmp_path / "x.json").write_text('{"no": "es har"}', encoding="utf-8")
    with pytest.raises(ToolError):
        har(tmp_path, "x.json")
