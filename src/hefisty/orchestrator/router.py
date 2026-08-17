"""Router de modelos: aplica la política 'nunca más de un modelo grande cargado'.

El frontal (1.7B) y embeddings son pequeños y pueden convivir. Al activar un agente
grande, se descarga cualquier otro modelo grande previo (keep_alive=0). En la Fase 2
solo el Coder es grande, pero la política ya queda lista para más agentes.
"""

from __future__ import annotations

from ..ollama_client import OllamaClient


class Router:
    def __init__(self, ollama: OllamaClient, small_models: set[str]) -> None:
        self._ollama = ollama
        self._small = small_models
        self._current_big: str | None = None

    async def activate(self, model: str) -> None:
        if model in self._small or model == self._current_big:
            return
        if self._current_big is not None:
            await self._ollama.unload(self._current_big)
        self._current_big = model

    async def release(self) -> None:
        """Descarga el modelo grande activo al terminar el turno para liberar VRAM.

        Sin esto, el Coder (~13 GB) queda residente `keep_alive` minutos sin trabajar.
        El frontal y embeddings (pequeños) nunca se tocan.
        """
        if self._current_big is not None:
            await self._ollama.unload(self._current_big)
            self._current_big = None
