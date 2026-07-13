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
