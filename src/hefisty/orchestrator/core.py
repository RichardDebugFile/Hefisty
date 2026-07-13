"""Orquestador frontal de Hefisty.

En cada turno el modelo pequeño (frontal) decide si responde ella misma (charla,
aclaraciones, estado) o delega en el Coder. La decisión usa salida estructurada JSON.
El resultado se transmite en streaming y se persiste en la sesión; peticiones
idénticas se sirven desde la cache L1.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

from ..agents.coder import Coder
from ..cache import L1Cache
from ..config import Settings
from ..ollama_client import Message, OllamaClient
from ..roles import load_identity
from .router import Router
from .sessions import Session, SessionStore

# Ollama structured outputs: fuerza al frontal a devolver exactamente esta forma.
DECISION_SCHEMA = {
    "type": "object",
    "properties": {
        "action": {"type": "string", "enum": ["reply", "delegate"]},
        "agent": {"type": "string", "enum": ["coder"]},
    },
    "required": ["action"],
}

DECISION_INSTRUCTION = (
    "Decide si este turno lo respondes tú (charla, saludo, quién eres, estado del "
    "sistema, aclaraciones o peticiones ambiguas) o si es una tarea de programación "
    "concreta (escribir, corregir, revisar o explicar código) que debe ir al Coder. "
    'Responde SOLO JSON: {"action":"reply"} o {"action":"delegate","agent":"coder"}.'
)

# Namespace de modelo para la clave de cache (independiente del agente elegido).
_CACHE_NS = "auto"


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        ollama: OllamaClient,
        sessions: SessionStore,
        cache: L1Cache,
        coder: Coder,
        router: Router,
    ) -> None:
        self._s = settings
        self._ollama = ollama
        self._sessions = sessions
        self._cache = cache
        self._coder = coder
        self._router = router
        self._identity = load_identity()

    async def decide(self, session: Session, user_text: str) -> str:
        messages: list[Message] = [
            {"role": "system", "content": f"{self._identity}\n\n{DECISION_INSTRUCTION}"},
            *session.messages[-8:],
            {"role": "user", "content": user_text},
        ]
        raw = await self._ollama.chat(
            self._s.model_frontal,
            messages,
            keep_alive=self._s.keep_alive,
            fmt=DECISION_SCHEMA,
        )
        try:
            data = json.loads(raw)
        except (json.JSONDecodeError, TypeError):
            return "reply"
        if data.get("action") == "delegate" and data.get("agent", "coder") == "coder":
            return "delegate"
        return "reply"

    async def _reply_stream(self, session: Session, user_text: str) -> AsyncIterator[str]:
        messages: list[Message] = [
            {"role": "system", "content": self._identity},
            *session.messages[-16:],
            {"role": "user", "content": user_text},
        ]
        async for piece in self._ollama.chat_stream(
            self._s.model_frontal, messages, keep_alive=self._s.keep_alive
        ):
            yield piece

    async def stream_turn(self, session: Session, user_text: str) -> AsyncIterator[dict]:
        """Emite eventos {type: meta|content}. La primera es meta (agente/modelo)."""
        cache_msgs: list[Message] = [*session.messages, {"role": "user", "content": user_text}]

        cached = await self._cache.get(cache_msgs, _CACHE_NS)
        if cached is not None:
            data = json.loads(cached)
            yield {
                "type": "meta",
                "session_id": session.id,
                "agent": data["agent"],
                "model": data["model"],
                "cached": True,
            }
            yield {"type": "content", "text": data["content"]}
            await self._persist(session, user_text, data["content"], data["agent"])
            return

        action = await self.decide(session, user_text)
        if action == "delegate":
            agent, model = "coder", self._coder.model
            await self._router.activate(model)
            gen = self._coder.stream(
                [*session.messages[-16:], {"role": "user", "content": user_text}]
            )
            ttl = self._s.cache_ttl_code
        else:
            agent, model = "hefisty", self._s.model_frontal
            gen = self._reply_stream(session, user_text)
            ttl = self._s.cache_ttl_chat

        yield {
            "type": "meta",
            "session_id": session.id,
            "agent": agent,
            "model": model,
            "cached": False,
        }
        parts: list[str] = []
        async for piece in gen:
            parts.append(piece)
            yield {"type": "content", "text": piece}

        content = "".join(parts)
        await self._persist(session, user_text, content, agent)
        await self._cache.set(
            cache_msgs,
            _CACHE_NS,
            json.dumps({"agent": agent, "model": model, "content": content}),
            ttl,
        )

    async def _persist(
        self, session: Session, user_text: str, assistant_text: str, agent: str
    ) -> None:
        session.messages.append({"role": "user", "content": user_text})
        session.messages.append({"role": "assistant", "content": assistant_text})
        session.active_agent = agent
        if session.title == "Nueva sesión" and user_text.strip():
            session.title = user_text.strip()[:48]
        await asyncio.to_thread(self._sessions.save, session)
