"""Coder agéntico: bucle de function calling sobre las herramientas de navegación.

El modelo localiza archivos (glob/grep/search_code), los lee (read_range/leer_archivo)
y los edita (edit/escribir_archivo) por su cuenta, sin recibir rutas exactas. Cada
edición registra el archivo tocado. Bucle acotado a `max_rounds`.
"""

from __future__ import annotations

import json
import logging
import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ..config import Settings
from ..knowledge.retrieval import Retriever
from ..lang import collections_for, detect_language
from ..ollama_client import OllamaClient
from ..protections import sanitize_chunk
from ..roles import Role
from . import tools
from .audit import RunRecorder

logger = logging.getLogger("hefisty.agentic")


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
        "outline",
        "Esqueleto de un archivo: sus declaraciones (fun/class/object/interface/enum/def) con "
        "su número de línea, sin leerlo entero. Úsalo para saltar a la función correcta.",
        {"ruta": _STR},
        ["ruta"],
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

_TOOL_NAMES = [t["function"]["name"] for t in TOOLS_SPEC]

_TOOL_GUIDANCE = (
    "\n\nTienes herramientas para trabajar en el workspace. NO pidas rutas al usuario: "
    "descúbrelas tú con glob/grep/search_code, lee con read_range/leer_archivo y modifica "
    "con edit (reemplazo exacto) o escribir_archivo. Para cambios extensos o refactors, "
    "reescribe el archivo completo con escribir_archivo en vez de muchos edit pequeños. "
    "Aplica SIEMPRE los cambios en los archivos (no solo los describas). Cuando termines, "
    "responde con un resumen breve de lo que hiciste."
    "\n\nCÓMO NAVEGAR UN REPO GRANDE (importante, no gastes rondas):\n"
    "1. Tu localizador PRINCIPAL es `grep`, NO `search_code`. grep es exacto: te da archivo:línea "
    "del identificador. Para ubicar código, grepea un IDENTIFICADOR EXACTO ligado al síntoma "
    "(nombre de variable/campo/función/enum). Deriva el identificador del síntoma: 'porcentaje' → "
    "grep 'Percentage'; 'ordenado mal' → grep 'sortedBy'; 'no filtra' → grep 'filter'.\n"
    "2. El texto que ve el usuario es una CLAVE DE RECURSO (labelOf/stringResource), casi nunca "
    "está literal en el código: NO grepees la frase de UI.\n"
    "3. `search_code` es APROXIMADO (semántico): devuelve archivos de TEMA parecido, a menudo el "
    "equivocado. Úsalo SOLO si no sabes qué identificador buscar, y como MUCHO una o dos veces; "
    "luego grepea un símbolo concreto de esos archivos. NUNCA encadenes varias search_code.\n"
    "4. grep te da archivo:LÍNEA. Ve DIRECTO ahí: `read_range` en esa línea (±25) o usa `outline` "
    "para ver las declaraciones del archivo y saltar a la función. NO re-busques lo que grep ya "
    "te dio: cuando tengas la línea, LÉELA y EDÍTALA. grep/glob ya IGNORAN .git/, build/, .gradle/."
)

# Un único pase de auto-revisión antes de cerrar: reduce que el modelo se quede a medias
# en tareas de muchas condiciones (p. ej. web-ARIA con 10 requisitos).
_REVIEW_NUDGE = (
    "Antes de terminar, revisa el enunciado punto por punto: ¿aplicaste en los archivos "
    "TODOS los cambios pedidos, no solo algunos? Si algo quedó a medias o sin hacer, "
    "corrígelo AHORA con edit/escribir_archivo. Si de verdad está todo completo, responde "
    "solo con un resumen breve."
)

_SEARCH_LOOP_NUDGE = (
    "PARA. Llevas varias búsquedas semánticas (search_code) seguidas sin leer ni editar nada. "
    "search_code es APROXIMADO y te está devolviendo archivos de tema parecido, no el correcto. "
    "Cambia de estrategia AHORA: usa `grep` con un IDENTIFICADOR EXACTO derivado del síntoma "
    "(un nombre de variable/campo/función, p. ej. 'Percentage', 'sortedBy', 'filter'). grep da "
    "archivo:línea exactos. Luego `read_range` ese archivo y edítalo. NO repitas search_code."
)

_TEXT_TOOLCALL_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)

# Árbol de archivos que se inyecta al inicio para que el Coder no navegue carpeta por
# carpeta (modelos como gpt-oss se rinden en árboles profundos, p. ej. src/com/x/y/ en Java).
_MAX_TREE = 200
_MAX_TREE_CHARS = 8000
# Máx. de carpetas en el mapa de paquetes (panorama de repos grandes).
_MAX_MAP = 160
# Tope del resultado de una tool devuelto al modelo: un archivo enorme reventaba el contexto.
_MAX_TOOL_RESULT = 6000
_TREE_SKIP = {
    "node_modules",
    ".git",
    "target",
    "build",
    "dist",
    ".venv",
    "__pycache__",
    ".idea",
    ".gradle",
    ".next",
    "coverage",
}


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
        max_rounds: int = 16,
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
            if name == "outline":
                return "\n".join(tools.outline(self._ws, args["ruta"])) or "(sin declaraciones)"
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
                if not hits:
                    return "(sin resultados)"
                # Devolver un SNIPPET del código de cada candidato, no solo la ruta: sin ver el
                # contenido el modelo no distingue el ViewModel del Composable y deambula entre
                # archivos de nombre parecido (observado en repos grandes). Con el fragmento va
                # directo al archivo correcto y abre el bueno con read_range.
                out = []
                for h in hits:
                    loc = h.source + (f" · {h.section}" if getattr(h, "section", "") else "")
                    snippet = " ".join((h.text or "").split())[:200]
                    out.append(f"{loc} (score {h.score:.2f})\n    {snippet}")
                return "\n".join(out)
        except tools.ToolError as exc:
            return f"ERROR: {exc}"
        except (KeyError, TypeError, ValueError) as exc:
            # Tool call malformada (falta un argumento, tipo inválido): devuélvelo como
            # error para que el modelo se corrija, en vez de romper todo el bucle.
            return f"ERROR: argumento faltante o inválido: {exc}"
        # El modelo a veces inventa nombres de tools (p. ej. `repo_browser.search`). Recuérdale
        # las reales para que se reencauce en vez de gastar rondas con herramientas inexistentes.
        return f"ERROR: herramienta desconocida '{name}'. Usa SOLO estas: {', '.join(_TOOL_NAMES)}."

    async def _dict_context(self, task: str) -> list[dict[str, Any]]:
        """Inyecta chunks de los diccionarios (`[lenguaje, patrones]`) como contexto de
        sistema, igual que el path de streaming del orquestador. Sin esto, el Coder que
        EDITA no ve los diccionarios (solo tendría `search_code` del índice del repo)."""
        if self._retriever is None:
            return []
        lang = detect_language(task)
        collections = collections_for(lang, self._s.extra_collections)
        if not collections:
            return []
        try:
            hits = await self._retriever.retrieve(task, collections)
        except Exception as exc:  # Qdrant caído no debe romper la tarea
            logger.warning("retrieval de diccionario falló: %s", exc)
            return []
        if not hits:
            return []
        parts = []
        for h in hits:
            safe, _degraded = sanitize_chunk(h.text)
            parts.append(f"[{h.source}] ({h.section})\n{safe}")
        return [
            {
                "role": "system",
                "content": (
                    "Contexto recuperado del diccionario. Úsalo si es relevante y CITA la "
                    "fuente entre corchetes, p. ej. [archivo.md]. Si no aporta, ignóralo.\n\n"
                    + "\n\n".join(parts)
                ),
            }
        ]

    def _workspace_tree(self) -> str:
        """Contexto inicial para orientar al Coder y que no navegue carpeta por carpeta.
        Repo chico → lista de archivos. Repo grande → MAPA de paquetes (carpeta + nº de
        archivos): da el panorama del dominio sin volcar miles de rutas (que además, con VRAM
        justa, inflarían el KV-cache y provocarían 500/OOM en gpt-oss)."""
        try:
            files = tools.glob(self._ws, "**/*")
        except tools.ToolError:
            return ""
        files = [f for f in files if not (set(f.split("/")) & _TREE_SKIP)]
        if not files:
            return ""
        if len(files) > _MAX_TREE:
            return self._dir_map(files)
        lines: list[str] = []
        used = 0
        for f in files:
            if used + len(f) + 1 > _MAX_TREE_CHARS:
                break
            lines.append(f)
            used += len(f) + 1
        listing = "\n".join(lines)
        extra = len(files) - len(lines)
        if extra > 0:
            listing += f"\n… (+{extra} archivos; usa glob/grep/search_code para el resto)"
        return listing

    def _dir_map(self, files: list[str]) -> str:
        """Mapa de carpetas (paquetes) con su conteo de archivos, ordenado y acotado.
        Panorama de un repo grande: `.../approvements/viewmodel/favorites/ (5)`."""
        counts: dict[str, int] = {}
        for f in files:
            parent = f.rsplit("/", 1)[0] if "/" in f else "."
            counts[parent] = counts.get(parent, 0) + 1
        lines: list[str] = []
        used = 0
        for d in sorted(counts):
            line = f"{d}/ ({counts[d]})"
            if len(lines) >= _MAX_MAP or used + len(line) + 1 > _MAX_TREE_CHARS:
                break
            lines.append(line)
            used += len(line) + 1
        listing = "\n".join(lines)
        extra = len(counts) - len(lines)
        if extra > 0:
            listing += f"\n… (+{extra} carpetas; usa glob/grep/outline para el resto)"
        return listing

    async def run(self, task: str, on_event: Callable[[str], None] | None = None) -> dict[str, Any]:
        rec = RunRecorder(
            self._s.data_dir / "agent_runs",
            task,
            meta={
                "model": self._role.model,
                "workspace": str(self._ws),
                "repo_collection": self._repo_col,
                "extra_collections": list(self._s.extra_collections),
                "num_ctx": self._s.coder_num_ctx,
                "max_rounds": self._max_rounds,
            },
            enabled=self._s.audit_enabled,
        )
        convo: list[dict[str, Any]] = [
            {"role": "system", "content": self._role.system_prompt + _TOOL_GUIDANCE},
            *await self._dict_context(task),
        ]
        tree = self._workspace_tree()
        if tree:
            convo.append(
                {
                    "role": "system",
                    "content": (
                        "Estructura del workspace para orientarte (archivos, o carpetas con su "
                        "nº de archivos si el repo es grande). Úsala + grep/outline; NO navegues "
                        "carpeta por carpeta:\n" + tree
                    ),
                }
            )
        convo.append({"role": "user", "content": task})
        steps = 0
        reviewed = False
        last_answer = ""
        search_streak = 0  # search_code seguidos sin leer/editar → reencauzar a grep
        for _ in range(self._max_rounds):
            msg = await self._ollama.chat_tools(
                self._role.model,
                convo,
                TOOLS_SPEC,
                keep_alive=self._s.keep_alive,
                options={"num_ctx": self._s.coder_num_ctx} if self._s.coder_num_ctx else None,
            )
            content = msg.get("content", "")
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                fallback = _extract_text_toolcall(content)
                if fallback is not None:
                    tool_calls = [fallback]
            rec.assistant(steps, content)
            if not tool_calls:
                if content:
                    last_answer = content
                if not reviewed:  # un pase de auto-revisión antes de cerrar
                    reviewed = True
                    convo.append({"role": "assistant", "content": content})
                    convo.append({"role": "user", "content": _REVIEW_NUDGE})
                    continue
                answer = content or last_answer
                rec.run_end(answer, sorted(self._touched), steps, "completed")
                return {
                    "answer": answer,
                    "touched": sorted(self._touched),
                    "steps": steps,
                    "audit": str(rec.path) if rec.path else None,
                }
            convo.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
            for tc in tool_calls:
                fn = tc.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments", {})
                if isinstance(args, str):
                    args = json.loads(args or "{}")
                t0 = time.monotonic()
                result = await self._exec(name, args)
                ms = int((time.monotonic() - t0) * 1000)
                # Registrar la edición ANTES de truncar el resultado: el diff exacto es lo más
                # valioso para verificar la corrección de la IA contra la verdad-terreno.
                if isinstance(args, dict) and name == "edit":
                    rec.edit(
                        args.get("ruta", ""),
                        args.get("texto_viejo", ""),
                        args.get("texto_nuevo", ""),
                    )
                elif isinstance(args, dict) and name == "escribir_archivo":
                    rec.write_file(args.get("ruta", ""), args.get("contenido", ""))
                if len(result) > _MAX_TOOL_RESULT:
                    # Un archivo enorme (leer_archivo) o un grep largo infla el contexto y, con
                    # VRAM justa, provoca OOM (500) en la ronda siguiente. Trunca y sugiere acotar.
                    result = (
                        result[:_MAX_TOOL_RESULT]
                        + f"\n… (resultado truncado a {_MAX_TOOL_RESULT} chars; "
                        "usa read_range o grep para acotar)"
                    )
                steps += 1
                rec.tool(steps, name, args, result, ok=not result.startswith("ERROR"), ms=ms)
                if name == "search_code":
                    search_streak += 1
                elif name in (
                    "grep",
                    "read_range",
                    "outline",
                    "leer_archivo",
                    "edit",
                    "escribir_archivo",
                ):
                    search_streak = 0
                if on_event is not None:
                    on_event(
                        f"{name}({', '.join(f'{k}={v}' for k, v in args.items())[:80]}) -> "
                        f"{result.splitlines()[0][:80] if result else ''}"
                    )
                convo.append({"role": "tool", "content": result})
            # Reencauze anti-loop: si el modelo encadena search_code (semántico, aproximado) sin
            # leer ni editar, se queda dando vueltas entre archivos de nombre parecido (observado
            # en repos grandes). Empújalo a grep exacto + read_range.
            if search_streak >= 3:
                convo.append({"role": "user", "content": _SEARCH_LOOP_NUDGE})
                rec.note("anti_loop_nudge", search_streak=search_streak)
                search_streak = 0
        answer = "(se alcanzó el límite de pasos sin respuesta final)"
        rec.run_end(answer, sorted(self._touched), steps, "max_rounds")
        return {
            "answer": answer,
            "touched": sorted(self._touched),
            "steps": steps,
            "audit": str(rec.path) if rec.path else None,
        }
