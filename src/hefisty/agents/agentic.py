"""Coder agéntico: bucle de function calling sobre las herramientas de navegación.

El modelo localiza archivos (glob/grep; search_code semántico con cupo por tarea), los lee
(read_range/outline/leer_archivo) y los edita (edit/escribir_archivo) por su cuenta, sin
recibir rutas exactas. Cada edición registra el archivo tocado. Bucle acotado a `max_rounds`.
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
        "Busca una regex en archivos del workspace (archivo:linea: contenido). Con "
        "contexto=N devuelve N líneas alrededor de cada hit (como grep -C): úsalo para ver "
        "el código que rodea al match sin tener que adivinar la ventana de read_range. Si el "
        "término es amplio (muchos hits) devuelve un RESUMEN por archivo con sus líneas: elige "
        "el archivo del módulo que te interesa y acota con ruta=<carpeta>.",
        {"regex": _STR, "ruta": _STR, "contexto": _INT},
        ["regex"],
    ),
    _fn(
        "read_range",
        "Lee las líneas [inicio, fin] de un archivo. Pide ventanas AMPLIAS (50-80 líneas: la "
        "función entera) en vez de muchas de 10-15: cada llamada cuesta una ronda.",
        {"ruta": _STR, "inicio": _INT, "fin": _INT},
        ["ruta", "inicio", "fin"],
    ),
    _fn(
        "har",
        "Consulta un archivo .har (captura de red) sin volcarlo: sin 'indice' lista las "
        "peticiones (METHOD status url), con 'filtro' acota por subcadena de la URL; con "
        "'indice' muestra esa petición en detalle (headers, query, body del request y "
        "respuesta). Úsalo para comparar lo que hace web contra lo que manda mobile.",
        {"ruta": _STR, "filtro": _STR, "indice": _INT},
        ["ruta"],
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
]


def _search_code_spec(left: int) -> dict:
    """`search_code` se ofrece aparte y con cupo: la descripción le dice al modelo cuántos
    usos le quedan en la tarea, y al agotarlos la tool se retira del set (ver `_tools_spec`).
    Es semántica/aproximada y el modelo tendía a encadenarla (44 % de sus llamadas) en vez
    de ir a grep exacto."""
    return _fn(
        "search_code",
        "Búsqueda semántica APROXIMADA en el índice del repo: devuelve archivos de tema "
        "parecido con un fragmento, a menudo el equivocado. Úsala SOLO si no sabes qué "
        f"identificador grepear. Te quedan {left} usos en esta tarea; después desaparece.",
        {"consulta": _STR},
        ["consulta"],
    )


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
    "equivocado. Úsalo SOLO si no sabes qué identificador buscar. Tiene un CUPO por tarea (su "
    "descripción te dice cuántos usos te quedan) y al agotarlo desaparece: gástalo con cabeza y "
    "luego grepea un símbolo concreto de los archivos que te dio. NUNCA encadenes varias.\n"
    "3b. Usa `grep` con `contexto=5`: te muestra el código ALREDEDOR de cada hit en la misma "
    "llamada. Así ves la función y sus vecinas sin adivinar la ventana de read_range.\n"
    "4. grep te da archivo:LÍNEA. Ve DIRECTO ahí: `read_range` en esa línea (±25) o usa `outline` "
    "para ver las declaraciones del archivo y saltar a la función. NO re-busques lo que grep ya "
    "te dio: cuando tengas la línea, LÉELA y EDÍTALA. grep/glob ya IGNORAN .git/, build/, "
    ".gradle/.\n"
    "5. Si el enunciado nombra un MÓDULO o pantalla (p. ej. 'Aprobaciones', 'pestaña X'), "
    "localiza primero su carpeta en el mapa del workspace y ACOTA todos los grep con "
    "`ruta=<esa carpeta>`: el mismo término ('search', 'Item') aparece en decenas de módulos "
    "ajenos y te desvía. Un nombre parecido en otra carpeta NO es el sitio.\n"
    "6. NUNCA inventes rutas ni nombres de archivo: usa solo rutas que te hayan devuelto glob, "
    "grep, outline o listar_directorio. Si una ruta da 'No existe', lista la carpeta real "
    "(listar_directorio) y usa lo que aparece.\n"
    "7. Si el enunciado dice que un caso HERMANO funciona (otra pestaña/perfil/flujo), ábrelo: "
    "grepea el mismo identificador en su carpeta y compara qué asigna/llama él que el roto no."
)

# Un único pase de auto-revisión antes de cerrar: reduce que el modelo se quede a medias
# en tareas de muchas condiciones (p. ej. web-ARIA con 10 requisitos). Pide además cuadrar
# SÍNTOMA por SÍNTOMA: en la eval §2 (13/09/2026) arregló el índice cruzado y dio por hecho
# que eso también explicaba "no quedan en orden cronológico", cuando el orden tenía su propia
# causa (un `sortedBy` por texto formateado) que ya había leído y no cuestionó.
_REVIEW_NUDGE = (
    "Antes de terminar, revisa el enunciado punto por punto: ¿aplicaste en los archivos "
    "TODOS los cambios pedidos, no solo algunos? Si el enunciado describe VARIOS síntomas, "
    "cuadra cada uno por separado: ¿qué línea concreta causa ESE síntoma y qué edición la "
    "corrige? Un mismo fix rara vez explica dos síntomas distintos; si para alguno no tienes "
    "línea causante, vuelve al código que ya leíste y cuestiónalo (p. ej. ¿por qué valor se "
    "ordena/compara/filtra realmente?). Si algo quedó a medias o sin hacer, corrígelo AHORA "
    "con edit/escribir_archivo. Si de verdad está todo completo, responde solo con un "
    "resumen breve."
)

# Los nudges se añaden como mensajes de usuario. Si el contexto se trunca (razonamiento largo +
# muchas rondas), el enunciado original se cae y el nudge pasa a ser "la petición": el modelo
# respondió "no se recibieron instrucciones específicas". Por eso TODO nudge re-ancla la tarea.
_TASK_ANCHOR = "RECORDATORIO — TAREA ORIGINAL (es lo único que debes resolver):\n{task}\n\n"

_SEARCH_LOOP_NUDGE = (
    "PARA. Llevas varias búsquedas semánticas (search_code) seguidas sin leer ni editar nada. "
    "search_code es APROXIMADO y te está devolviendo archivos de tema parecido, no el correcto. "
    "Cambia de estrategia AHORA: usa `grep` con un IDENTIFICADOR EXACTO derivado del síntoma "
    "(un nombre de variable/campo/función, p. ej. 'Percentage', 'sortedBy', 'filter'). grep da "
    "archivo:línea exactos. Luego `read_range` ese archivo y edítalo. NO repitas search_code."
)

_TOOL_ALIASES = {
    "open": "leer_archivo",
    "read": "leer_archivo",
    "cat": "leer_archivo",
    "view": "leer_archivo",
}
# Líneas de cabecera que se muestran de un archivo grande antes de su esqueleto.
_BIG_FILE_HEAD = 60

_EMPTY_NUDGE = (
    "Tu última respuesta llegó VACÍA (sin texto ni herramienta). Continúa la tarea: llama a "
    "la siguiente herramienta (read_range/outline/grep/edit) o, si ya terminaste, escribe el "
    "resumen de lo que cambiaste."
)
_MAX_EMPTY = 2

_TEXT_TOOLCALL_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)

# Árbol de archivos que se inyecta al inicio para que el Coder no navegue carpeta por
# carpeta (modelos como gpt-oss se rinden en árboles profundos, p. ej. src/com/x/y/ en Java).
_MAX_TREE = 200
_MAX_TREE_CHARS = 8000
# Máx. de carpetas en el mapa de paquetes (panorama de repos grandes).
_MAX_MAP = 240
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


class _Node:
    """Carpeta del mapa: hijos por nombre, nº de archivos propios y recursivos."""

    __slots__ = ("children", "own", "total")

    def __init__(self) -> None:
        self.children: dict[str, _Node] = {}
        self.own = 0
        self.total = 0


def _build_tree(files: list[str]) -> _Node:
    root = _Node()
    for f in files:
        parts = f.split("/")
        node = root
        node.total += 1
        for seg in parts[:-1]:
            node = node.children.setdefault(seg, _Node())
            node.total += 1
        node.own += 1
    return root


def _compact(node: _Node) -> None:
    """Colapsa cadenas de un solo hijo sin archivos propios: `src/main/java/com/x/`."""
    for name in list(node.children):
        child = node.children[name]
        path = name
        while child.own == 0 and len(child.children) == 1:
            sub_name, sub = next(iter(child.children.items()))
            path = f"{path}/{sub_name}"
            child = sub
        del node.children[name]
        node.children[path] = child
        _compact(child)


def _expand_by_size(root: _Node, budget: int) -> set[int]:
    """Elige qué carpetas desplegar: siempre la más GRANDE (por archivos) que aún no lo está,
    mientras sus hijos quepan en el presupuesto de nodos. Así un subárbol con 1 700 archivos
    (el código fuente) se abre hasta los módulos, y `.github/` o `libs/` quedan plegados."""
    expanded: set[int] = {id(root)}
    visible = len(root.children)
    candidates = [c for c in root.children.values() if c.children]
    while candidates:
        candidates.sort(key=lambda n: n.total, reverse=True)
        node = candidates.pop(0)
        if visible + len(node.children) > budget:
            continue  # no cabe; probar con los siguientes (más chicos)
        expanded.add(id(node))
        visible += len(node.children)
        candidates.extend(c for c in node.children.values() if c.children)
    return expanded


def _count_nodes(node: _Node) -> int:
    return sum(1 + _count_nodes(c) for c in node.children.values())


def _render(node: _Node, expanded: set[int], level: int, out: list[str]) -> int:
    """Escribe el árbol; devuelve cuántas carpetas quedaron plegadas (marcadas con ›)."""
    hidden = 0
    for name in sorted(node.children):
        child = node.children[name]
        open_ = id(child) in expanded
        more = " ›" if child.children and not open_ else ""
        out.append(f"{'  ' * level}{name}/ ({child.total}){more}")
        if open_:
            hidden += _render(child, expanded, level + 1, out)
        else:
            hidden += _count_nodes(child)
    return hidden


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
        max_rounds: int = 20,
    ) -> None:
        self._ollama = ollama
        self._role = role
        self._ws = Path(workspace)
        self._s = settings
        self._retriever = retriever
        self._repo_col = repo_collection
        self._max_rounds = max_rounds
        self._touched: set[str] = set()
        self._search_left = settings.coder_search_code_max

    def _search_code_available(self) -> bool:
        return self._retriever is not None and self._repo_col is not None

    def _tools_spec(self) -> list[dict]:
        """Set de tools de la ronda. `search_code` solo entra si hay índice del repo y aún
        queda cupo; ofrecer una tool que siempre falla o está agotada gasta rondas."""
        if self._search_code_available() and self._search_left > 0:
            return [*TOOLS_SPEC, _search_code_spec(self._search_left)]
        return list(TOOLS_SPEC)

    def _tool_names(self) -> list[str]:
        return [t["function"]["name"] for t in self._tools_spec()]

    def _search_code_as_grep(self, consulta: str) -> str:
        """Redirige una `search_code` sin cupo/índice a un grep exacto de la consulta
        (espacios → `\\s+`), con contexto. Si no hay hits, lo dice y reencauza."""
        motivo = "sin índice del repo" if not self._search_code_available() else "cupo agotado"
        q = consulta.strip()
        if not q:
            return f"ERROR: search_code {motivo} y consulta vacía. Usa grep con un identificador."
        regex = r"\s+".join(re.escape(w) for w in q.split())
        try:
            res = tools.grep(self._ws, regex, ".", contexto=3)
        except tools.ToolError as exc:
            return f"ERROR: search_code {motivo}; grep '{q}' falló: {exc}"
        if not res:
            return (
                f"search_code {motivo}; grep exacto de '{q}' no dio resultados. Prueba `grep` con "
                "un identificador más corto (una sola palabra CamelCase) o `outline` del archivo."
            )
        return f"search_code {motivo}: ejecuté grep '{q}' en su lugar:\n" + "\n".join(res)

    async def _exec(self, name: str, args: dict[str, Any]) -> str:
        # Alias de tools que el modelo inventa con regularidad (`open`, `read`, `cat`, `view`
        # con una `ruta`): 1 ronda perdida por corrida en 3 de las evals §6 del 13/09/2026.
        if name in _TOOL_ALIASES and isinstance(args, dict) and "ruta" in args:
            name = _TOOL_ALIASES[name]
        try:
            if name == "glob":
                return "\n".join(tools.glob(self._ws, args["patron"])) or "(sin resultados)"
            if name == "grep":
                # El modelo confunde `patron` (de glob) con `regex`: aceptar el alias ahorra
                # una ronda de error por cada despiste (2 en la eval §2 del 13/09/2026).
                regex = args["regex"] if "regex" in args else args["patron"]
                res = tools.grep(
                    self._ws,
                    regex,
                    args.get("ruta") or ".",
                    contexto=int(args.get("contexto") or 0),
                )
                return "\n".join(res) or "(sin resultados)"
            if name == "read_range":
                return tools.read_range(
                    self._ws, args["ruta"], int(args["inicio"]), int(args["fin"])
                )
            if name == "har":
                idx = args.get("indice")
                return tools.har(
                    self._ws,
                    args["ruta"],
                    str(args.get("filtro") or ""),
                    int(idx) if idx is not None and str(idx) != "" else None,
                )
            if name == "outline":
                return "\n".join(tools.outline(self._ws, args["ruta"])) or "(sin declaraciones)"
            if name == "edit":
                out = tools.edit(self._ws, args["ruta"], args["texto_viejo"], args["texto_nuevo"])
                self._touched.add(args["ruta"])
                return out
            if name == "leer_archivo":
                content = tools.leer_archivo(self._ws, args["ruta"])
                if len(content) <= _MAX_TOOL_RESULT:
                    return content
                # Archivo grande: el truncado ciego dejaba al modelo creyendo que "leyó el
                # archivo" cuando vio ~150 líneas (eval §6 v4: 646 líneas, la función causante
                # en la 184, nunca vista). Mejor: cabecera + ESQUELETO completo con líneas.
                head_lines = content.splitlines()[:_BIG_FILE_HEAD]
                total = content.count("\n") + 1
                esqueleto = tools.outline(self._ws, args["ruta"])
                return (
                    "\n".join(f"{i}: {ln}" for i, ln in enumerate(head_lines, 1))
                    + f"\n… (archivo de {total} líneas: se muestran las {len(head_lines)} "
                    "primeras. Esqueleto completo — usa read_range en la función que te "
                    "interese):\n" + "\n".join(esqueleto)
                )
            if name == "escribir_archivo":
                out = tools.escribir_archivo(self._ws, args["ruta"], args["contenido"])
                self._touched.add(args["ruta"])
                return out
            if name == "listar_directorio":
                return "\n".join(tools.listar_directorio(self._ws, args.get("ruta", ".")))
            if name == "search_code":
                if not self._search_code_available() or self._search_left <= 0:
                    # Sin índice o sin cupo: el modelo sigue invocándola (la ve en su propio
                    # historial aunque ya no esté en el set). En vez de gastar la ronda en un
                    # ERROR, se ejecuta un grep con la consulta: tras agotar el cupo las
                    # consultas eran identificadores (`getResumenInfo`, `buildFromAudit`)
                    # que grep resuelve exacto (eval §2 v4: 4 rondas perdidas en ERROR).
                    return self._search_code_as_grep(str(args["consulta"]))
                self._search_left -= 1
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
                out.append(
                    f"(search_code: quedan {self._search_left} usos en esta tarea. Ahora grepea "
                    "un identificador concreto de estos archivos.)"
                )
                return "\n".join(out)
        except tools.ToolError as exc:
            return f"ERROR: {exc}"
        except (KeyError, TypeError, ValueError) as exc:
            # Tool call malformada (falta un argumento, tipo inválido): devuélvelo como
            # error para que el modelo se corrija, en vez de romper todo el bucle.
            return f"ERROR: argumento faltante o inválido: {exc}"
        # El modelo a veces inventa nombres de tools (p. ej. `repo_browser.search`). Recuérdale
        # las reales para que se reencauce en vez de gastar rondas con herramientas inexistentes.
        return (
            f"ERROR: herramienta desconocida '{name}'. Usa SOLO estas: "
            f"{', '.join(self._tool_names())}."
        )

    async def _dict_context(self, task: str, rec: RunRecorder) -> list[dict[str, Any]]:
        """Inyecta chunks de los diccionarios (`[lenguaje, patrones]`) como contexto de
        sistema, igual que el path de streaming del orquestador. Sin esto, el Coder que
        EDITA no ve los diccionarios (solo tendría `search_code` del índice del repo).
        Deja en la traza QUÉ chunks entraron: sin eso no se puede saber si un fallo fue
        por no saber (el chunk no llegó) o por no aplicar (llegó y lo ignoró)."""
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
        rec.note(
            "dict_context",
            collections=collections,
            hits=[
                {
                    "source": h.source,
                    "section": h.section,
                    "collection": getattr(h, "collection", ""),
                    "score": round(h.score, 3),
                    "preview": " ".join((h.text or "").split())[:160],
                }
                for h in hits
            ],
        )
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
            listing += f"\n… (+{extra} archivos; usa glob/grep/outline para el resto)"
        return listing

    def _dir_map(self, files: list[str]) -> str:
        """Mapa de carpetas como ÁRBOL compactado y equilibrado en profundidad.

        La versión anterior listaba `ruta/completa/ (n)` en orden alfabético con tope de
        caracteres: en el repo objetivo (578 carpetas, prefijo de ~70 chars por línea) cabían
        75 y TODAS eran de `cuentas/`; `pedidos/` y 500 carpetas más no existían para el
        modelo (eval §6, 13/09/2026: buscó la pestaña Favoritos en el módulo equivocado).

        Ahora: (1) se colapsan las cadenas de un solo hijo (`java/com/x/y/` es un nodo),
        (2) se despliegan primero las carpetas MÁS GRANDES hasta agotar el presupuesto, así el
        código fuente se abre hasta los módulos y las carpetas auxiliares quedan plegadas (›),
        y (3) el conteo es recursivo (archivos bajo la carpeta)."""
        tree = _build_tree(files)
        _compact(tree)
        expanded = _expand_by_size(tree, _MAX_MAP)
        lines: list[str] = []
        hidden = _render(tree, expanded, 0, lines)
        listing = "\n".join(lines)
        if len(listing) > _MAX_TREE_CHARS:
            listing = listing[:_MAX_TREE_CHARS].rsplit("\n", 1)[0]
        if hidden > 0:
            listing += (
                f"\n… (las carpetas marcadas con › tienen {hidden} subcarpetas más; "
                "usa listar_directorio/glob para verlas)"
            )
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
                "search_code_max": self._s.coder_search_code_max,
            },
            enabled=self._s.audit_enabled,
        )
        self._search_left = self._s.coder_search_code_max
        convo: list[dict[str, Any]] = [
            {"role": "system", "content": self._role.system_prompt + _TOOL_GUIDANCE},
            *await self._dict_context(task, rec),
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
        search_exhausted_noted = False
        empty_streak = 0  # respuestas vacías seguidas (ni texto ni tools)
        for _ in range(self._max_rounds):
            msg = await self._ollama.chat_tools(
                self._role.model,
                convo,
                self._tools_spec(),
                keep_alive=self._s.keep_alive,
                options={"num_ctx": self._s.coder_num_ctx} if self._s.coder_num_ctx else None,
                think=self._s.coder_reasoning or None,
            )
            content = msg.get("content", "")
            # gpt-oss devuelve su cadena de razonamiento aparte: guardarla hace la traza
            # mucho más útil para entender POR QUÉ eligió cada herramienta.
            if msg.get("thinking"):
                rec.thinking(steps, str(msg["thinking"]))
            tool_calls = msg.get("tool_calls") or []
            if not tool_calls:
                fallback = _extract_text_toolcall(content)
                if fallback is not None:
                    tool_calls = [fallback]
            rec.assistant(steps, content)
            if not tool_calls and not content.strip():
                # Respuesta VACÍA (ni texto ni tools; gpt-oss/Ollama lo hace a veces a mitad de
                # tarea). No es un cierre: se reintenta re-anclando la tarea. En la eval §6 v4
                # (13/09/2026) el harness lo tomó por "terminado" con respuesta "" y 0 edits.
                empty_streak += 1
                rec.note("respuesta_vacia", streak=empty_streak)
                if empty_streak <= _MAX_EMPTY:
                    convo.append(
                        {"role": "user", "content": _TASK_ANCHOR.format(task=task) + _EMPTY_NUDGE}
                    )
                    continue
                answer = last_answer or "(el modelo dejó de responder: respuestas vacías)"
                rec.run_end(answer, sorted(self._touched), steps, "empty_response")
                return {
                    "answer": answer,
                    "touched": sorted(self._touched),
                    "steps": steps,
                    "audit": str(rec.path) if rec.path else None,
                }
            empty_streak = 0
            if not tool_calls:
                if content:
                    last_answer = content
                if not reviewed:  # un pase de auto-revisión antes de cerrar
                    reviewed = True
                    convo.append({"role": "assistant", "content": content})
                    convo.append(
                        {"role": "user", "content": _TASK_ANCHOR.format(task=task) + _REVIEW_NUDGE}
                    )
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
                    if (
                        self._search_left == 0
                        and self._s.coder_search_code_max > 0
                        and not search_exhausted_noted
                    ):
                        # Cupo recién agotado: a partir de aquí la tool ya no se ofrece. Queda
                        # en la traza para explicar por qué desaparece del set.
                        search_exhausted_noted = True
                        rec.note("search_code_agotado", max=self._s.coder_search_code_max)
                    elif result.startswith("search_code "):
                        rec.note("search_code_redirigido_a_grep", consulta=args.get("consulta"))
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
                convo.append(
                    {"role": "user", "content": _TASK_ANCHOR.format(task=task) + _SEARCH_LOOP_NUDGE}
                )
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
