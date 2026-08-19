"""CLI de Hefisty (Typer). Es un cliente HTTP del gateway; requiere el gateway arriba."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import typer

from .config import get_settings

app = typer.Typer(add_completion=False, help="Hefisty: sistema de IA local orquestada.")


def _api() -> tuple[str, dict[str, str]]:
    s = get_settings()
    base = f"http://{s.host}:{s.port}"  # NOSONAR: gateway local en loopback (127.0.0.1)
    headers = {"Authorization": f"Bearer {s.api_token}"} if s.api_token else {}
    return base, headers


@app.command()
def ask(
    prompt: str = typer.Argument(..., help="Lo que quieres pedirle a Hefisty."),
    session: str | None = typer.Option(None, "--session", "-s", help="ID de sesión a continuar."),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Muestra el retrieval (fuentes)."),
) -> None:
    """Envía una petición y muestra la respuesta en streaming."""
    base, headers = _api()
    body: dict = {"messages": [{"role": "user", "content": prompt}], "stream": True}
    if session:
        body["session_id"] = session
    try:
        with httpx.stream(
            "POST", f"{base}/v1/chat/completions", json=body, headers=headers, timeout=300
        ) as r:
            r.raise_for_status()
            for line in r.iter_lines():
                if not line.startswith("data: "):
                    continue
                payload = line[6:]
                if payload == "[DONE]":
                    break
                data = json.loads(payload)
                if data.get("type") == "meta":
                    tag = f"[{data['agent']} · {data['model']}]"
                    if data.get("cached"):
                        tag += " (cache)"
                    typer.secho(tag, fg=typer.colors.CYAN, err=True)
                    if verbose:
                        for s in data.get("sources", []):
                            typer.secho(
                                f"  ↳ {s['source']} ({s['section']}) score={s['score']}",
                                fg=typer.colors.BLUE,
                                err=True,
                            )
                elif data.get("type") == "error":
                    typer.secho(f"\n[error] {data.get('message')}", fg=typer.colors.RED, err=True)
                else:
                    typer.echo(data["choices"][0]["delta"].get("content", ""), nl=False)
        typer.echo()
    except httpx.HTTPError as exc:
        typer.secho(f"Error hablando con el gateway: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc


@app.command()
def sessions() -> None:
    """Lista las sesiones guardadas."""
    base, headers = _api()
    try:
        r = httpx.get(f"{base}/v1/sessions", headers=headers, timeout=10)
        r.raise_for_status()
    except httpx.HTTPError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    items = r.json()["sessions"]
    if not items:
        typer.echo("No hay sesiones.")
        return
    for s in items:
        typer.echo(f"{s['id'][:8]}  {s['title']}  [{s['active_agent']}]  {s['updated_at']}")


@app.command()
def resume(session_id: str = typer.Argument(..., help="ID de la sesión.")) -> None:
    """Muestra una sesión; si tiene una cadena de agentes a medias, la continúa."""
    base, headers = _api()
    try:
        r = httpx.post(f"{base}/v1/sessions/{session_id}/resume", headers=headers, timeout=10)
    except httpx.HTTPError as exc:
        typer.secho(f"Error: {exc}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1) from exc
    if r.status_code == 404:
        typer.secho("Sesión no encontrada.", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    data = r.json()
    subtasks = data.get("subtasks", [])
    if subtasks and any(st.get("state") != "hecha" for st in subtasks):
        estado = " -> ".join(f"{st['agent']}:{st['state']}" for st in subtasks)
        typer.secho(
            f"# {data['title']} — cadena a medias ({estado}); continuando...",
            fg=typer.colors.YELLOW,
        )
        try:
            with httpx.stream(
                "POST",
                f"{base}/v1/sessions/{session_id}/continue",
                headers=headers,
                timeout=300,
            ) as cr:
                cr.raise_for_status()
                for line in cr.iter_lines():
                    if not line.startswith("data: "):
                        continue
                    payload = line[6:]
                    if payload == "[DONE]":
                        break
                    d = json.loads(payload)
                    if d.get("type") == "meta":
                        typer.secho(f"[{d['agent']}]", fg=typer.colors.CYAN, err=True)
                    elif d.get("type") == "error":
                        typer.secho(f"[error] {d['message']}", fg=typer.colors.RED, err=True)
                    else:
                        typer.echo(d["choices"][0]["delta"].get("content", ""), nl=False)
            typer.echo()
        except httpx.HTTPError as exc:
            typer.secho(f"Error continuando: {exc}", fg=typer.colors.RED, err=True)
            raise typer.Exit(1) from exc
        return
    typer.secho(f"# {data['title']}", fg=typer.colors.GREEN)
    for m in data["messages"]:
        who = "tú" if m["role"] == "user" else "hefisty"
        typer.echo(f"{who}: {m['content']}")


@app.command()
def serve(
    host: str | None = typer.Option(None, help="Host de escucha (por defecto, config)."),
    port: int | None = typer.Option(None, help="Puerto (por defecto, config)."),
) -> None:
    """Arranca el gateway respetando la config; avisa si se expone a la LAN sin token."""
    import uvicorn

    from .gateway.app import app as gateway_app

    s = get_settings()
    h = host or s.host
    p = port or s.port
    loopback = {"127.0.0.1", "localhost", "::1"}
    if h not in loopback and not s.api_token:
        typer.secho(
            f"AVISO: gateway expuesto en {h} SIN token (HEFISTY_API_TOKEN vacío). "
            "Cualquiera en la red podría usarlo. Define un token o usa 127.0.0.1.",
            fg=typer.colors.RED,
            err=True,
        )
    uvicorn.run(gateway_app, host=h, port=p)


@app.command()
def status() -> None:
    """Muestra el estado de los servicios y los modelos cargados."""
    base, headers = _api()
    try:
        r = httpx.get(f"{base}/health", headers=headers, timeout=10)
        r.raise_for_status()
    except httpx.HTTPError:
        typer.secho("gateway: ● down (no responde)", fg=typer.colors.RED)
        raise typer.Exit(1) from None
    h = r.json()

    def mark(ok: bool) -> str:
        color = typer.colors.GREEN if ok else typer.colors.RED
        return typer.style("● up" if ok else "● down", fg=color)

    typer.echo(f"gateway: {mark(True)}")
    typer.echo(f"ollama:  {mark(h['ollama'])}")
    typer.echo(f"redis:   {mark(h['redis'])}")
    typer.echo(f"qdrant:  {mark(h['qdrant'])}")
    typer.echo(f"modelos cargados: {', '.join(h['loaded_models']) or 'ninguno'}")


# --- Conocimiento (RAG) ---

knowledge_app = typer.Typer(help="Diccionarios de conocimiento (RAG).")
app.add_typer(knowledge_app, name="knowledge")


def _knowledge():
    from .knowledge.store import KnowledgeStore
    from .ollama_client import OllamaClient

    s = get_settings()
    return s, OllamaClient(s.ollama_url), KnowledgeStore(s.qdrant_url)


@knowledge_app.command("ingest")
def knowledge_ingest(
    coleccion: str = typer.Argument(..., help="Nombre de la colección/diccionario."),
    path: str = typer.Option(..., "--path", help="Directorio con las fuentes."),
) -> None:
    """Ingesta un directorio de fuentes en una colección."""
    from .knowledge.ingest import ingest_path

    p = Path(path)
    if not p.is_dir():
        typer.secho(f"No existe el directorio: {path}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)
    s, ollama, store = _knowledge()
    res = asyncio.run(ingest_path(s, ollama, store, coleccion, p, language=coleccion))
    typer.secho(
        f"ingesta '{res.collection}': {res.files} archivos -> {res.chunks} chunks",
        fg=typer.colors.GREEN,
    )


@knowledge_app.command("status")
def knowledge_status() -> None:
    """Lista las colecciones y su conteo de chunks."""
    _, _, store = _knowledge()
    cols = store.collections()
    if not cols:
        typer.echo("No hay colecciones.")
        return
    for name, cnt in cols:
        typer.echo(f"{name}: {cnt} chunks")


@knowledge_app.command("delete")
def knowledge_delete(
    coleccion: str = typer.Argument(..., help="Colección a borrar."),
) -> None:
    """Borra una colección."""
    _, _, store = _knowledge()
    if store.delete(coleccion):
        typer.secho(f"borrada '{coleccion}'", fg=typer.colors.GREEN)
    else:
        typer.secho(f"no existe '{coleccion}'", fg=typer.colors.RED, err=True)


@app.command("index")
def index_cmd(ruta: str = typer.Argument(".", help="Ruta del repo a indexar.")) -> None:
    """Índice semántico del repo (incremental) para el search_code del Coder."""
    from .knowledge.repo_index import index_repo

    s, ollama, store = _knowledge()
    res = asyncio.run(index_repo(s, ollama, store, Path(ruta)))
    typer.secho(
        f"indice '{res.collection}': {res.scanned} archivos, {res.changed} cambiados "
        f"-> {res.chunks} chunks",
        fg=typer.colors.GREEN,
    )


# --- Memoria de largo plazo (Fase 4) ---

memory_app = typer.Typer(help="Memoria de largo plazo de Hefisty.")
app.add_typer(memory_app, name="memory")


def _memory_deps():
    from .knowledge.store import KnowledgeStore
    from .memory import MemoryService, MemoryStore
    from .ollama_client import OllamaClient

    s = get_settings()
    ollama = OllamaClient(s.ollama_url)
    store = MemoryStore(s.db_path)
    return s, ollama, store, MemoryService(s, ollama, store, KnowledgeStore(s.qdrant_url))


@memory_app.command("list")
def memory_list(todos: bool = typer.Option(False, "--all", help="Incluye archivados.")) -> None:
    """Lista los recuerdos."""
    _, _, store, _ = _memory_deps()
    mems = store.list(include_archived=todos)
    if not mems:
        typer.echo("Sin recuerdos.")
        return
    for m in mems:
        flags = " ".join(
            filter(None, ["[fijado]" if m.pinned else "", "[archivado]" if m.archived else ""])
        )
        typer.echo(f"[{m.id}] ({m.category}) usos={m.uses} {flags}  {m.fact}")


@memory_app.command("add")
def memory_add(
    hecho: str = typer.Argument(..., help="El hecho a recordar."),
    categoria: str = typer.Option("manual", "--category", "-c", help="Categoría."),
) -> None:
    """Añade un recuerdo manualmente."""
    _, ollama, _, svc = _memory_deps()

    async def _go():
        try:
            return await svc.add_manual(hecho, categoria)
        finally:
            await ollama.aclose()

    mem = asyncio.run(_go())
    if mem:
        typer.secho(f"recordado [{mem.id}]: {mem.fact}", fg=typer.colors.GREEN)
    else:
        typer.secho("no se pudo (revisa embeddings/Qdrant)", fg=typer.colors.RED, err=True)


@memory_app.command("forget")
def memory_forget(mem_id: int = typer.Argument(..., help="ID del recuerdo.")) -> None:
    """Borra un recuerdo (SQLite + Qdrant)."""
    _, _, _, svc = _memory_deps()
    if svc.forget(mem_id):
        typer.secho(f"olvidado [{mem_id}]", fg=typer.colors.GREEN)
    else:
        typer.secho(f"no existe [{mem_id}]", fg=typer.colors.RED, err=True)


@memory_app.command("pin")
def memory_pin(
    mem_id: int = typer.Argument(..., help="ID del recuerdo."),
    quitar: bool = typer.Option(False, "--off", help="Desfijar en vez de fijar."),
) -> None:
    """Fija (o desfija con --off) un recuerdo para que nunca decaiga."""
    _, _, store, _ = _memory_deps()
    if store.set_pinned(mem_id, not quitar):
        typer.secho(f"{'desfijado' if quitar else 'fijado'} [{mem_id}]", fg=typer.colors.GREEN)
    else:
        typer.secho(f"no existe [{mem_id}]", fg=typer.colors.RED, err=True)


@memory_app.command("learn")
def memory_learn(session_id: str = typer.Argument(..., help="Sesión a consolidar.")) -> None:
    """Consolida los recuerdos de una sesión (equivale al cierre de sesión)."""
    from .orchestrator.sessions import SessionStore

    s, ollama, _, svc = _memory_deps()
    sess = SessionStore(s.db_path).get(session_id)
    if sess is None:
        typer.secho(f"no existe la sesión {session_id}", fg=typer.colors.RED, err=True)
        raise typer.Exit(1)

    async def _go():
        try:
            return await svc.consolidate(sess.messages, origin=sess.id)
        finally:
            await ollama.aclose()

    typer.secho(f"{asyncio.run(_go())} recuerdos consolidados", fg=typer.colors.GREEN)


@memory_app.command("decay")
def memory_decay() -> None:
    """Archiva recuerdos viejos y sin uso (salvo los fijados)."""
    _, _, _, svc = _memory_deps()
    typer.secho(f"{svc.decay()} recuerdos archivados", fg=typer.colors.GREEN)


# --- Feedback -> datasets de corrección (Fase 4) ---

feedback_app = typer.Typer(help="Feedback y datasets de corrección.")
app.add_typer(feedback_app, name="feedback")


@feedback_app.command("export")
def feedback_export(rol: str = typer.Argument(..., help="Rol cuyo feedback exportar.")) -> None:
    """Exporta el feedback del rol a data/datasets/corrections/<rol>.jsonl."""
    from .feedback import export_corrections
    from .orchestrator.sessions import SessionStore

    s = get_settings()
    out = s.data_dir / "datasets" / "corrections"
    path, n = export_corrections(SessionStore(s.db_path), rol, out)
    typer.secho(f"{n} pares -> {path}", fg=typer.colors.GREEN)


@app.command("code")
def code_cmd(
    tarea: str = typer.Argument(..., help="Tarea de código a resolver en el workspace."),
    verbose: bool = typer.Option(False, "-v", "--verbose", help="Muestra las llamadas a tools."),
) -> None:
    """El Coder resuelve una tarea con sus herramientas (glob/grep/read_range/edit/search_code)."""
    from .agents.agentic import AgenticCoder
    from .knowledge.repo_index import collection_name
    from .knowledge.retrieval import Retriever
    from .knowledge.store import KnowledgeStore
    from .ollama_client import OllamaClient
    from .roles import load_role

    s = get_settings()
    s.workspace_dir.mkdir(parents=True, exist_ok=True)
    ollama = OllamaClient(s.ollama_url)
    agent = AgenticCoder(
        ollama,
        load_role("coder"),
        s.workspace_dir,
        s,
        retriever=Retriever(s, ollama, KnowledgeStore(s.qdrant_url)),
        repo_collection=collection_name(s.workspace_dir),
    )

    def on_event(ev: str) -> None:
        if verbose:
            typer.secho(f"  · {ev}", fg=typer.colors.BLUE, err=True)

    async def _go() -> dict:
        try:
            return await agent.run(tarea, on_event)
        finally:
            await ollama.aclose()

    res = asyncio.run(_go())
    typer.echo(res["answer"])
    if res["touched"]:
        typer.secho(
            f"archivos tocados: {', '.join(res['touched'])}", fg=typer.colors.GREEN, err=True
        )


if __name__ == "__main__":
    app()
