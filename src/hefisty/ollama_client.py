"""Cliente async de Ollama (API REST nativa).

Chat con y sin streaming, salida estructurada (`format`), function calling (`tools`),
embeddings, y gestión de modelos (listar/cargados/descargar). Reutiliza un único
`httpx.AsyncClient` (pool de conexiones); ciérralo con `aclose()` en el shutdown.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from typing import Any

import httpx

Message = dict[str, str]


class OllamaError(RuntimeError):
    """Fallo hablando con Ollama."""


class OllamaClient:
    def __init__(self, base_url: str, timeout: float = 300.0) -> None:
        self._base = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    def _c(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def chat(
        self,
        model: str,
        messages: list[Message],
        *,
        keep_alive: str | int = "10m",
        fmt: Any | None = None,
        options: dict[str, Any] | None = None,
    ) -> str:
        """Respuesta completa (no streaming). Devuelve el texto del mensaje."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": keep_alive,
        }
        if fmt is not None:
            payload["format"] = fmt
        if options:
            payload["options"] = options
        try:
            r = await self._c().post(f"{self._base}/api/chat", json=payload)
            r.raise_for_status()
        except httpx.HTTPError as exc:  # pragma: no cover - red
            raise OllamaError(f"chat falló: {exc}") from exc
        return r.json().get("message", {}).get("content", "")

    async def chat_tools(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]],
        *,
        keep_alive: str | int = "10m",
    ) -> dict[str, Any]:
        """Chat con function calling. Devuelve el mensaje (con `content` y `tool_calls`)."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": False,
            "keep_alive": keep_alive,
            "tools": tools,
        }
        try:
            r = await self._c().post(f"{self._base}/api/chat", json=payload)
            r.raise_for_status()
        except httpx.HTTPError as exc:  # pragma: no cover - red
            raise OllamaError(f"chat_tools falló: {exc}") from exc
        return r.json().get("message", {})

    async def chat_stream(
        self,
        model: str,
        messages: list[Message],
        *,
        keep_alive: str | int = "10m",
        options: dict[str, Any] | None = None,
    ) -> AsyncIterator[str]:
        """Genera piezas de texto conforme llegan (para SSE)."""
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "keep_alive": keep_alive,
        }
        if options:
            payload["options"] = options
        async with self._c().stream("POST", f"{self._base}/api/chat", json=payload) as r:
            r.raise_for_status()
            async for line in r.aiter_lines():
                if not line.strip():
                    continue
                chunk = json.loads(line)
                piece = chunk.get("message", {}).get("content", "")
                if piece:
                    yield piece
                if chunk.get("done"):
                    break

    async def embed(self, model: str, inputs: list[str]) -> list[list[float]]:
        """Embeddings de una lista de textos (`/api/embed`)."""
        try:
            r = await self._c().post(
                f"{self._base}/api/embed", json={"model": model, "input": inputs}
            )
            r.raise_for_status()
        except httpx.HTTPError as exc:  # pragma: no cover - red
            raise OllamaError(f"embed falló: {exc}") from exc
        return r.json().get("embeddings", [])

    async def list_models(self) -> list[str]:
        """Modelos instalados (`/api/tags`)."""
        r = await self._c().get(f"{self._base}/api/tags", timeout=30.0)
        r.raise_for_status()
        return [m["name"] for m in r.json().get("models", [])]

    async def loaded_models(self) -> list[str]:
        """Modelos residentes en memoria ahora mismo (`/api/ps`)."""
        return [m.get("name", "") for m in await self.running()]

    async def running(self) -> list[dict]:
        """Detalle de los modelos cargados (`/api/ps`): incluye `size_vram`."""
        try:
            r = await self._c().get(f"{self._base}/api/ps", timeout=30.0)
            r.raise_for_status()
        except httpx.HTTPError:
            return []
        return r.json().get("models", [])

    async def unload(self, model: str) -> None:
        """Descarga un modelo de VRAM de inmediato (`keep_alive=0`)."""
        try:
            await self._c().post(
                f"{self._base}/api/chat",
                json={"model": model, "messages": [], "keep_alive": 0},
                timeout=30.0,
            )
        except httpx.HTTPError:  # pragma: no cover - red
            pass

    async def ping(self) -> bool:
        """True si Ollama responde."""
        try:
            r = await self._c().get(f"{self._base}/api/version", timeout=5.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False
