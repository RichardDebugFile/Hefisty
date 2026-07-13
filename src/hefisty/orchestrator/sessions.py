"""Persistencia de sesiones en SQLite.

Cada conversación es una sesión con id, título, historial (comprimido con gzip),
agente activo y estado de tarea (archivos tocados, subtareas). Permite crear,
listar y retomar donde se quedó.
"""

from __future__ import annotations

import gzip
import json
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

Message = dict[str, str]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id           TEXT PRIMARY KEY,
    title        TEXT NOT NULL,
    created_at   TEXT NOT NULL,
    updated_at   TEXT NOT NULL,
    active_agent TEXT NOT NULL DEFAULT 'hefisty',
    history      BLOB NOT NULL,   -- JSON gzip: lista de mensajes
    state        TEXT NOT NULL    -- JSON: {touched_files, subtasks}
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


@dataclass
class Session:
    id: str
    title: str
    created_at: str
    updated_at: str
    active_agent: str = "hefisty"
    messages: list[Message] = field(default_factory=list)
    touched_files: list[str] = field(default_factory=list)
    subtasks: list[dict] = field(default_factory=list)


class SessionStore:
    """Almacén sincrónico (sqlite3 stdlib). Los llamadores async lo invocan vía
    `asyncio.to_thread` para no bloquear el event loop."""

    def __init__(self, db_path: Path) -> None:
        self._path = Path(db_path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as con:
            con.executescript(_SCHEMA)

    def _connect(self) -> sqlite3.Connection:
        con = sqlite3.connect(self._path)
        con.row_factory = sqlite3.Row
        return con

    @staticmethod
    def _pack(messages: list[Message]) -> bytes:
        return gzip.compress(json.dumps(messages, ensure_ascii=False).encode("utf-8"))

    @staticmethod
    def _unpack(blob: bytes | None) -> list[Message]:
        if not blob:
            return []
        return json.loads(gzip.decompress(blob).decode("utf-8"))

    def create(self, title: str = "Nueva sesión") -> Session:
        now = _now()
        s = Session(id=uuid.uuid4().hex, title=title, created_at=now, updated_at=now)
        with self._connect() as con:
            con.execute(
                "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    s.id,
                    s.title,
                    s.created_at,
                    s.updated_at,
                    s.active_agent,
                    self._pack(s.messages),
                    json.dumps({"touched_files": [], "subtasks": []}),
                ),
            )
        return s

    def get(self, session_id: str) -> Session | None:
        with self._connect() as con:
            row = con.execute("SELECT * FROM sessions WHERE id = ?", (session_id,)).fetchone()
        if row is None:
            return None
        state = json.loads(row["state"])
        return Session(
            id=row["id"],
            title=row["title"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            active_agent=row["active_agent"],
            messages=self._unpack(row["history"]),
            touched_files=state.get("touched_files", []),
            subtasks=state.get("subtasks", []),
        )

    def list(self) -> list[Session]:
        """Metadatos de todas las sesiones (sin cargar el historial completo)."""
        with self._connect() as con:
            rows = con.execute(
                "SELECT id, title, created_at, updated_at, active_agent "
                "FROM sessions ORDER BY updated_at DESC"
            ).fetchall()
        return [
            Session(
                id=r["id"],
                title=r["title"],
                created_at=r["created_at"],
                updated_at=r["updated_at"],
                active_agent=r["active_agent"],
            )
            for r in rows
        ]

    def save(self, s: Session) -> None:
        s.updated_at = _now()
        with self._connect() as con:
            con.execute(
                "UPDATE sessions SET title=?, updated_at=?, active_agent=?, history=?, state=? "
                "WHERE id=?",
                (
                    s.title,
                    s.updated_at,
                    s.active_agent,
                    self._pack(s.messages),
                    json.dumps({"touched_files": s.touched_files, "subtasks": s.subtasks}),
                    s.id,
                ),
            )

    def rename(self, session_id: str, title: str) -> None:
        with self._connect() as con:
            con.execute(
                "UPDATE sessions SET title=?, updated_at=? WHERE id=?",
                (title, _now(), session_id),
            )
