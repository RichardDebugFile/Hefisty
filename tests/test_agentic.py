import json

from hefisty.agents.agentic import AgenticCoder
from hefisty.config import Settings
from hefisty.roles import load_role


class ScriptedOllama:
    """Devuelve una secuencia predefinida de mensajes (con/sin tool_calls)."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    async def chat_tools(self, model, messages, tools, *, keep_alive="10m"):
        msg = self._script[self.calls]
        self.calls += 1
        return msg


def _tc(name, args):
    return {"function": {"name": name, "arguments": args}}


async def test_agentic_locates_and_edits_via_tools(tmp_path):
    (tmp_path / "app.py").write_text("def foo():\n    return 1\n", encoding="utf-8")
    script = [
        {"content": "", "tool_calls": [_tc("glob", {"patron": "*.py"})]},
        {"content": "", "tool_calls": [_tc("grep", {"regex": "return 1"})]},
        {
            "content": "",
            "tool_calls": [
                _tc(
                    "edit", {"ruta": "app.py", "texto_viejo": "return 1", "texto_nuevo": "return 2"}
                )
            ],
        },
        {"content": "Cambié return 1 por return 2 en app.py.", "tool_calls": []},
    ]
    agent = AgenticCoder(ScriptedOllama(script), load_role("coder"), tmp_path, Settings())
    events: list[str] = []
    res = await agent.run("haz que foo devuelva 2", events.append)
    assert (tmp_path / "app.py").read_text(encoding="utf-8") == "def foo():\n    return 2\n"
    assert "app.py" in res["touched"]
    assert res["steps"] == 3
    assert "Cambié" in res["answer"]
    assert len(events) == 3


async def test_agentic_stops_at_max_rounds(tmp_path):
    loop_msg = {"content": "", "tool_calls": [_tc("listar_directorio", {})]}
    agent = AgenticCoder(
        ScriptedOllama([loop_msg] * 20), load_role("coder"), tmp_path, Settings(), max_rounds=3
    )
    res = await agent.run("bucle infinito", None)
    assert res["steps"] == 3
    assert "límite" in res["answer"]


async def test_agentic_accepts_string_arguments(tmp_path):
    (tmp_path / "x.txt").write_text("hola", encoding="utf-8")
    script = [
        {
            "content": "",
            "tool_calls": [
                {"function": {"name": "leer_archivo", "arguments": json.dumps({"ruta": "x.txt"})}}
            ],
        },
        {"content": "el archivo dice hola", "tool_calls": []},
    ]
    agent = AgenticCoder(ScriptedOllama(script), load_role("coder"), tmp_path, Settings())
    res = await agent.run("lee x.txt", None)
    assert "hola" in res["answer"]


async def test_agentic_parses_text_tool_call(tmp_path):
    # Modelos/Ollama que emiten la tool call como texto (```json ...```) en vez de tool_calls.
    (tmp_path / "app.py").write_text("x = 1\n", encoding="utf-8")
    script = [
        {
            "content": '```json\n{"name": "glob", "arguments": {"patron": "*.py"}}\n```',
            "tool_calls": [],
        },
        {"content": "Encontré app.py.", "tool_calls": []},
    ]
    agent = AgenticCoder(ScriptedOllama(script), load_role("coder"), tmp_path, Settings())
    events: list[str] = []
    res = await agent.run("lista los .py", events.append)
    assert res["steps"] == 1  # el glob se ejecutó vía fallback de texto
    assert events and "app.py" in events[0]
