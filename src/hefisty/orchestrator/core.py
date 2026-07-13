"""Orquestador frontal de Hefisty.

En cada turno el modelo pequeño (frontal) decide si responde ella misma (charla,
aclaraciones, estado) o delega en el Coder. La decisión usa salida estructurada JSON.
El resultado se transmite en streaming y se persiste en la sesión; peticiones
idénticas se sirven desde la cache L1.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator

from ..agents.coder import Coder
from ..cache import L1Cache
from ..config import Settings
from ..knowledge.retrieval import Retriever
from ..knowledge.store import Hit
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

logger = logging.getLogger("hefisty.orchestrator")

# Palabras que activan el diccionario de un lenguaje. Ampliable por rol en fases futuras.
_LANG_KEYWORDS = {
    "kotlin": (
        "kotlin",
        "android",
        "compose",
        "jetpack",
        "gradle",
        "coroutine",
        "corrutina",
        "room",
        "hilt",
        "viewmodel",
        ".kt",
        "livedata",
    ),
}


def detect_language(text: str) -> str | None:
    low = text.lower()
    for lang, kws in _LANG_KEYWORDS.items():
        if any(kw in low for kw in kws):
            return lang
    return None


class Orchestrator:
    def __init__(
        self,
        settings: Settings,
        ollama: OllamaClient,
        sessions: SessionStore,
        cache: L1Cache,
        coder: Coder,
        router: Router,
        retriever: Retriever | None = None,
    ) -> None:
        self._s = settings
        self._ollama = ollama
        self._sessions = sessions
        self._cache = cache
        self._coder = coder
        self._router = router
        self._retriever = retriever
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
                "sources": data.get("sources", []),
            }
            yield {"type": "content", "text": data["content"]}
            await self._persist(session, user_text, data["content"], data["agent"])
            return

        action = await self.decide(session, user_text)
        sources: list[dict] = []
        if action == "delegate":
            agent, model = "coder", self._coder.model
            await self._router.activate(model)
            hits, lang = await self._retrieve(user_text)
            coder_msgs: list[Message] = [
                *session.messages[-16:],
                {"role": "user", "content": user_text},
            ]
            if hits:
                coder_msgs = [self._context_message(hits), *coder_msgs]
                logger.info("retrieval: %d chunks (lang=%s)", len(hits), lang)
                sources = [
                    {"source": h.source, "section": h.section, "score": round(h.score, 3)}
                    for h in hits
                ]
            gen = self._coder.stream(coder_msgs)
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
            "sources": sources,
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
            json.dumps({"agent": agent, "model": model, "content": content, "sources": sources}),
            ttl,
        )

    async def _retrieve(self, user_text: str) -> tuple[list[Hit], str | None]:
        if self._retriever is None:
            return [], None
        lang = detect_language(user_text)
        collections = [c for c in [lang, "patrones"] if c]
        if not collections:
            return [], lang
        try:
            hits = await self._retriever.retrieve(user_text, collections)
        except Exception as exc:  # resiliencia: Qdrant caído no rompe el turno
            logger.warning("retrieval falló: %s", exc)
            return [], lang
        return hits, lang

    @staticmethod
    def _context_message(hits: list[Hit]) -> Message:
        blocks = "\n\n".join(f"[{h.source}] ({h.section})\n{h.text}" for h in hits)
        return {
            "role": "system",
            "content": (
                "Contexto recuperado del diccionario. Úsalo si es relevante y CITA la "
                "fuente entre corchetes, p. ej. [archivo.md]. Si no aporta, ignóralo.\n\n" + blocks
            ),
        }

    async def _persist(
        self, session: Session, user_text: str, assistant_text: str, agent: str
    ) -> None:
        session.messages.append({"role": "user", "content": user_text})
        session.messages.append({"role": "assistant", "content": assistant_text})
        session.active_agent = agent
        if session.title == "Nueva sesión" and user_text.strip():
            session.title = user_text.strip()[:48]
        await asyncio.to_thread(self._sessions.save, session)
