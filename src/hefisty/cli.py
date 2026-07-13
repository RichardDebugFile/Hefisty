"""CLI de Hefisty (Typer). Es un cliente HTTP del gateway; requiere el gateway arriba."""

from __future__ import annotations

import json

import httpx
import typer

from .config import get_settings

app = typer.Typer(add_completion=False, help="Hefisty: sistema de IA local orquestada.")


def _api() -> tuple[str, dict[str, str]]:
    s = get_settings()
    base = f"http://{s.host}:{s.port}"
    headers = {"Authorization": f"Bearer {s.api_token}"} if s.api_token else {}
    return base, headers


@app.command()
def ask(
    prompt: str = typer.Argument(..., help="Lo que quieres pedirle a Hefisty."),
    session: str | None = typer.Option(None, "--session", "-s", help="ID de sesión a continuar."),
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
    """Muestra el historial de una sesión."""
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
    typer.secho(f"# {data['title']}", fg=typer.colors.GREEN)
    for m in data["messages"]:
        who = "tú" if m["role"] == "user" else "hefisty"
        typer.echo(f"{who}: {m['content']}")


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


if __name__ == "__main__":
    app()
