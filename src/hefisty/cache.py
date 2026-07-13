"""Cache L1 exacta sobre Redis.

Hash SHA-256 de la petición normalizada (mensajes + modelo). Resiliente: si Redis
no está disponible, se comporta como cache vacía en vez de romper la petición.
"""

from __future__ import annotations

import hashlib
import json

import redis.asyncio as redis
from redis.exceptions import RedisError

Message = dict[str, str]


class L1Cache:
    def __init__(self, url: str) -> None:
        self._r = redis.from_url(url, decode_responses=True)

    @staticmethod
    def _key(messages: list[Message], model: str) -> str:
        norm = json.dumps({"m": model, "msgs": messages}, ensure_ascii=False, sort_keys=True)
        digest = hashlib.sha256(norm.encode("utf-8")).hexdigest()
        return f"hefisty:l1:{digest}"

    async def get(self, messages: list[Message], model: str) -> str | None:
        try:
            return await self._r.get(self._key(messages, model))
        except RedisError:
            return None

    async def set(self, messages: list[Message], model: str, value: str, ttl: int) -> None:
        try:
            await self._r.set(self._key(messages, model), value, ex=ttl)
        except RedisError:
            pass

    async def ping(self) -> bool:
        try:
            return bool(await self._r.ping())
        except RedisError:
            return False

    async def close(self) -> None:
        try:
            await self._r.aclose()
        except RedisError:
            pass
