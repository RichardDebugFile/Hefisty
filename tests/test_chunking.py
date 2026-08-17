from hefisty.knowledge.chunking import chunk_text


def test_markdown_splits_by_headers():
    text = "# Uno\ncontenido uno\n## Dos\ncontenido dos\n## Tres\ncontenido tres"
    chunks = chunk_text(text, "markdown", 512, 64)
    sections = {c.section for c in chunks}
    assert {"Uno", "Dos", "Tres"} <= sections


def test_large_section_size_split_with_overlap():
    text = "## Grande\n" + ("palabra " * 500)
    chunks = chunk_text(text, "markdown", 64, 8)  # ~256 chars por chunk
    assert len(chunks) > 1
    assert all(c.section == "Grande" for c in chunks)


def test_code_size_split():
    chunks = chunk_text("x = 1\n" * 400, "code", 64, 8)
    assert len(chunks) >= 1
    assert all(c.section == "" for c in chunks)


def test_empty_text_gives_no_chunks():
    assert chunk_text("", "markdown", 512, 64) == []


def test_chunks_are_indexed():
    chunks = chunk_text("# A\nx\n# B\ny", "markdown", 512, 64)
    assert [c.index for c in chunks] == list(range(len(chunks)))
