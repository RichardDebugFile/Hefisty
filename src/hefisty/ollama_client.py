"""Cliente async de Ollama (API REST nativa).

Cubre lo que la Fase 2 necesita: chat con y sin streaming, salida estructurada
(`format`), listado de modelos instalados/cargados y descarga bajo demanda
(`keep_alive=0`). Un modelo grande a la vez: el router usa esto para intercambiar.
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
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                r = await client.post(f"{self._base}/api/chat", json=payload)
                r.raise_for_status()
            except httpx.HTTPError as exc:  # pragma: no cover - red
                raise OllamaError(f"chat falló: {exc}") from exc
            return r.json().get("message", {}).get("content", "")

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
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            async with client.stream("POST", f"{self._base}/api/chat", json=payload) as r:
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
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                r = await client.post(
                    f"{self._base}/api/embed", json={"model": model, "input": inputs}
                )
                r.raise_for_status()
            except httpx.HTTPError as exc:  # pragma: no cover - red
                raise OllamaError(f"embed falló: {exc}") from exc
            return r.json().get("embeddings", [])

    async def list_models(self) -> list[str]:
        """Modelos instalados (`/api/tags`)."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            r = await client.get(f"{self._base}/api/tags")
            r.raise_for_status()
            return [m["name"] for m in r.json().get("models", [])]

    async def loaded_models(self) -> list[str]:
        """Modelos residentes en memoria ahora mismo (`/api/ps`)."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                r = await client.get(f"{self._base}/api/ps")
                r.raise_for_status()
            except httpx.HTTPError:
                return []
            return [m["name"] for m in r.json().get("models", [])]

    async def unload(self, model: str) -> None:
        """Descarga un modelo de VRAM de inmediato (`keep_alive=0`)."""
        async with httpx.AsyncClient(timeout=30.0) as client:
            try:
                await client.post(
                    f"{self._base}/api/chat",
                    json={"model": model, "messages": [], "keep_alive": 0},
                )
            except httpx.HTTPError:  # pragma: no cover - red
                pass

    async def ping(self) -> bool:
        """True si Ollama responde."""
        async with httpx.AsyncClient(timeout=5.0) as client:
            try:
                r = await client.get(f"{self._base}/api/version")
                return r.status_code == 200
            except httpx.HTTPError:
                return False
