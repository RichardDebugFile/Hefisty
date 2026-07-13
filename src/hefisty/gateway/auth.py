"""Autenticación Bearer del gateway.

El token se lee de la config (nunca hardcodeado). Si está vacío, la auth queda
desactivada: pensado solo para dev local, ya que el gateway escucha en 127.0.0.1.
"""

from __future__ import annotations

import hmac
from collections.abc import Callable, Coroutine
from typing import Any

from fastapi import Header, HTTPException


def make_auth_dep(token: str) -> Callable[[str | None], Coroutine[Any, Any, None]]:
    expected = f"Bearer {token}"

    async def dep(authorization: str | None = Header(default=None)) -> None:
        if not token:
            return  # auth desactivada (dev local)
        # Comparación en tiempo constante para no filtrar el token por timing.
        if not hmac.compare_digest(authorization or "", expected):
            raise HTTPException(status_code=401, detail="Token inválido o ausente")

    return dep
