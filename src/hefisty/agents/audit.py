"""Traza estructurada (JSONL) de una corrida del Coder agéntico.

Un archivo por run bajo `data_dir/agent_runs/<ts>-<slug>.jsonl`. Registra qué buscó,
qué leyó, qué editó (con diff), cuánto tardó cada tool y por qué paró. Sirve para
auditar/verificar el comportamiento de la IA y, más adelante, como dataset (Fase 5).

Regla de oro: **nunca romper la corrida**. Si el disco/serialización falla, se degrada
a no-op en silencio; auditar no debe tumbar la tarea real.
"""

from __future__ import annotations

import json
import re
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slug(text: str, maxlen: int = 40) -> str:
    s = _SLUG_RE.sub("-", text.lower()).strip("-")
    return (s[:maxlen].rstrip("-")) or "run"


def _trim(value: Any, limit: int) -> Any:
    """Recorta strings largos; deja intactos números/booleanos/None."""
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + f"…(+{len(value) - limit})"
    return value


def _trim_args(args: dict[str, Any], limit: int = 300) -> dict[str, Any]:
    if not isinstance(args, dict):
        return {"_raw": _trim(str(args), limit)}
    return {k: _trim(v, limit) for k, v in args.items()}


class RunRecorder:
    """Escribe eventos como líneas JSON. Instanciar una por corrida."""

    def __init__(
        self,
        audit_dir: Path,
        task: str,
        meta: dict[str, Any] | None = None,
        enabled: bool = True,
    ) -> None:
        self.enabled = enabled
        self.path: Path | None = None
        self._fh: Any = None
        self._t0 = time.monotonic()
        if not enabled:
            return
        try:
            audit_dir.mkdir(parents=True, exist_ok=True)
            ts = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            self.path = audit_dir / f"{ts}-{_slug(task)}.jsonl"
            self._fh = self.path.open("w", encoding="utf-8")
            self._write("run_start", task=task, **(meta or {}))
        except OSError:
            self.enabled = False
            self._fh = None
            self.path = None

    def _write(self, event: str, **fields: Any) -> None:
        if self._fh is None:
            return
        rec = {"dt": round(time.monotonic() - self._t0, 3), "event": event, **fields}
        try:
            self._fh.write(json.dumps(rec, ensure_ascii=False) + "\n")
            self._fh.flush()
        except (OSError, TypeError, ValueError):
            pass

    def assistant(self, step: int, content: str) -> None:
        if content:
            self._write("assistant", step=step, content=_trim(content, 2000))

    def thinking(self, step: int, text: str) -> None:
        """Cadena de razonamiento del modelo (gpt-oss la devuelve aparte del content)."""
        if text:
            self._write("thinking", step=step, text=_trim(text, 2000))

    def tool(
        self, step: int, name: str, args: dict[str, Any], result: str, ok: bool, ms: int
    ) -> None:
        self._write(
            "tool",
            step=step,
            name=name,
            args=_trim_args(args),
            ok=ok,
            ms=ms,
            result=_trim(result, 800),
        )

    def edit(self, ruta: str, texto_viejo: str, texto_nuevo: str) -> None:
        self._write("edit", ruta=ruta, old=_trim(texto_viejo, 500), new=_trim(texto_nuevo, 500))

    def write_file(self, ruta: str, contenido: str) -> None:
        self._write("write_file", ruta=ruta, chars=len(contenido), preview=_trim(contenido, 500))

    def note(self, text: str, **fields: Any) -> None:
        self._write("note", text=text, **fields)

    def run_end(self, answer: str, touched: list[str], steps: int, stop_reason: str) -> None:
        self._write(
            "run_end",
            answer=_trim(answer, 2000),
            touched=touched,
            steps=steps,
            stop_reason=stop_reason,
        )
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
            self._fh = None
