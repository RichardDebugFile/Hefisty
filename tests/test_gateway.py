from fastapi.testclient import TestClient

from hefisty.config import Settings
from hefisty.gateway.app import create_app
from hefisty.orchestrator.sessions import SessionStore


class FakeOrch:
    async def stream_turn(self, session, user_text):
        yield {
            "type": "meta",
            "session_id": session.id,
            "agent": "hefisty",
            "model": "qwen3:1.7b",
            "cached": False,
        }
        yield {"type": "content", "text": "hola "}
        yield {"type": "content", "text": "mundo"}


class FakeOllama:
    async def ping(self):
        return True

    async def loaded_models(self):
        return ["qwen3:1.7b"]


class FakeCache:
    def __init__(self):
        self.deleted: list[str] = []

    async def ping(self):
        return True

    async def delete_key(self, key):
        self.deleted.append(key)


class RaisingOrch:
    """Simula un fallo del modelo a mitad del turno (tras emitir meta)."""

    async def stream_turn(self, session, user_text):
        yield {
            "type": "meta",
            "session_id": session.id,
            "agent": "coder",
            "model": "m",
            "cached": False,
        }
        raise RuntimeError("ollama caído")


def _client(tmp_path, token="", orch=None):
    settings = Settings(api_token=token, data_dir=tmp_path)
    sessions = SessionStore(tmp_path / "s.db")
    app = create_app(
        settings=settings,
        ollama=FakeOllama(),
        sessions=sessions,
        cache=FakeCache(),
        orchestrator=orch or FakeOrch(),
    )
    return TestClient(app), sessions


def test_health_ok(tmp_path):
    client, _ = _client(tmp_path)
    r = client.get("/health")
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "ok"
    assert body["ollama"] is True
    assert body["redis"] is True
    assert body["loaded_models"] == ["qwen3:1.7b"]


def test_auth_required_when_token_set(tmp_path):
    client, _ = _client(tmp_path, token="secreto")
    assert client.get("/v1/sessions").status_code == 401
    ok = client.get("/v1/sessions", headers={"Authorization": "Bearer secreto"})
    assert ok.status_code == 200


def test_auth_disabled_when_token_empty(tmp_path):
    client, _ = _client(tmp_path)
    assert client.get("/v1/sessions").status_code == 200


def test_chat_streaming_sse(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hola"}], "stream": True},
    )
    assert r.status_code == 200
    body = r.text
    assert '"type": "meta"' in body
    assert "hefisty" in body
    assert "hola " in body and "mundo" in body
    assert "[DONE]" in body


def test_chat_non_streaming(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hola"}], "stream": False},
    )
    assert r.status_code == 200
    data = r.json()
    assert data["choices"][0]["message"]["content"] == "hola mundo"
    assert data["hefisty"]["agent"] == "hefisty"


def test_sessions_lifecycle(tmp_path):
    client, sessions = _client(tmp_path)
    s = sessions.create("prueba")
    listed = client.get("/v1/sessions").json()["sessions"]
    assert any(item["id"] == s.id for item in listed)

    resumed = client.post(f"/v1/sessions/{s.id}/resume").json()
    assert resumed["title"] == "prueba"

    renamed = client.patch(f"/v1/sessions/{s.id}", json={"title": "otro"})
    assert renamed.status_code == 200
    assert sessions.get(s.id).title == "otro"

    assert client.post("/v1/sessions/inexistente/resume").status_code == 404


def _client_with_cache(tmp_path):
    settings = Settings(data_dir=tmp_path)
    sessions = SessionStore(tmp_path / "s.db")
    cache = FakeCache()
    app = create_app(
        settings=settings,
        ollama=FakeOllama(),
        sessions=sessions,
        cache=cache,
        orchestrator=FakeOrch(),
    )
    return TestClient(app), sessions, cache


def test_feedback_down_stored_and_invalidates_cache(tmp_path):
    client, sessions, cache = _client_with_cache(tmp_path)
    r = client.post(
        "/v1/feedback",
        json={
            "vote": "down",
            "session_id": "s1",
            "agent": "coder",
            "model": "m",
            "cache_key": "hefisty:l1:xyz",
        },
    )
    assert r.status_code == 200
    assert r.json()["invalidated_cache"] is True
    assert "hefisty:l1:xyz" in cache.deleted
    assert len(sessions.list_feedback("s1")) == 1


def test_feedback_up_does_not_invalidate(tmp_path):
    client, _, cache = _client_with_cache(tmp_path)
    r = client.post("/v1/feedback", json={"vote": "up", "session_id": "s1", "cache_key": "k"})
    assert r.status_code == 200
    assert r.json()["invalidated_cache"] is False
    assert cache.deleted == []


def test_unknown_session_id_returns_404(tmp_path):
    client, _ = _client(tmp_path)
    r = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hola"}], "session_id": "nope"},
    )
    assert r.status_code == 404


def test_stream_error_emits_error_event_and_cleans_orphan(tmp_path):
    client, sessions = _client(tmp_path, orch=RaisingOrch())
    r = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hola"}], "stream": True},
    )
    assert r.status_code == 200
    body = r.text
    assert '"type": "error"' in body
    assert "[DONE]" in body
    assert sessions.list() == []  # sesión huérfana eliminada


def test_nonstream_error_returns_503_and_cleans_orphan(tmp_path):
    client, sessions = _client(tmp_path, orch=RaisingOrch())
    r = client.post(
        "/v1/chat/completions",
        json={"messages": [{"role": "user", "content": "hola"}], "stream": False},
    )
    assert r.status_code == 503
    assert sessions.list() == []
