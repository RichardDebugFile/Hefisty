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

    async def chat_tools(
        self, model, messages, tools, *, keep_alive="10m", options=None, think=None
    ):
        # Al agotar el guion devuelve un cierre vacío (cubre el pase de auto-revisión).
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
        self.last_messages = []

    async def chat_tools(
        self, model, messages, tools, *, keep_alive="10m", options=None, think=None
    ):
        if self.calls == 0:
            self.first_messages = [dict(m) for m in messages]
        self.last_messages = [dict(m) for m in messages]
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


async def test_agentic_unknown_tool_lists_real_tools(tmp_path):
    # gpt-oss a veces inventa tools (repo_browser.search): el error debe reencauzarlo.
    agent = AgenticCoder(ScriptedOllama([]), load_role("coder"), tmp_path, Settings())
    msg = await agent._exec("repo_browser.search", {"query": "x"})
    assert "desconocida" in msg
    assert "glob" in msg and "grep" in msg
    assert "search_code" not in msg  # sin índice del repo no se ofrece


def _hit(source="a/B.kt", text="fun x() {}"):
    return SimpleNamespace(source=source, section="", text=text, score=0.8)


class ToolsCapturingOllama(ScriptedOllama):
    """Guarda el set de tools ofrecido en cada llamada."""

    def __init__(self, script):
        super().__init__(script)
        self.tool_sets: list[list[str]] = []
        self.descs: list[dict[str, str]] = []

    async def chat_tools(
        self, model, messages, tools, *, keep_alive="10m", options=None, think=None
    ):
        self.tool_sets.append([t["function"]["name"] for t in tools])
        self.descs.append({t["function"]["name"]: t["function"]["description"] for t in tools})
        return await super().chat_tools(model, messages, tools, keep_alive=keep_alive, think=think)


async def test_search_code_not_offered_without_index(tmp_path):
    # Ofrecer una tool que siempre falla gasta rondas: sin retriever/índice no entra al set.
    ollama = ToolsCapturingOllama([{"content": "listo", "tool_calls": []}])
    agent = AgenticCoder(ollama, load_role("coder"), tmp_path, Settings())
    await agent.run("tarea", None)
    assert "search_code" not in ollama.tool_sets[0]
    assert "grep" in ollama.tool_sets[0]


async def test_search_code_has_quota_and_disappears(tmp_path):
    # Cupo de 2: la descripción dice cuántos usos quedan; al agotarlo la tool sale del set y
    # una llamada extra (p. ej. vía texto) devuelve ERROR reencauzando a grep.
    sc = _tc("search_code", {"consulta": "porcentaje"})
    script = [
        {"content": "", "tool_calls": [sc]},
        {"content": "", "tool_calls": [sc]},
        {"content": "", "tool_calls": [sc]},  # ya sin cupo
        {"content": "fin", "tool_calls": []},
    ]
    ollama = ToolsCapturingOllama(script)
    settings = Settings(coder_search_code_max=2)
    agent = AgenticCoder(
        ollama,
        load_role("coder"),
        tmp_path,
        settings,
        retriever=FakeRetriever([_hit()]),
        repo_collection="repo__x",
    )
    await agent.run("arregla el porcentaje", None)
    assert "search_code" in ollama.tool_sets[0]
    assert "search_code" in ollama.tool_sets[1]
    assert "search_code" not in ollama.tool_sets[2]  # agotado → desaparece
    # La descripción lleva el contador decreciente.
    assert "quedan 2 usos" in ollama.descs[0]["search_code"]
    assert "quedan 1 usos" in ollama.descs[1]["search_code"]
    # La tercera llamada, ya sin cupo, NO gasta la ronda en un ERROR: se ejecuta como grep.
    (tmp_path / "V.kt").write_text("fun getResumenInfo() {}\n", encoding="utf-8")
    third = await agent._exec("search_code", {"consulta": "getResumenInfo"})
    assert third.startswith("search_code cupo agotado: ejecuté grep")
    assert "V.kt:1:" in third
    # Consulta de varias palabras → regex con \s+; sin hits lo dice y reencauza.
    none = await agent._exec("search_code", {"consulta": "approval log"})
    assert "no dio resultados" in none and "grep" in none


async def test_search_code_result_tells_remaining_uses(tmp_path):
    agent = AgenticCoder(
        ScriptedOllama([]),
        load_role("coder"),
        tmp_path,
        Settings(coder_search_code_max=3),
        retriever=FakeRetriever([_hit()]),
        repo_collection="repo__x",
    )
    out = await agent._exec("search_code", {"consulta": "x"})
    assert "a/B.kt" in out
    assert "quedan 2 usos" in out


async def test_grep_accepts_patron_alias(tmp_path):
    # El modelo confunde `patron` (glob) con `regex` (grep): 2 rondas perdidas en la eval §2.
    (tmp_path / "a.kt").write_text("val x = indexOfFirst()\n", encoding="utf-8")
    agent = AgenticCoder(ScriptedOllama([]), load_role("coder"), tmp_path, Settings())
    out = await agent._exec("grep", {"patron": "indexOfFirst", "contexto": 0})
    assert "a.kt:1" in out
    out2 = await agent._exec("grep", {"regex": "indexOfFirst", "ruta": ""})  # ruta vacía = raíz
    assert "a.kt:1" in out2


async def test_dict_context_is_audited(tmp_path):
    # La traza debe decir QUÉ chunks del diccionario entraron (con colección y score): es la
    # única forma de distinguir "no sabía" de "sabía y no lo aplicó".
    hit = SimpleNamespace(
        source="10-formas-de-bug.md",
        section="orden por texto",
        text="Ordenar por string formateado es lexicográfico.",
        score=0.71,
        collection="patrones",
    )
    settings = Settings(data_dir=tmp_path / "data", audit_enabled=True)
    ollama = CapturingOllama([{"content": "listo", "tool_calls": []}])
    agent = AgenticCoder(
        ollama, load_role("coder"), tmp_path / "ws", settings, retriever=FakeRetriever([hit])
    )
    (tmp_path / "ws").mkdir()
    res = await agent.run("la lista sale desordenada")
    lines = [json.loads(ln) for ln in open(res["audit"], encoding="utf-8")]
    notes = [ln for ln in lines if ln["event"] == "note" and ln["text"] == "dict_context"]
    assert len(notes) == 1
    assert notes[0]["collections"] == ["patrones"]
    assert notes[0]["hits"] == [
        {
            "source": "10-formas-de-bug.md",
            "section": "orden por texto",
            "collection": "patrones",
            "score": 0.71,
            "preview": "Ordenar por string formateado es lexicográfico.",
        }
    ]


async def test_invented_open_tool_maps_to_leer_archivo(tmp_path):
    (tmp_path / "a.kt").write_text("fun a() {}\n", encoding="utf-8")
    agent = AgenticCoder(ScriptedOllama([]), load_role("coder"), tmp_path, Settings())
    assert await agent._exec("open", {"ruta": "a.kt"}) == "fun a() {}\n"
    assert "desconocida" in await agent._exec("open", {"query": "x"})  # sin ruta: no es lectura


async def test_leer_archivo_big_file_shows_head_and_outline(tmp_path):
    # Eval §6 v4: leer_archivo de 646 líneas se truncaba a ~150 y el modelo creía haberlo
    # leído entero; la función causante (l. 184) nunca apareció. Ahora: cabecera + esqueleto.
    body = "\n".join(f"val v{i} = {i}" for i in range(800)) + "\nfun normalizeItems() {}\n"
    (tmp_path / "Big.kt").write_text("package p\n" + body, encoding="utf-8")  # >6000 chars
    agent = AgenticCoder(ScriptedOllama([]), load_role("coder"), tmp_path, Settings())
    out = await agent._exec("leer_archivo", {"ruta": "Big.kt"})
    assert out.startswith("1: package p\n2: val v0 = 0\n")
    assert "archivo de 803 líneas: se muestran las 60 primeras" in out
    assert out.rstrip().endswith("802: fun normalizeItems() {}")
    assert "val v799" not in out


async def test_search_code_zero_quota_never_offered(tmp_path):
    ollama = ToolsCapturingOllama([{"content": "listo", "tool_calls": []}])
    agent = AgenticCoder(
        ollama,
        load_role("coder"),
        tmp_path,
        Settings(coder_search_code_max=0),
        retriever=FakeRetriever([_hit()]),
        repo_collection="repo__x",
    )
    await agent.run("tarea", None)
    assert "search_code" not in ollama.tool_sets[0]


class RecordingOllama(ScriptedOllama):
    """Captura los mensajes de la 2ª llamada (tras el resultado de la tool)."""

    def __init__(self, script):
        super().__init__(script)
        self.second_msgs = None

    async def chat_tools(
        self, model, messages, tools, *, keep_alive="10m", options=None, think=None
    ):
        if self.calls == 1:
            self.second_msgs = [dict(m) for m in messages]
        return await super().chat_tools(model, messages, tools, keep_alive=keep_alive, think=think)


async def test_agentic_truncates_huge_tool_result(tmp_path):
    # Un archivo enorme no debe inflar el contexto (provocaba OOM/500 con VRAM justa).
    (tmp_path / "big.txt").write_text("x" * 20000, encoding="utf-8")
    script = [
        {"content": "", "tool_calls": [_tc("leer_archivo", {"ruta": "big.txt"})]},
        {"content": "leído", "tool_calls": []},
    ]
    ollama = RecordingOllama(script)
    agent = AgenticCoder(ollama, load_role("coder"), tmp_path, Settings())
    await agent.run("lee big.txt", None)
    tool_msgs = [m for m in ollama.second_msgs if m["role"] == "tool"]
    assert tool_msgs and len(tool_msgs[0]["content"]) < 20000
    assert "truncado" in tool_msgs[0]["content"]


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


async def test_agentic_outline_tool(tmp_path):
    (tmp_path / "V.kt").write_text(
        "class V {\n    fun onApprove() {}\n    val x = 1\n}\n", encoding="utf-8"
    )
    agent = AgenticCoder(ScriptedOllama([]), load_role("coder"), tmp_path, Settings())
    out = await agent._exec("outline", {"ruta": "V.kt"})
    assert "class V" in out and "fun onApprove" in out and "val x" not in out


async def test_agentic_injects_dir_map_for_large_repo(tmp_path):
    # >200 archivos → se inyecta el MAPA de carpetas (carpeta + nº), no la lista plana.
    pkg = tmp_path / "app" / "pedidos" / "detalle"
    pkg.mkdir(parents=True)
    for i in range(220):
        (pkg / f"F{i}.kt").write_text("class F {}", encoding="utf-8")
    ollama = CapturingOllama([{"content": "listo", "tool_calls": []}])
    agent = AgenticCoder(ollama, load_role("coder"), tmp_path, Settings())
    await agent.run("arregla algo")
    system_texts = [m["content"] for m in ollama.first_messages if m["role"] == "system"]
    joined = "\n".join(system_texts)
    assert "app/pedidos/detalle/ (220)" in joined  # cadena de un solo hijo = un nodo
    assert "F0.kt" not in joined  # no vuelca los 220 archivos uno por uno


class OrientingOllama(CapturingOllama):
    """Además de chat_tools, responde a la ronda de orientación (chat sin tools)."""

    def __init__(self, script, orientation):
        super().__init__(script)
        self.orientation = orientation
        self.orientation_prompt = None

    async def chat(self, model, messages, *, keep_alive="10m", fmt=None, options=None):
        self.orientation_prompt = messages[-1]["content"]
        return self.orientation


async def test_orientation_fixes_module_focus_for_large_repo(tmp_path):
    # Repo grande (mapa): antes del bucle, el modelo dice en qué carpeta vive el módulo de la
    # tarea; se validan las rutas y se fijan como FOCO en un mensaje de sistema.
    pkg = tmp_path / "app" / "pedidos" / "detalle"
    pkg.mkdir(parents=True)
    for i in range(220):
        (pkg / f"F{i}.kt").write_text("class F {}", encoding="utf-8")
    (tmp_path / "app" / "pagos").mkdir()
    ollama = OrientingOllama(
        [{"content": "listo", "tool_calls": []}],
        orientation="- app/pedidos/detalle/ (220)\napp/no/existe\n`app/pagos`\n",
    )
    agent = AgenticCoder(ollama, load_role("coder"), tmp_path, Settings())
    res = await agent.run("en Pedidos, pestaña Detalle, el filtro no funciona")
    assert "pestaña Detalle" in ollama.orientation_prompt  # la tarea va en la orientación
    assert "app/pedidos/detalle/ (220)" in ollama.orientation_prompt  # y el mapa
    focus = [m for m in ollama.first_messages if m["role"] == "system" and "FOCO" in m["content"]]
    assert len(focus) == 1
    assert "app/pedidos/detalle\napp/pagos\n" in focus[0]["content"]  # validadas y limpias
    assert "no/existe" not in focus[0]["content"]
    notes = [json.loads(ln) for ln in open(res["audit"], encoding="utf-8")]
    foco_note = next(n for n in notes if n["event"] == "note" and n["text"] == "foco")
    assert foco_note["carpetas"] == ["app/pedidos/detalle", "app/pagos"]


async def test_orientation_skipped_for_small_repo_and_never_breaks(tmp_path):
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    ollama = CapturingOllama([{"content": "listo", "tool_calls": []}])  # sin método `chat`
    agent = AgenticCoder(ollama, load_role("coder"), tmp_path, Settings())
    await agent.run("arregla a.py")  # repo chico: no hay orientación, no llama a chat
    assert not any("FOCO" in m["content"] for m in ollama.first_messages)


def test_dir_map_expands_biggest_subtrees_first(tmp_path):
    # Eval §6 (13/09/2026): el mapa alfabético con tope de chars mostraba 75 carpetas, TODAS de
    # `cuentas/`; `pedidos/` y 500 más no existían para el modelo. Ahora se despliegan
    # primero las carpetas más grandes: el código fuente llega hasta los módulos y las
    # auxiliares (`.github/`) quedan plegadas con ›.
    from hefisty.agents import agentic

    files = [f".github/workflows/w{i}.yml" for i in range(3)]
    files += [
        f"app/src/main/java/com/x/cuentas/sub{i}/F{j}.kt" for i in range(30) for j in range(5)
    ]
    files += [f"app/src/main/java/com/x/pedidos/viewmodel/detalle/G{j}.kt" for j in range(4)]
    files += [f"app/src/main/java/com/x/pedidos/commons/H{j}.kt" for j in range(6)]
    files += [f"app/src/main/java/com/x/pagos/detalle/M{j}.kt" for j in range(8)]
    orig = agentic._MAX_MAP
    agentic._MAX_MAP = 20  # presupuesto chico para forzar la elección
    try:
        m = AgenticCoder._dir_map(None, sorted(files))
    finally:
        agentic._MAX_MAP = orig
    lines = m.splitlines()
    assert ".github/workflows/ (3)" in lines  # cadena de un hijo → un nodo hoja
    assert any(ln.startswith("app/src/main/java/com/x/ (") for ln in lines)  # cadena colapsada
    # Los tres módulos son visibles aunque cuentas/ tenga 30 subcarpetas.
    assert any("pedidos/ (10)" in ln for ln in lines)
    assert any("pagos/detalle/ (8)" in ln for ln in lines)
    assert any("cuentas/ (150)" in ln for ln in lines)
    assert "subcarpetas más" in lines[-1]


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


async def test_nudges_reanchor_the_task(tmp_path):
    # Si el contexto se trunca, el enunciado se pierde y el nudge pasaría a ser "la petición"
    # (el modelo llegó a responder "no se recibieron instrucciones"). Todo nudge lo re-ancla.
    (tmp_path / "a.py").write_text("x = 1\n", encoding="utf-8")
    tarea = "arregla el calculo del porcentaje"
    script = [{"content": "listo sin editar", "tool_calls": []}]  # dispara el nudge de revisión
    ollama = CapturingOllama(script)
    agent = AgenticCoder(ollama, load_role("coder"), tmp_path, Settings())
    await agent.run(tarea, None)
    nudges = [
        m for m in ollama.last_messages if m["role"] == "user" and "TAREA ORIGINAL" in m["content"]
    ]
    assert nudges, "el nudge debe re-anclar la tarea"
    assert tarea in nudges[0]["content"]
