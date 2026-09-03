import json
from pathlib import Path

from hefisty.agents.agentic import AgenticCoder
from hefisty.agents.audit import RunRecorder
from hefisty.config import Settings
from hefisty.roles import load_role


def _read_events(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def test_recorder_writes_events(tmp_path):
    rec = RunRecorder(tmp_path / "runs", "arreglar el loader", meta={"model": "m"})
    assert rec.path is not None and rec.path.exists()
    rec.tool(1, "grep", {"regex": "x"}, "app.kt:1: x", ok=True, ms=12)
    rec.edit("app.kt", "return 1", "return 2")
    rec.run_end("listo", ["app.kt"], steps=1, stop_reason="completed")

    events = _read_events(rec.path)
    kinds = [e["event"] for e in events]
    assert kinds == ["run_start", "tool", "edit", "run_end"]
    assert events[0]["task"] == "arreglar el loader" and events[0]["model"] == "m"
    assert events[1]["ok"] is True and events[1]["ms"] == 12
    assert events[2]["old"] == "return 1" and events[2]["new"] == "return 2"
    assert events[3]["stop_reason"] == "completed" and events[3]["touched"] == ["app.kt"]


def test_recorder_disabled_is_noop(tmp_path):
    rec = RunRecorder(tmp_path / "runs", "x", enabled=False)
    assert rec.path is None
    rec.run_end("a", [], 0, "completed")  # no debe lanzar ni crear archivos
    assert not (tmp_path / "runs").exists()


def test_recorder_truncates_long_strings(tmp_path):
    rec = RunRecorder(tmp_path / "runs", "t")
    rec.tool(1, "leer_archivo", {"ruta": "big"}, "z" * 5000, ok=True, ms=1)
    rec.run_end("", [], 1, "completed")
    tool_ev = next(e for e in _read_events(rec.path) if e["event"] == "tool")
    assert len(tool_ev["result"]) < 5000 and "(+" in tool_ev["result"]


class _ScriptedOllama:
    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    async def chat_tools(
        self, model, messages, tools, *, keep_alive="10m", options=None, think=None
    ):
        msg = (
            self._script[self.calls]
            if self.calls < len(self._script)
            else {
                "content": "",
                "tool_calls": [],
            }
        )
        self.calls += 1
        return msg


def _tc(name, args):
    return {"function": {"name": name, "arguments": args}}


async def test_run_produces_trace_with_edit_and_stop_reason(tmp_path):
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    script = [
        {
            "content": "",
            "tool_calls": [
                _tc("edit", {"ruta": "app.py", "texto_viejo": "x = 1", "texto_nuevo": "x = 2"})
            ],
        },
        {"content": "cambié x a 2", "tool_calls": []},
    ]
    settings = Settings(data_dir=tmp_path, audit_enabled=True)
    agent = AgenticCoder(_ScriptedOllama(script), load_role("coder"), tmp_path, settings)
    res = await agent.run("cambia x a 2", None)

    assert res["audit"] is not None
    events = _read_events(Path(res["audit"]))
    kinds = [e["event"] for e in events]
    assert kinds[0] == "run_start" and kinds[-1] == "run_end"
    assert "edit" in kinds and "tool" in kinds
    end = events[-1]
    assert end["stop_reason"] == "completed" and end["touched"] == ["app.py"]
    edit_ev = next(e for e in events if e["event"] == "edit")
    assert edit_ev["old"] == "x = 1" and edit_ev["new"] == "x = 2"


async def test_run_trace_records_max_rounds(tmp_path):
    loop = {"content": "", "tool_calls": [_tc("listar_directorio", {})]}
    settings = Settings(data_dir=tmp_path, audit_enabled=True)
    agent = AgenticCoder(
        _ScriptedOllama([loop] * 10), load_role("coder"), tmp_path, settings, max_rounds=2
    )
    res = await agent.run("bucle", None)
    events = _read_events(Path(res["audit"]))
    assert events[-1]["stop_reason"] == "max_rounds"


async def test_run_without_audit_has_no_trace(tmp_path):
    settings = Settings(data_dir=tmp_path, audit_enabled=False)
    agent = AgenticCoder(_ScriptedOllama([]), load_role("coder"), tmp_path, settings)
    res = await agent.run("nada", None)
    assert res["audit"] is None
    assert not (tmp_path / "agent_runs").exists()
