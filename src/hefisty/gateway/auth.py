"""Autenticación Bearer del gateway.

El token se lee de la config (nunca hardcodeado). Si está vacío, la auth queda
desactivada: pensado solo para dev local, ya que el gateway escucha en 127.0.0.1.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Header, HTTPException


def make_auth_dep(token: str) -> Callable[[str | None], Coroutine[Any, Any, None]]:
    async def dep(authorization: str | None = Header(default=None)) -> None:
        if not token:
            return  # auth desactivada (dev local)
        if authorization != f"Bearer {token}":
            raise HTTPException(status_code=401, detail="Token inválido o ausente")

    return dep
