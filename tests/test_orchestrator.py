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


async def test_delegate_unloads_coder_after_turn(tmp_path):
    orch, sessions, ollama, settings = _build(tmp_path, "delegate")
    s = sessions.create()
    await _collect(orch.stream_turn(s, "escribe una función en python"))
    # El Coder se descarga de VRAM al terminar (no queda residente ocioso).
    assert ("unload", settings.model_coder) in ollama.calls


async def test_reply_does_not_unload_any_model(tmp_path):
    orch, sessions, ollama, settings = _build(tmp_path, "reply")
    s = sessions.create()
    await _collect(orch.stream_turn(s, "hola"))
    # Charla: nunca se cargó ni se descarga un modelo grande.
    assert not any(c[0] == "unload" for c in ollama.calls)


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


class FakeSemantic:
    def __init__(self, hit=None):
        self.hit = hit
        self.put_calls: list = []

    async def get(self, query):
        return self.hit

    async def put(self, query, value):
        self.put_calls.append((query, value))


async def test_semantic_cache_hit_on_reply(tmp_path):
    orch, sessions, ollama, settings = _build(tmp_path, "reply")
    orch._semantic = FakeSemantic(
        hit={"agent": "hefisty", "model": "m", "content": "respuesta cacheada", "sources": []}
    )
    s = sessions.create()
    meta, text = await _collect(orch.stream_turn(s, "¿quién eres tú?"))
    assert meta["cached"] is True
    assert text == "respuesta cacheada"
    assert not any(c[0] == "stream" for c in ollama.calls)  # no se generó


async def test_semantic_put_on_reply_generation(tmp_path):
    orch, sessions, ollama, settings = _build(tmp_path, "reply")
    sem = FakeSemantic(hit=None)
    orch._semantic = sem
    s = sessions.create()
    await _collect(orch.stream_turn(s, "hola"))
    assert len(sem.put_calls) == 1


async def test_coder_turn_is_never_cached(tmp_path):
    orch, sessions, ollama, settings = _build(tmp_path, "delegate")
    sem = FakeSemantic(hit=None)
    orch._semantic = sem
    s = sessions.create()
    await _collect(orch.stream_turn(s, "escribe una función en python"))
    assert sem.put_calls == []  # semántica: nunca para el Coder
    assert orch._cache.store == {}  # exacta: tampoco


class ChainFakeOllama:
    def __init__(self):
        self.calls: list = []

    async def chat(self, model, messages, *, keep_alive="10m", fmt=None, options=None):
        return json.dumps({"action": "delegate", "agent": "coder"})

    async def chat_stream(self, model, messages, *, keep_alive="10m", options=None):
        system = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        text = "hallazgo: usa nombres claros" if "Revisor" in system else "def f(): pass"
        for word in text.split():
            yield word + " "

    async def unload(self, model):
        pass

    async def loaded_models(self):
        return []


def _build_chain(tmp_path):
    settings = Settings()
    ollama = ChainFakeOllama()
    sessions = SessionStore(tmp_path / "s.db")
    coder = Coder(ollama, load_role("coder"), settings.keep_alive)
    revisor = Coder(ollama, load_role("revisor"), settings.keep_alive)
    router = Router(ollama, {settings.model_frontal, settings.model_embed})
    orch = Orchestrator(
        settings,
        ollama,
        sessions,
        FakeCache(),
        coder,
        router,
        agents={"coder": coder, "revisor": revisor},
    )
    return orch, sessions


async def test_chain_coder_then_revisor(tmp_path):
    orch, sessions = _build_chain(tmp_path)
    s = sessions.create()
    agents_seen, parts = [], []
    async for ev in orch.stream_turn(s, "escribe una función y revísala"):
        (agents_seen if ev["type"] == "meta" else parts).append(
            ev["agent"] if ev["type"] == "meta" else ev["text"]
        )
    assert agents_seen == ["coder", "revisor"]
    reloaded = sessions.get(s.id)
    assert [st["state"] for st in reloaded.subtasks] == ["hecha", "hecha"]
    assert "hallazgo" in "".join(parts)  # hallazgos del revisor en la respuesta


async def test_streaming_redacts_credential_split_across_chunks(tmp_path):
    orch, sessions, ollama, settings = _build(tmp_path, "reply")

    async def split_stream(model, messages, *, keep_alive="10m", options=None):
        # el modelo emite una credencial partida en varios chunks de streaming
        for piece in ["Tu token es sk-", "ABCDEFGHIJKLMNOPQR", "STUVWXYZ0123456789", " listo."]:
            yield piece

    ollama.chat_stream = split_stream
    s = sessions.create()
    _meta, text = await _collect(orch.stream_turn(s, "dame el token"))
    assert "[REDACTADO]" in text
    assert "sk-ABCDEF" not in text  # la credencial cruda no llega al cliente


async def test_resume_continues_chain_from_pending(tmp_path):
    orch, sessions = _build_chain(tmp_path)
    s = sessions.create()
    # Cadena a medias: coder hecha, revisor pendiente (simula muerte del proceso).
    s.subtasks = [
        {"agent": "coder", "input": "tarea original", "state": "hecha", "result": "def f(): pass"},
        {"agent": "revisor", "input": "", "state": "pendiente", "result": ""},
    ]
    sessions.save(s)

    agents_seen = []
    async for ev in orch.resume_chain(sessions.get(s.id)):
        if ev["type"] == "meta":
            agents_seen.append(ev["agent"])
    assert agents_seen == ["revisor"]  # retoma solo desde la subtarea pendiente
    reloaded = sessions.get(s.id)
    assert reloaded.subtasks[1]["state"] == "hecha"
