"""Agente Coder: genera/explica código en streaming usando su modelo dedicado.

Las herramientas de archivo (tools.py) quedan registradas en el manifiesto y listas
para el bucle agéntico de la Fase 3; en la Fase 2 el Coder responde en streaming.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from ..ollama_client import Message, OllamaClient
from ..roles import Role


class Coder:
    def __init__(self, ollama: OllamaClient, role: Role, keep_alive: str) -> None:
        self._ollama = ollama
        self._role = role
        self._keep_alive = keep_alive

    @property
    def model(self) -> str:
        return self._role.model

    async def stream(self, messages: list[Message]) -> AsyncIterator[str]:
        convo: list[Message] = [
            {"role": "system", "content": self._role.system_prompt},
            *messages,
        ]
        async for piece in self._ollama.chat_stream(self.model, convo, keep_alive=self._keep_alive):
            yield piece
