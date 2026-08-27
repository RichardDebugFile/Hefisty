import json
from types import SimpleNamespace

from hefisty.agents.agentic import AgenticCoder
from hefisty.config import Settings
from hefisty.roles import load_role


class ScriptedOllama:
    """Devuelve una secuencia predefinida de mensajes (con/sin tool_calls)."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    async def chat_tools(self, model, messages, tools, *, keep_alive="10m"):
        # Al agotar el guion devuelve un cierre vacío (cubre el pase de auto-revisión).
        msg = self._script[self.calls] if self.calls < len(self._script) else {
            "content": "",
            "tool_calls": [],
        }
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


class FakeRetriever:
    def __init__(self, hits):
        self._hits = hits
        self.collections = None

    async def retrieve(self, query, collections):
        self.collections = collections
        return self._hits


class CapturingOllama:
    """Registra los mensajes de la primera llamada para inspeccionar el contexto inyectado."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0
        self.first_messages = None

    async def chat_tools(self, model, messages, tools, *, keep_alive="10m"):
        if self.calls == 0:
            self.first_messages = [dict(m) for m in messages]
        msg = self._script[self.calls] if self.calls < len(self._script) else {
            "content": "",
            "tool_calls": [],
        }
        self.calls += 1
        return msg


async def test_agentic_injects_dictionary_context(tmp_path):
    # El Coder que EDITA debe recibir los chunks del diccionario como contexto de sistema.
    hit = SimpleNamespace(
        source="react-a11y.md",
        section="useId",
        text="Para identificadores únicos por instancia en React usa useId().",
        score=0.9,
    )
    retriever = FakeRetriever([hit])
    ollama = CapturingOllama([{"content": "listo", "tool_calls": []}])
    agent = AgenticCoder(ollama, load_role("coder"), tmp_path, Settings(), retriever=retriever)
    await agent.run("arregla el formulario accesible en React con ARIA")
    system_texts = [m["content"] for m in ollama.first_messages if m["role"] == "system"]
    assert any("useId" in t and "react-a11y.md" in t for t in system_texts)
    assert retriever.collections == ["patrones"]  # sin lenguaje detectado → solo el comodín


async def test_agentic_uses_extra_collections(tmp_path):
    # Con extra_collections configuradas (diccionario de proyecto), el Coder las consulta.
    hit = SimpleNamespace(source="proyecto.md", section="x", text="dato del proyecto", score=0.9)
    retriever = FakeRetriever([hit])
    ollama = CapturingOllama([{"content": "listo", "tool_calls": []}])
    settings = Settings(extra_collections=["proyecto"])
    agent = AgenticCoder(ollama, load_role("coder"), tmp_path, settings, retriever=retriever)
    await agent.run("arregla algo en kotlin con compose")  # lang=kotlin
    assert retriever.collections == ["kotlin", "proyecto", "patrones"]


async def test_agentic_without_retriever_has_no_dictionary_context(tmp_path):
    ollama = CapturingOllama([{"content": "listo", "tool_calls": []}])
    agent = AgenticCoder(ollama, load_role("coder"), tmp_path, Settings())  # retriever=None
    await agent.run("cualquier tarea")  # workspace vacío -> tampoco árbol
    system_msgs = [m for m in ollama.first_messages if m["role"] == "system"]
    assert len(system_msgs) == 1  # solo el system prompt del rol, sin diccionario ni árbol


async def test_agentic_self_review_gives_second_chance(tmp_path):
    # El modelo dice "listo" sin editar; el pase de revisión lo empuja a aplicar el cambio.
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    script = [
        {"content": "Listo (pero no edité).", "tool_calls": []},  # cierre prematuro
        {
            "content": "",
            "tool_calls": [
                _tc("edit", {"ruta": "a.py", "texto_viejo": "x = 1", "texto_nuevo": "x = 2"})
            ],
        },
        {"content": "Ahora sí, cambié x a 2.", "tool_calls": []},
    ]
    agent = AgenticCoder(ScriptedOllama(script), load_role("coder"), tmp_path, Settings())
    res = await agent.run("cambia x a 2", None)
    assert (tmp_path / "a.py").read_text(encoding="utf-8") == "x = 2\n"
    assert res["steps"] == 1  # la edición ocurrió tras el nudge
    assert "cambié x a 2" in res["answer"].lower()


async def test_agentic_tolerates_malformed_tool_args(tmp_path):
    # El modelo emite un `edit` sin `texto_nuevo`: no debe romper el bucle, sino devolver
    # un error de herramienta y seguir.
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    script = [
        {"content": "", "tool_calls": [_tc("edit", {"ruta": "a.py", "texto_viejo": "x = 1"})]},
        {"content": "corregido", "tool_calls": []},
    ]
    agent = AgenticCoder(ScriptedOllama(script), load_role("coder"), tmp_path, Settings())
    res = await agent.run("edita a.py", None)  # no debe lanzar
    assert res["steps"] == 1
    assert res["answer"] == "corregido"


async def test_agentic_injects_workspace_tree(tmp_path):
    # Archivos anidados: el Coder debe recibir el árbol y no navegar carpeta por carpeta.
    nested = tmp_path / "src" / "com" / "forja" / "pedidos"
    nested.mkdir(parents=True)
    (nested / "Servicio.java").write_text("class X {}", encoding="utf-8")
    ollama = CapturingOllama([{"content": "listo", "tool_calls": []}])
    agent = AgenticCoder(ollama, load_role("coder"), tmp_path, Settings())
    await agent.run("arregla el servicio")
    system_texts = [m["content"] for m in ollama.first_messages if m["role"] == "system"]
    assert any("src/com/forja/pedidos/Servicio.java" in t for t in system_texts)
