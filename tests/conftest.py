from pathlib import Path

import pytest

from hefisty.agents import audit
from hefisty.config import REPO_ROOT

_REAL_AUDIT_DIR = REPO_ROOT / "data" / "agent_runs"


@pytest.fixture(autouse=True)
def _audit_to_tmp(monkeypatch, tmp_path_factory):
    """Las trazas JSONL de los tests no van al `data/agent_runs/` real (cada corrida de la
    suite dejaba ~10 archivos basura entre las trazas de verdad). Solo se desvía la ruta por
    defecto: los tests que pasan su propio `data_dir` siguen viéndolo donde lo pidieron.
    Carpeta aparte de `tmp_path` porque muchos tests usan `tmp_path` como workspace vacío."""
    orig = audit.RunRecorder.__init__
    sink = tmp_path_factory.mktemp("agent_runs")

    def patched(self, audit_dir, *args, **kwargs):
        if Path(audit_dir) == _REAL_AUDIT_DIR:
            audit_dir = sink
        orig(self, audit_dir, *args, **kwargs)

    monkeypatch.setattr(audit.RunRecorder, "__init__", patched)
