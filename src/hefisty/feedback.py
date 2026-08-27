"""Export de feedback a datasets de corrección por rol (Fase 4, insumo de Fase 5).

Convierte la tabla `feedback` en pares `{"u", "a", "score"}` (JSONL) por rol: 👍 → +1,
👎 → -1 (con la corrección en `comentario` si la hay). Los datasets van a `data/`
(gitignorado) porque contienen código/datos del usuario. Los RECUERDOS nunca entran aquí:
esto solo lee la tabla `feedback`, jamás `memories`.
"""

from __future__ import annotations

import json
from pathlib import Path

from .orchestrator.sessions import SessionStore


def export_corrections(store: SessionStore, role: str, out_dir: Path) -> tuple[Path, int]:
    """Escribe `<out_dir>/<role>.jsonl` con los pares de feedback de ese rol. Devuelve
    la ruta y cuántos pares se escribieron."""
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{role}.jsonl"
    sessions: dict[str, object] = {}
    n = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in store.list_feedback():
            if row.get("agent") != role:
                continue
            sid = row.get("session_id")
            ti = row.get("turn_index")
            if not sid or ti is None:
                continue
            if sid not in sessions:
                sessions[sid] = store.get(sid)
            sess = sessions[sid]
            if sess is None:
                continue
            users = [m["content"] for m in sess.messages if m["role"] == "user"]
            assistants = [m["content"] for m in sess.messages if m["role"] == "assistant"]
            if not (0 <= ti < len(users) and ti < len(assistants)):
                continue
            entry = {
                "u": users[ti],
                "a": assistants[ti],
                "score": 1 if row.get("vote") == "up" else -1,
            }
            if row.get("comment"):
                entry["comentario"] = row["comment"]
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            n += 1
    return path, n
