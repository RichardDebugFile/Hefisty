"""Coder agéntico: bucle de function calling sobre las herramientas de navegación.

El modelo localiza archivos (glob/grep/search_code), los lee (read_range/leer_archivo)
y los edita (edit/escribir_archivo) por su cuenta, sin recibir rutas exactas. Cada
edición registra el archivo tocado. Bucle acotado a `max_rounds`.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import Settings
from ..knowledge.retrieval import Retriever
from ..ollama_client import OllamaClient
from ..roles import Role
from . import tools


def _fn(name: str, desc: str, props: dict, required: list[str]) -> dict:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": desc,
            "parameters": {"type": "object", "properties": props, "required": required},
        },
    }


_STR = {"type": "string"}
_INT = {"type": "integer"}

TOOLS_SPEC = [
    _fn(
        "glob",
        "Lista archivos que casan un patrón glob dentro del workspace.",
        {"patron": _STR},
        ["patron"],
    ),
    _fn(
        "grep",
        "Busca una regex en archivos del workspace (archivo:linea: contenido).",
        {"regex": _STR, "ruta": _STR},
        ["regex"],
    ),
    _fn(
        "read_range",
        "Lee las líneas [inicio, fin] de un archivo.",
        {"ruta": _STR, "inicio": _INT, "fin": _INT},
        ["ruta", "inicio", "fin"],
    ),
    _fn(
        "edit",
        "Reemplazo exacto único en un archivo (falla si el texto no es único).",
        {"ruta": _STR, "texto_viejo": _STR, "texto_nuevo": _STR},
        ["ruta", "texto_viejo", "texto_nuevo"],
    ),
    _fn("leer_archivo", "Lee un archivo completo.", {"ruta": _STR}, ["ruta"]),
    _fn(
        "escribir_archivo",
        "Escribe o crea un archivo.",
        {"ruta": _STR, "contenido": _STR},
        ["ruta", "contenido"],
    ),
    _fn("listar_directorio", "Lista el contenido de un directorio.", {"ruta": _STR}, []),
    _fn(
        "search_code",
        "Búsqueda semántica en el índice del repo; devuelve archivos candidatos.",
        {"consulta": _STR},
        ["consulta"],
    ),
]

_TOOL_GUIDANCE = (
    "\n\nTienes herramientas para trabajar en el workspace. NO pidas rutas al usuario: "
    "descúbrelas tú con glob/grep/search_code, lee con read_range/leer_archivo y modifica "
    "con edit (reemplazo exacto) o escribir_archivo. Cuando termines, responde con un "
    "resumen breve de lo que hiciste."
)

_TEXT_TOOLCALL_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)


def _extract_text_toolcall(content: str) -> dict | None:
    """Fallback: algunos modelos (o versiones viejas de Ollama) emiten la llamada como
    JSON en el texto en vez de en `tool_calls`. Detecta {"name":…, "arguments":…}."""
    text = content.strip()
    m = _TEXT_TOOLCALL_RE.search(text)
    if m:
        text = m.group(1)
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        return None
    if isinstance(obj, dict) and "name" in obj and "arguments" in obj:
        return {"function": {"name": obj["name"], "arguments": obj["arguments"]}}
    return None


class AgenticCoder:
    def __init__(
        self,
        ollama: OllamaClient,
        role: Role,
        workspace: Path,
        settings: Settings,
        retriever: Retriever | None = None,
        repo_collection: str | None = None,
        max_rounds: int = 8,
    ) -> None:
        self._ollama = ollama
        self._role = role
        self._ws = Path(workspace)
        self._s = settings
        self._retriever = retriever
        self._repo_col = repo_collection
        self._max_rounds = max_rounds
        self._touched: set[str] = set()

    async def _exec(self, name: str, args: dict[str, Any]) -> str:
        try:
            if name == "glob":
                return "\n".join(tools.glob(self._ws, args["patron"])) or "(sin resultados)"
            if name == "grep":
                res = tools.grep(self._ws, args["regex"], args.get("ruta", "."))
                return "\n".join(res) or "(sin resultados)"
            if name == "read_range":
                return tools.read_range(
                    self._ws, args["ruta"], int(args["inicio"]), int(args["fin"])
                )
            if name == "edit":
                out = tools.edit(self._ws, args["ruta"], args["texto_viejo"], args["texto_nuevo"])
                self._touched.add(args["ruta"])
                return out
            if name == "leer_archivo":
                return tools.leer_archivo(self._ws, args["ruta"])
            if name == "escribir_archivo":
                out = tools.escribir_archivo(self._ws, args["ruta"], args["contenido"])
                self._touched.add(args["ruta"])
                return out
            if name == "listar_directorio":
                return "\n".join(tools.listar_directorio(self._ws, args.get("ruta", ".")))
            if name == "search_code":
                if self._retriever is None or self._repo_col is None:
                    return "(search_code no disponible: indexa el repo con 'hefisty index .')"
                hits = await self._retriever.retrieve(args["consulta"], [self._repo_col])
                return (
                    "\n".join(f"{h.source} (score {h.score:.2f})" for h in hits)
                    or "(sin resultados)"
                )
        except tools.ToolError as exc:
            return f"ERROR: {exc}"
        return f"(herramienta desconocida: {name})"

    async def run(self, task: str, on_event: Callable[[str], None] | None = None) -> dict[str, Any]:
        convo: list[dict[str, Any]] = [
            {"role": "system", "content": self._role.system_prompt + _TOOL_GUIDANCE},
            {"role": "user", "content": task},
        ]
        steps = 0
        for _ in range(self._max_rounds):
            msg = await self._ollama.chat_tools(
                self._role.model, convo, TOOLS_SPEC, keep_alive=self._s.keep_alive
            )
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                fallback = _extract_text_toolcall(content)
                if fallback is not None:
                    tool_calls = [fallback]
            if not tool_calls:
                return {
                    "answer": content,
                    "touched": sorted(self._touched),
                    "steps": steps,
                }
            convo.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    args = json.loads(args or "{}")
                result = await self._exec(name, args)
                steps += 1
                if on_event is not None:
                    on_event(
                        f"{name}({', '.join(f'{k}={v}' for k, v in args.items())[:80]}) -> "
                        f"{result.splitlines()[0][:80] if result else ''}"
                    )
                convo.append({"role": "tool", "content": result})
        return {
            "answer": "(se alcanzó el límite de pasos sin respuesta final)",
            "touched": sorted(self._touched),
            "steps": steps,
        }
