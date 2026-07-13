"""Modelos de entrada/salida del gateway (OpenAI-compatible en lo esencial)."""

from __future__ import annotations

from pydantic import BaseModel


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: list[ChatMessage]
    model: str | None = None
    stream: bool = False
    session_id: str | None = None


class RenameRequest(BaseModel):
    title: str


class FeedbackRequest(BaseModel):
    vote: str  # "up" | "down"
    session_id: str | None = None
    turn_index: int | None = None
    agent: str | None = None
    model: str | None = None
    comment: str | None = None
    cache_key: str | None = None
    sources: list | None = None
