"""Gateway FastAPI: único punto de entrada.

Escucha en 127.0.0.1, autentica con Bearer, aplica protección de entrada mínima
(límite de tamaño), expone la API OpenAI-compatible con streaming SSE, gestiona
sesiones y sirve el front compilado (`web/dist`) en `/`.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from ..agents.coder import Coder
from ..cache import L1Cache
from ..config import Settings, get_settings
from ..knowledge.retrieval import Retriever
from ..knowledge.store import KnowledgeStore
from ..ollama_client import OllamaClient
from ..orchestrator.core import Orchestrator
from ..orchestrator.router import Router
from ..orchestrator.sessions import SessionStore
from ..protections import detect_injection
from ..roles import list_roles, load_role
from ..semantic_cache import SemanticCache
from .auth import make_auth_dep
from .schemas import ChatRequest, FeedbackRequest, RenameRequest

logger = logging.getLogger("hefisty.gateway")


async def _qdrant_ok(url: str) -> bool:
    async with httpx.AsyncClient(timeout=5.0) as client:
        try:
            r = await client.get(f"{url.rstrip('/')}/healthz")
            return r.status_code == 200
        except httpx.HTTPError:
            return False


def create_app(
    *,
    settings: Settings | None = None,
    ollama: OllamaClient | None = None,
    sessions: SessionStore | None = None,
    cache: L1Cache | None = None,
    orchestrator: Orchestrator | None = None,
) -> FastAPI:
    """Construye la app. Los componentes son inyectables para tests."""
    settings = settings or get_settings()
    ollama = ollama or OllamaClient(settings.ollama_url)
    sessions = sessions or SessionStore(settings.db_path)
    cache = cache or L1Cache(settings.redis_url)
    knowledge = KnowledgeStore(settings.qdrant_url)
    semantic = SemanticCache(settings, ollama, knowledge)
    if orchestrator is None:
        coder = Coder(ollama, load_role("coder"), settings.keep_alive)
        agents = {"coder": coder}
        for role_name in ("revisor", "docs"):
            try:
                agents[role_name] = Coder(ollama, load_role(role_name), settings.keep_alive)
            except FileNotFoundError:
                pass
        router = Router(ollama, small_models={settings.model_frontal, settings.model_embed})
        orchestrator = Orchestrator(
            settings,
            ollama,
            sessions,
            cache,
            coder,
            router,
            retriever=Retriever(settings, ollama, knowledge),
            semantic=semantic,
            agents=agents,
        )
    auth = make_auth_dep(settings.api_token)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        # Cierre limpio de conexiones en el shutdown (guardas para fakes de tests).
        for closer in (getattr(ollama, "aclose", None), getattr(cache, "close", None)):
            if closer is not None:
                await closer()

    app = FastAPI(title="Hefisty", version="0.1.0", lifespan=lifespan)

    def _sanitize(req: ChatRequest) -> str:
        users = [m for m in req.messages if m.role == "user"]
        if not users:
            raise HTTPException(status_code=400, detail="Falta un mensaje de usuario")
        text = users[-1].content
        if len(text) > settings.max_input_chars:
            raise HTTPException(status_code=413, detail="Entrada demasiado grande")
        flags = detect_injection(text)
        if flags:
            logger.warning("entrada con posible prompt injection (%d patrones)", len(flags))
        return text

    @app.post("/v1/chat/completions", dependencies=[Depends(auth)])
    async def chat(req: ChatRequest):  # noqa: ANN202 - respuesta dinámica (JSON o SSE)
        user_text = _sanitize(req)
        if req.session_id:
            session = await asyncio.to_thread(sessions.get, req.session_id)
            if session is None:
                raise HTTPException(status_code=404, detail="Sesión no encontrada")
            created = False
        else:
            session = await asyncio.to_thread(sessions.create)
            created = True

        if req.stream:

            async def event_gen() -> AsyncIterator[str]:
                errored = False
                try:
                    async for ev in orchestrator.stream_turn(session, user_text):
                        if ev["type"] == "meta":
                            yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                        else:
                            chunk = {"choices": [{"delta": {"content": ev["text"]}}]}
                            yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
                except Exception as exc:
                    errored = True
                    err = {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
                    yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
                finally:
                    if errored and created:
                        # limpia la sesión huérfana creada en esta petición
                        await asyncio.to_thread(sessions.delete, session.id)
                    yield "data: [DONE]\n\n"

            return StreamingResponse(event_gen(), media_type="text/event-stream")

        meta: dict | None = None
        parts: list[str] = []
        try:
            async for ev in orchestrator.stream_turn(session, user_text):
                if ev["type"] == "meta":
                    meta = ev
                else:
                    parts.append(ev["text"])
        except Exception as exc:
            if created:
                await asyncio.to_thread(sessions.delete, session.id)
            raise HTTPException(status_code=503, detail=f"Error del modelo: {exc}") from exc
        content = "".join(parts)
        return JSONResponse(
            {
                "id": session.id,
                "object": "chat.completion",
                "model": meta["model"] if meta else settings.model_frontal,
                "choices": [
                    {
                        "index": 0,
                        "message": {"role": "assistant", "content": content},
                        "finish_reason": "stop",
                    }
                ],
                "hefisty": {
                    "agent": meta["agent"] if meta else "hefisty",
                    "session_id": session.id,
                    "cached": bool(meta["cached"]) if meta else False,
                },
            }
        )

    @app.get("/v1/sessions", dependencies=[Depends(auth)])
    async def list_sessions():  # noqa: ANN202
        items = await asyncio.to_thread(sessions.list)
        return {
            "sessions": [
                {
                    "id": s.id,
                    "title": s.title,
                    "updated_at": s.updated_at,
                    "active_agent": s.active_agent,
                }
                for s in items
            ]
        }

    @app.post("/v1/sessions/{session_id}/resume", dependencies=[Depends(auth)])
    async def resume(session_id: str):  # noqa: ANN202
        s = await asyncio.to_thread(sessions.get, session_id)
        if s is None:
            raise HTTPException(status_code=404, detail="Sesión no encontrada")
        return {
            "id": s.id,
            "title": s.title,
            "active_agent": s.active_agent,
            "messages": s.messages,
            "subtasks": s.subtasks,
        }

    @app.post("/v1/sessions/{session_id}/continue", dependencies=[Depends(auth)])
    async def continue_chain(session_id: str):  # noqa: ANN202
        session = await asyncio.to_thread(sessions.get, session_id)
        if session is None:
            raise HTTPException(status_code=404, detail="Sesión no encontrada")

        async def event_gen() -> AsyncIterator[str]:
            try:
                async for ev in orchestrator.resume_chain(session):
                    if ev["type"] == "meta":
                        yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    else:
                        chunk = {"choices": [{"delta": {"content": ev["text"]}}]}
                        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n"
            except Exception as exc:
                err = {"type": "error", "message": f"{type(exc).__name__}: {exc}"}
                yield f"data: {json.dumps(err, ensure_ascii=False)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(event_gen(), media_type="text/event-stream")

    @app.patch("/v1/sessions/{session_id}", dependencies=[Depends(auth)])
    async def rename(session_id: str, body: RenameRequest):  # noqa: ANN202
        if await asyncio.to_thread(sessions.get, session_id) is None:
            raise HTTPException(status_code=404, detail="Sesión no encontrada")
        await asyncio.to_thread(sessions.rename, session_id, body.title)
        return {"id": session_id, "title": body.title}

    @app.post("/v1/feedback", dependencies=[Depends(auth)])
    async def feedback(body: FeedbackRequest):  # noqa: ANN202
        fid = await asyncio.to_thread(
            sessions.add_feedback,
            session_id=body.session_id,
            turn_index=body.turn_index,
            agent=body.agent,
            model=body.model,
            vote=body.vote,
            comment=body.comment or "",
            cache_key=body.cache_key or "",
            sources=body.sources or [],
        )
        invalidated = False
        if body.vote == "down":
            if body.cache_key:
                await cache.delete_key(body.cache_key)
                invalidated = True
            # Invalida también la entrada de cache semántica del turno (por su query).
            if body.session_id:
                sess = await asyncio.to_thread(sessions.get, body.session_id)
                if sess is not None:
                    users = [m["content"] for m in sess.messages if m["role"] == "user"]
                    q = None
                    if body.turn_index is not None and 0 <= body.turn_index < len(users):
                        q = users[body.turn_index]
                    elif users:
                        q = users[-1]
                    if q is not None:
                        await semantic.delete(q)
                        invalidated = True
        return {"id": fid, "invalidated_cache": invalidated}

    @app.get("/v1/models", dependencies=[Depends(auth)])
    async def models_loaded():  # noqa: ANN202
        running = await ollama.running()
        return {
            "models": [
                {
                    "name": m.get("name", ""),
                    "vram_bytes": m.get("size_vram", 0),
                    "vram": f"{m.get('size_vram', 0) / 1e9:.1f} GB",
                }
                for m in running
            ]
        }

    @app.get("/v1/roles", dependencies=[Depends(auth)])
    async def roles_installed():  # noqa: ANN202
        return {
            "roles": [
                {
                    "name": r.name,
                    "description": r.description,
                    "model": r.model,
                    "collection": r.collection,
                    "tools": r.tools,
                }
                for r in list_roles()
            ]
        }

    @app.get("/health")
    async def health():  # noqa: ANN202
        return {
            "status": "ok",
            "ollama": await ollama.ping(),
            "redis": await cache.ping(),
            "qdrant": await _qdrant_ok(settings.qdrant_url),
            "loaded_models": await ollama.loaded_models(),
        }

    # El front compilado se sirve en '/'. Se monta al final para no tapar la API.
    if settings.web_dist.is_dir():
        app.mount("/", StaticFiles(directory=str(settings.web_dist), html=True), name="web")

    return app


app = create_app()
