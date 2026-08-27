"""Test del export de feedback a datasets de corrección (Fase 4, criterio 5)."""

import json

from hefisty.feedback import export_corrections
from hefisty.orchestrator.sessions import SessionStore


def test_export_corrections_pairs_and_excludes_memories(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    sess = store.create("demo")
    sess.messages = [
        {"role": "user", "content": "haz una función suma"},
        {"role": "assistant", "content": "def suma(a,b): return a+b"},
        {"role": "user", "content": "ahora una resta"},
        {"role": "assistant", "content": "def resta(a,b): return a-b"},
    ]
    store.save(sess)
    # 👍 al turno 0 (coder), 👎 con corrección al turno 1 (coder), y un feedback de otro rol.
    store.add_feedback(session_id=sess.id, turn_index=0, agent="coder", model="m", vote="up")
    store.add_feedback(
        session_id=sess.id,
        turn_index=1,
        agent="coder",
        model="m",
        vote="down",
        comment="faltó validar tipos",
    )
    store.add_feedback(session_id=sess.id, turn_index=0, agent="docs", model="m", vote="up")

    path, n = export_corrections(store, "coder", tmp_path / "out")
    assert n == 2  # solo los del rol coder
    lines = [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]
    by_score = {ln["score"]: ln for ln in lines}
    assert by_score[1]["u"] == "haz una función suma"
    assert by_score[1]["a"].startswith("def suma")
    assert by_score[-1]["comentario"] == "faltó validar tipos"
    # Ningún dato de memoria: el export solo mira la tabla feedback.
    assert all(set(ln.keys()) <= {"u", "a", "score", "comentario"} for ln in lines)


def test_export_empty_for_unknown_role(tmp_path):
    store = SessionStore(tmp_path / "s.db")
    path, n = export_corrections(store, "inexistente", tmp_path / "out")
    assert n == 0
    assert path.read_text(encoding="utf-8") == ""
