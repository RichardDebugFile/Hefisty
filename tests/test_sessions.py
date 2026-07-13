from hefisty.orchestrator.sessions import SessionStore


def test_create_get_roundtrip(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    s = store.create("hola")
    assert s.id
    got = store.get(s.id)
    assert got is not None
    assert got.title == "hola"
    assert got.messages == []
    assert got.active_agent == "hefisty"


def test_save_list_and_reload(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    s = store.create()
    s.messages.append({"role": "user", "content": "hola"})
    s.messages.append({"role": "assistant", "content": "qué tal"})
    s.active_agent = "coder"
    store.save(s)

    got = store.get(s.id)
    assert len(got.messages) == 2
    assert got.messages[0]["content"] == "hola"
    assert got.active_agent == "coder"

    listed = store.list()
    assert len(listed) == 1
    assert listed[0].id == s.id
    assert listed[0].messages == []  # la lista no carga el historial


def test_rename(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    s = store.create("viejo")
    store.rename(s.id, "nuevo")
    assert store.get(s.id).title == "nuevo"


def test_get_missing_returns_none(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    assert store.get("noexiste") is None
