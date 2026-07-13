from hefisty.orchestrator.router import Router


class FakeOllama:
    def __init__(self) -> None:
        self.unloaded: list[str] = []

    async def unload(self, model: str) -> None:
        self.unloaded.append(model)


async def test_small_model_is_noop():
    o = FakeOllama()
    r = Router(o, {"qwen3:1.7b", "nomic-embed-text"})
    await r.activate("qwen3:1.7b")
    assert o.unloaded == []


async def test_first_big_model_no_unload():
    o = FakeOllama()
    r = Router(o, {"qwen3:1.7b"})
    await r.activate("coder-a:14b")
    assert o.unloaded == []


async def test_switch_big_model_unloads_previous():
    o = FakeOllama()
    r = Router(o, {"qwen3:1.7b"})
    await r.activate("coder-a:14b")
    await r.activate("coder-b:14b")
    assert o.unloaded == ["coder-a:14b"]  # nunca dos grandes a la vez


async def test_same_big_model_no_reunload():
    o = FakeOllama()
    r = Router(o, {"qwen3:1.7b"})
    await r.activate("coder-a:14b")
    await r.activate("coder-a:14b")
    assert o.unloaded == []
