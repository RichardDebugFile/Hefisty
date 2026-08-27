import httpx
import pytest

from hefisty.ollama_client import _MAX_TRIES, OllamaClient, OllamaError


async def _noop(*_args, **_kwargs):
    return None


async def test_chat_tools_retries_on_transient_500(monkeypatch):
    monkeypatch.setattr("hefisty.ollama_client.asyncio.sleep", _noop)
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        if calls["n"] == 1:  # primer intento: 500 transitorio
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"message": {"content": "ok", "tool_calls": []}})

    client = OllamaClient("http://x")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    msg = await client.chat_tools("m", [{"role": "user", "content": "hi"}], [])
    assert msg["content"] == "ok"
    assert calls["n"] == 2  # reintentó una vez y lo logró
    await client.aclose()


async def test_chat_tools_gives_up_after_max_500(monkeypatch):
    monkeypatch.setattr("hefisty.ollama_client.asyncio.sleep", _noop)
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(500, text="boom")

    client = OllamaClient("http://x")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(OllamaError):
        await client.chat_tools("m", [{"role": "user", "content": "hi"}], [])
    assert calls["n"] == _MAX_TRIES  # agota los intentos y se rinde
    await client.aclose()


async def test_chat_does_not_retry_on_4xx(monkeypatch):
    monkeypatch.setattr("hefisty.ollama_client.asyncio.sleep", _noop)
    calls = {"n": 0}

    def handler(_request):
        calls["n"] += 1
        return httpx.Response(404, text="no such model")

    client = OllamaClient("http://x")
    client._client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    with pytest.raises(OllamaError):
        await client.chat("m", [{"role": "user", "content": "hi"}])
    assert calls["n"] == 1  # 4xx no se reintenta
    await client.aclose()
