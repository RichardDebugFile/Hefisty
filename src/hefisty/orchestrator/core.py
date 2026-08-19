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
import re
from collections.abc import AsyncIterator

from ..agents.coder import Coder
from ..cache import L1Cache
from ..config import Settings
from ..knowledge.retrieval import Retriever
from ..knowledge.store import Hit
from ..lang import detect_language
from ..memory import MemoryService
from ..ollama_client import Message, OllamaClient
from ..protections import redact_credentials, sanitize_chunk
from ..roles import load_identity
from ..semantic_cache import SemanticCache
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
        semantic: SemanticCache | None = None,
        agents: dict[str, Coder] | None = None,
        memory: MemoryService | None = None,
    ) -> None:
        self._s = settings
        self._ollama = ollama
        self._sessions = sessions
        self._cache = cache
        self._coder = coder
        self._router = router
        self._retriever = retriever
        self._semantic = semantic
        self._memory = memory
        # Registro de agentes para el encadenamiento. El Coder siempre está.
        self._agents = agents or {"coder": coder}
        self._identity = load_identity()

    def _plan_chain(self, user_text: str) -> list[str]:
        """Cadena de agentes según la petición (opt-in por palabras clave)."""
        low = user_text.lower()
        chain = ["coder"]
        if re.search(r"rev[ií]s|review", low) and "revisor" in self._agents:
            chain.append("revisor")
        if re.search(r"document|docstring|readme", low) and "docs" in self._agents:
            chain.append("docs")
        return chain

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

    async def _memory_context(self, user_text: str) -> str:
        """Recuerdos relevantes de sesiones anteriores para el contexto del frontal."""
        if self._memory is None:
            return ""
        try:
            facts = await self._memory.recall(user_text)
        except Exception as exc:  # Qdrant caído no rompe el turno
            logger.warning("recall de memoria falló: %s", exc)
            return ""
        if not facts:
            return ""
        joined = "\n".join(f"- {f}" for f in facts)
        return (
            "\n\nRecuerdos sobre el usuario (de sesiones anteriores; úsalos si son "
            "relevantes, no los recites literalmente):\n" + joined
        )

    async def _reply_stream(self, session: Session, user_text: str) -> AsyncIterator[str]:
        memory = await self._memory_context(user_text)
        messages: list[Message] = [
            {"role": "system", "content": self._identity + memory},
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
        cache_key = self._cache.key_for(cache_msgs, _CACHE_NS)
        # La cache semántica solo aplica al primer turno (sin historial): un follow-up como
        # "sigue" o "¿en qué íbamos?" depende del contexto de SU sesión y no debe compartirse.
        semantic_ok = self._semantic is not None and not session.messages

        cached = await self._cache.get(cache_msgs, _CACHE_NS)
        if cached is not None:
            data = json.loads(cached)
            yield {
                "type": "meta",
                "session_id": session.id,
                "agent": data["agent"],
                "model": data["model"],
                "cached": True,
                "cache_key": cache_key,
                "sources": data.get("sources", []),
            }
            yield {"type": "content", "text": data["content"]}
            await self._persist(session, user_text, data["content"], data["agent"])
            return

        action = await self.decide(session, user_text)
        sources: list[dict] = []
        if action == "delegate":
            chain = self._plan_chain(user_text)
            if len(chain) > 1:
                async for ev in self._stream_chain(session, user_text, chain):
                    yield ev
                return
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
            cacheable = False  # tareas del Coder no se cachean (el workspace cambia)
        else:
            agent, model = "hefisty", self._s.model_frontal
            # Cache semántica (parafraseos): solo charla/conocimiento, primer turno.
            if semantic_ok:
                sc = await self._semantic.get(user_text)
                if sc is not None:
                    yield {
                        "type": "meta",
                        "session_id": session.id,
                        "agent": sc.get("agent", agent),
                        "model": sc.get("model", model),
                        "cached": True,
                        "cache_key": cache_key,
                        "sources": sc.get("sources", []),
                    }
                    yield {"type": "content", "text": sc["content"]}
                    await self._persist(session, user_text, sc["content"], sc.get("agent", agent))
                    return
            gen = self._reply_stream(session, user_text)
            cacheable = True

        yield {
            "type": "meta",
            "session_id": session.id,
            "agent": agent,
            "model": model,
            "cached": False,
            "cache_key": cache_key,
            "sources": sources,
        }
        content = ""
        async for kind, text in self._stream_redacted(gen):
            if kind == "content":
                yield {"type": "content", "text": text}
            else:
                content = text
        await self._persist(session, user_text, content, agent)
        if cacheable:
            value = {"agent": agent, "model": model, "content": content, "sources": sources}
            await self._cache.set(cache_msgs, _CACHE_NS, json.dumps(value), self._s.cache_ttl_chat)
            if semantic_ok:
                await self._semantic.put(user_text, value)
        # Delegación terminada: libera la VRAM del Coder en vez de dejarlo residente ocioso.
        if action == "delegate" and self._s.unload_coder_after_turn:
            await self._router.release()

    async def _stream_chain(
        self, session: Session, user_text: str, chain: list[str]
    ) -> AsyncIterator[dict]:
        session.subtasks = [
            {"agent": a, "input": "", "state": "pendiente", "result": ""} for a in chain
        ]
        session.subtasks[0]["input"] = user_text
        await asyncio.to_thread(self._sessions.save, session)
        async for ev in self._run_chain_from(session, user_text, 0):
            yield ev

    async def resume_chain(self, session: Session) -> AsyncIterator[dict]:
        """Continúa una cadena a medias desde la subtarea siguiente a la última completada."""
        pending = [i for i, st in enumerate(session.subtasks) if st["state"] != "hecha"]
        if not pending:
            return
        user_text = session.subtasks[0].get("input") or ""
        async for ev in self._run_chain_from(session, user_text, pending[0]):
            yield ev

    async def _run_chain_from(
        self, session: Session, user_text: str, start: int
    ) -> AsyncIterator[dict]:
        prev = session.subtasks[start - 1]["result"] if start > 0 else user_text
        for i in range(start, len(session.subtasks)):
            st = session.subtasks[i]
            name = st["agent"]
            agent_obj = self._agents.get(name)
            if agent_obj is None:
                st["state"] = "fallida"
                await asyncio.to_thread(self._sessions.save, session)
                continue
            step_input = user_text if i == 0 else self._chain_prompt(name, user_text, prev)
            st["input"] = step_input[:1000]
            st["state"] = "en_curso"
            await asyncio.to_thread(self._sessions.save, session)

            msgs: list[Message] = [{"role": "user", "content": step_input}]
            sources: list[dict] = []
            if name == "coder":
                hits, _lang = await self._retrieve(user_text)
                if hits:
                    msgs = [self._context_message(hits), *msgs]
                    sources = [
                        {"source": h.source, "section": h.section, "score": round(h.score, 3)}
                        for h in hits
                    ]
            await self._router.activate(agent_obj.model)
            yield {
                "type": "meta",
                "session_id": session.id,
                "agent": name,
                "model": agent_obj.model,
                "cached": False,
                "cache_key": "",
                "sources": sources,
                "chain": self._chain_state(session),
            }
            result = ""
            async for kind, text in self._stream_redacted(agent_obj.stream(msgs)):
                if kind == "content":
                    yield {"type": "content", "text": text}
                else:
                    result = text
            st["result"] = result
            st["state"] = "hecha"
            await asyncio.to_thread(self._sessions.save, session)
            prev = st["result"]

        final = self._combine_chain(session.subtasks)
        await self._persist(session, user_text, final, session.subtasks[-1]["agent"])
        # Cadena terminada: libera la VRAM del modelo grande (Coder/Revisor/Docs comparten uno).
        if self._s.unload_coder_after_turn:
            await self._router.release()

    @staticmethod
    def _chain_prompt(name: str, user_text: str, prev: str) -> str:
        if name == "revisor":
            return f"Revisa este resultado para la tarea «{user_text}»:\n\n{prev}"
        if name == "docs":
            return f"Documenta lo siguiente:\n\n{prev}"
        return prev

    @staticmethod
    def _chain_state(session: Session) -> list[dict]:
        return [{"agent": st["agent"], "state": st["state"]} for st in session.subtasks]

    @staticmethod
    def _combine_chain(subtasks: list[dict]) -> str:
        return "\n\n".join(
            f"### {st['agent']}\n{st['result']}" for st in subtasks if st.get("result")
        )

    @staticmethod
    async def _stream_redacted(gen: AsyncIterator[str]):
        """Redacta credenciales sobre un stream aguantando una cola, por si una credencial
        cruza el límite entre chunks. Emite ('content', txt) y al final ('final', completo)."""
        tail = 200  # ninguna credencial soportada supera esta longitud
        acc = ""
        emitted = 0
        async for piece in gen:
            acc += piece
            red, _ = redact_credentials(acc)
            safe = max(0, len(red) - tail)
            if safe > emitted:
                yield ("content", red[emitted:safe])
                emitted = safe
        red, _ = redact_credentials(acc)
        if len(red) > emitted:
            yield ("content", red[emitted:])
        yield ("final", red)

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
        parts = []
        for h in hits:
            # Los chunks pueden venir de docs de terceros: degradar si traen injection.
            safe, _degraded = sanitize_chunk(h.text)
            parts.append(f"[{h.source}] ({h.section})\n{safe}")
        blocks = "\n\n".join(parts)
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
        await self._maybe_consolidate(session)

    async def _maybe_consolidate(self, session: Session) -> None:
        """Cada N turnos, el frontal repasa la conversación y extrae recuerdos nuevos."""
        if self._memory is None:
            return
        turns = len(session.messages) // 2
        if turns == 0 or turns % self._s.memory_consolidate_every != 0:
            return
        try:
            n = await self._memory.consolidate(session.messages, origin=session.id)
            if n:
                logger.info("memoria: %d recuerdos consolidados (sesión %s)", n, session.id)
        except Exception as exc:  # la consolidación nunca debe tumbar el turno
            logger.warning("consolidación de memoria falló: %s", exc)
