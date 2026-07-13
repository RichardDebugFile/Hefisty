import json

from hefisty.agents.coder import Coder
from hefisty.config import Settings
from hefisty.orchestrator.core import Orchestrator
from hefisty.orchestrator.router import Router
from hefisty.orchestrator.sessions import SessionStore
from hefisty.roles import load_role


class FakeOllama:
    """Sustituye a Ollama: `chat` devuelve la decisión, `chat_stream` texto simulado."""

    def __init__(self, decision: str) -> None:
        self.decision = decision
        self.calls: list[tuple[str, str]] = []

    async def chat(self, model, messages, *, keep_alive="10m", fmt=None, options=None):
        self.calls.append(("chat", model))
        return json.dumps({"action": self.decision, "agent": "coder"})

    async def chat_stream(self, model, messages, *, keep_alive="10m", options=None):
        self.calls.append(("stream", model))
        text = "def f(): pass" if "coder" in model else "Hola soy Hefisty"
        for word in text.split():
            yield word + " "

    async def unload(self, model):
        self.calls.append(("unload", model))

    async def loaded_models(self):
        return []


class FakeCache:
    def __init__(self) -> None:
        self.store: dict[str, str] = {}

    def key_for(self, messages, model):
        return "k:" + json.dumps(messages, sort_keys=True)

    async def get(self, messages, model):
        return self.store.get(json.dumps(messages, sort_keys=True))

    async def set(self, messages, model, value, ttl):
        self.store[json.dumps(messages, sort_keys=True)] = value


def _build(tmp_path, decision):
    settings = Settings()
    ollama = FakeOllama(decision)
    sessions = SessionStore(tmp_path / "s.db")
    cache = FakeCache()
    coder = Coder(ollama, load_role("coder"), settings.keep_alive)
    router = Router(ollama, {settings.model_frontal, settings.model_embed})
    orch = Orchestrator(settings, ollama, sessions, cache, coder, router)
    return orch, sessions, ollama, settings


async def _collect(gen):
    meta = None
    parts = []
    async for ev in gen:
        if ev["type"] == "meta":
            meta = ev
        else:
            parts.append(ev["text"])
    return meta, "".join(parts)


async def test_reply_never_loads_coder(tmp_path):
    orch, sessions, ollama, settings = _build(tmp_path, "reply")
    s = sessions.create()
    meta, text = await _collect(orch.stream_turn(s, "hola, ¿quién eres?"))
    assert meta["agent"] == "hefisty"
    assert meta["model"] == settings.model_frontal
    assert not any(m == settings.model_coder for _, m in ollama.calls)
    assert text.strip()


async def test_delegate_routes_to_coder(tmp_path):
    orch, sessions, ollama, settings = _build(tmp_path, "delegate")
    s = sessions.create()
    meta, text = await _collect(orch.stream_turn(s, "escribe una función en python"))
    assert meta["agent"] == "coder"
    assert meta["model"] == settings.model_coder
    assert ("stream", settings.model_coder) in ollama.calls
    assert text.strip()


async def test_persists_turn_and_autotitle(tmp_path):
    orch, sessions, ollama, settings = _build(tmp_path, "reply")
    s = sessions.create()
    await _collect(orch.stream_turn(s, "hola"))
    reloaded = sessions.get(s.id)
    assert len(reloaded.messages) == 2  # user + assistant
    assert reloaded.title == "hola"


async def test_identical_request_hits_cache(tmp_path):
    orch, sessions, ollama, settings = _build(tmp_path, "reply")
    s1 = sessions.create()
    await _collect(orch.stream_turn(s1, "hola"))
    calls_before = len(ollama.calls)

    s2 = sessions.create()  # sesión nueva, mismo primer mensaje
    meta, _ = await _collect(orch.stream_turn(s2, "hola"))
    assert meta["cached"] is True
    assert len(ollama.calls) == calls_before  # no hubo llamadas al modelo
