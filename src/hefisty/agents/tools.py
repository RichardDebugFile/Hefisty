"""Herramientas de archivo del Coder, restringidas al workspace configurado.

Toda ruta se resuelve y se valida contra el workspace: cualquier intento de salir
(`..`, rutas absolutas, symlinks que escapen) se rechaza. La ejecución de código
queda para la Fase 3.
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path, PureWindowsPath
from typing import Any


class ToolError(Exception):
    """Uso inválido de una herramienta (ruta fuera del workspace, no existe, etc.)."""


# Carpetas de ruido que grep/glob NO deben recorrer: control de versiones, artefactos de
# build y dependencias. Sin esto, grep entra a `.git/` (índice binario) y a `build/`, gasta
# rondas del Coder y ensucia los resultados con basura no-fuente.
_SKIP_DIRS = frozenset(
    {
        ".git",
        "node_modules",
        "build",
        "target",
        "dist",
        ".gradle",
        ".venv",
        "__pycache__",
        ".idea",
        ".next",
        "coverage",
        "bin",
        "out",
    }
)


def _skipped(rel: Path) -> bool:
    """True si la ruta relativa cae bajo una carpeta de ruido."""
    return any(part in _SKIP_DIRS for part in rel.parts)


# Extensiones de texto/código que grep recorre. Filtrar por extensión es MUCHO más barato
# que abrir cada archivo para olfatear binarios: en un repo de ~13k archivos, grep sobre la
# raíz pasó de ~90 s (abría todo) a <2 s. Los binarios (imágenes, .jar, `.git/index`) quedan
# fuera por no estar en la lista. El modelo aún puede grepear un archivo puntual por su ruta.
_TEXT_EXTS = frozenset(
    {
        ".kt",
        ".kts",
        ".java",
        ".py",
        ".pyi",
        ".xml",
        ".json",
        ".gradle",
        ".properties",
        ".ts",
        ".tsx",
        ".js",
        ".jsx",
        ".vue",
        ".md",
        ".txt",
        ".yaml",
        ".yml",
        ".sql",
        ".html",
        ".css",
        ".scss",
        ".sh",
        ".bat",
        ".cfg",
        ".ini",
        ".toml",
        ".pro",
        ".graphql",
        ".c",
        ".h",
        ".cpp",
        ".hpp",
        ".cs",
        ".go",
        ".rb",
        ".rs",
        ".php",
        ".swift",
        ".dart",
    }
)
# Tope de tamaño en el RECORRIDO (un archivo pedido por su ruta se lee siempre). Generoso a
# propósito: los logs de evidencia (logcat .txt), los bundles .js de web y los .json de labels
# pesan 1-3 MB y son justo lo que hay que leer para diagnosticar un flujo. Con el pre-filtro de
# una pasada, grepearlos es barato; saltárselos daba falsos "sin resultados".
_MAX_GREP_BYTES = 8 * 1024 * 1024


def _grep_candidate(rel: Path, abs_path: Path) -> bool:
    """True si el archivo debe entrar a grep: no es ruido, es de texto y no es gigante."""
    if _skipped(rel) or abs_path.suffix.lower() not in _TEXT_EXTS:
        return False
    try:
        return abs_path.stat().st_size <= _MAX_GREP_BYTES
    except OSError:
        return False


def _resolve(workspace: Path, rel: str) -> Path:
    # SANITIZADOR DE RUTAS: única puerta de entrada de toda ruta suministrada por el modelo.
    # Rechaza rutas absolutas/con unidad y, tras resolver symlinks, exige que el destino quede
    # DENTRO del workspace. Por eso los file-ops que usan su resultado están marcados # NOSONAR
    # (path injection): el path ya viene validado aquí, no directo del dato no confiable.
    # Rechaza de entrada rutas absolutas o con unidad (C:..., \\server, /etc): el Coder
    # solo direcciona con rutas relativas dentro del workspace.
    if Path(rel).is_absolute() or PureWindowsPath(rel).drive or PureWindowsPath(rel).is_absolute():
        raise ToolError(f"Ruta no relativa no permitida: {rel}")
    workspace = Path(workspace).resolve()
    target = (workspace / rel).resolve()  # NOSONAR: validada abajo contra el workspace
    if target != workspace and workspace not in target.parents:
        raise ToolError(f"Ruta fuera del workspace: {rel}")
    return target


def leer_archivo(workspace: Path, ruta: str) -> str:
    p = _resolve(workspace, ruta)
    if not p.is_file():
        raise ToolError(f"No existe el archivo: {ruta}")
    return p.read_text(encoding="utf-8")  # NOSONAR: path validado por _resolve()


def escribir_archivo(workspace: Path, ruta: str, contenido: str) -> str:
    p = _resolve(workspace, ruta)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(contenido, encoding="utf-8")  # NOSONAR: path validado por _resolve()
    return f"escrito: {ruta} ({len(contenido)} chars)"


def listar_directorio(workspace: Path, ruta: str = ".") -> list[str]:
    p, nota = _resolve_tolerant(workspace, ruta)
    if not p.is_dir():
        raise ToolError(f"No es un directorio: {ruta}")
    out = [nota] if nota else []
    return out + sorted(e.name + ("/" if e.is_dir() else "") for e in p.iterdir())  # NOSONAR


# --- Navegación de código (todas confinadas al workspace) ---


def _resolve_tolerant(workspace: Path, ruta: str) -> tuple[Path, str]:
    """`_resolve` + corrección de rutas a medias. El modelo escribe rutas PARCIALES que son un
    sufijo de la real (`app-mobile/src/main/java/.../pedidos` sin el módulo raíz), con
    comodines (`**/pedidos`) o solo el nombre de la carpeta/archivo. Si la ruta no existe
    y hay UNA sola carpeta/archivo del workspace cuya ruta relativa termina así, se usa esa y se
    avisa; si hay varias, se listan para que elija. Cada ronda perdida en "No existe la ruta"
    era una ronda menos para arreglar (eval §6 v3, 13/09/2026: 3 de 26)."""
    p = _resolve(workspace, ruta)
    if p.exists():
        return p, ""
    ws = Path(workspace).resolve()
    suffix = ruta.replace("\\", "/").strip("/")
    # Quitar comodines de cabecera (`**/`, `*/`) — la intención es "en algún sitio".
    suffix = re.sub(r"^(\*\*?/)+", "", suffix)
    if not suffix or any(ch in suffix for ch in "*?"):
        return p, ""
    parts = suffix.split("/")
    matches: list[Path] = []
    for root, dirs, filenames in os.walk(ws):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]
        rel_root = Path(root).relative_to(ws).as_posix()
        for name in dirs + filenames:
            full = f"{rel_root}/{name}" if rel_root != "." else name
            if full.split("/")[-len(parts) :] == parts:
                matches.append(ws / full)
    matches.sort()  # os.walk no ordena (Linux ≠ Windows): salida determinista
    if len(matches) == 1:
        real = matches[0].relative_to(ws).as_posix()
        return matches[0], f"(ruta corregida: '{ruta}' → '{real}')"
    if len(matches) > 1:
        opts = ", ".join(m.relative_to(ws).as_posix() for m in matches[:6])
        raise ToolError(f"No existe la ruta: {ruta}. ¿Quisiste decir una de estas? {opts}")
    return p, ""


def _inside(ws: Path, p: Path) -> bool:
    """True si `p` (tras resolver symlinks) sigue dentro del workspace."""
    rp = p.resolve()
    return rp == ws or ws in rp.parents


def _walk_files(base: Path, ws: Path) -> list[Path]:
    """Archivos bajo `base`, **podando en el recorrido** las carpetas de ruido.

    Clave para el rendimiento: `rglob("*")` enumera TODO y filtra después. En el repo objetivo
    eso son 91.708 rutas, de las cuales `build/` aporta 88.400 (96%). Podando con os.walk no
    se desciende siquiera a esas carpetas.
    """
    out: list[Path] = []
    for root, dirs, filenames in os.walk(base):
        dirs[:] = [d for d in dirs if d not in _SKIP_DIRS]  # poda in-place: no desciende
        rootp = Path(root)
        for fn in filenames:
            p = rootp / fn
            if _inside(ws, p):  # descarta symlinks que escapen del workspace
                out.append(p)
    return out


def _glob_to_regex(patron: str) -> re.Pattern[str]:
    """Traduce un patrón glob a regex sobre la ruta relativa posix.
    `**` cruza separadores, `*`/`?` no. Permite casar sin recorrer el árbol de nuevo."""
    parts = patron.split("/")
    chunks: list[str] = []
    for i, seg in enumerate(parts):
        last = i == len(parts) - 1
        if seg == "**":
            chunks.append(".*" if last else "(?:.*/)?")
            continue
        seg_re = ""
        for ch in seg:
            if ch == "*":
                seg_re += "[^/]*"
            elif ch == "?":
                seg_re += "[^/]"
            else:
                seg_re += re.escape(ch)
        chunks.append(seg_re if last else seg_re + "/")
    return re.compile("^" + "".join(chunks) + "$")


def _glob_once(ws: Path, patron: str, files: list[Path] | None = None) -> list[str]:
    try:
        rx = _glob_to_regex(patron)
    except re.error:
        return []
    if files is None:
        files = _walk_files(ws, ws)
    out: list[str] = []
    for p in files:
        rel = p.relative_to(ws).as_posix()
        if rx.match(rel):
            out.append(rel)
    return sorted(out)


def _glob_variants(patron: str) -> list[str]:
    """Variantes tolerantes del patrón. El modelo escribe patrones con semántica de shell
    (`**/dir/**`, `*dir*/*sub*`) que pathlib expande distinto y devuelve vacío en silencio,
    gastando rondas. Se reintenta con las formas que casi siempre son lo que quería."""
    alts: list[str] = []
    p = patron.rstrip("/")
    if p.endswith("/**"):  # "a/**" en pathlib no incluye los archivos: hace falta "/**/*"
        alts.append(p + "/*")
    if not p.startswith("**/"):  # buscar en cualquier profundidad, no solo en la raíz
        alts.append("**/" + p)
        if p.endswith("/**"):
            alts.append("**/" + p + "/*")
    if "/" not in p and not p.startswith("*"):  # nombre suelto -> por todo el árbol
        alts.append(f"**/*{p}*")
    return alts


def glob(workspace: Path, patron: str) -> list[str]:
    """Rutas de archivo (relativas) que casan el patrón glob dentro del workspace."""
    if ".." in patron or patron.startswith(("/", "\\")) or PureWindowsPath(patron).drive:
        raise ToolError(f"Patrón no permitido: {patron}")
    ws = Path(workspace).resolve()
    files = _walk_files(ws, ws)  # un solo recorrido podado, reutilizado por las variantes
    out = _glob_once(ws, patron, files)
    if out:
        return out
    for alt in _glob_variants(patron):  # reintento tolerante antes de rendirse
        out = _glob_once(ws, alt, files)
        if out:
            return out
    return []


# Tope de hits que se listan en detalle; por encima, grep devuelve un RESUMEN por archivo.
# Antes cortaba por líneas de salida (200) en orden de recorrido: con contexto=5 el modelo veía
# ~15 hits, todos de la primera carpeta alfabética (`cuentas/`), y los del módulo que buscaba
# (`pedidos/`) quedaban fuera del corte sin aviso (eval §6, 13/09/2026: `queryText`
# tenía 38 hits en 18 archivos, 13 en pedidos/, y el modelo nunca los vio).
_GREP_MAX_DETALLE = 30
_GREP_MAX_DETALLE_SIN_CONTEXTO = 80
_GREP_MAX_HITS = 3000
_GREP_MAX_ARCHIVOS_RESUMEN = 40


def _grep_resumen(hits: list[tuple[str, int, str]], truncado: bool) -> list[str]:
    """Vista `grep -c` enriquecida: archivos ordenados por nº de hits con sus líneas."""
    por_archivo: dict[str, list[int]] = {}
    for rel, i, _ in hits:
        por_archivo.setdefault(rel, []).append(i)
    n_files = len(por_archivo)
    out = [
        f"{len(hits)}{'+' if truncado else ''} coincidencias en {n_files} archivos: demasiadas "
        "para listarlas. Por archivo (líneas), de más a menos hits:"
    ]
    ordenados = sorted(por_archivo.items(), key=lambda kv: (-len(kv[1]), kv[0]))
    for rel, lineas in ordenados[:_GREP_MAX_ARCHIVOS_RESUMEN]:
        shown = ", ".join(str(x) for x in lineas[:8])
        extra = f", … (+{len(lineas) - 8})" if len(lineas) > 8 else ""
        out.append(f"  {rel}: {shown}{extra} [{len(lineas)}]")
    if n_files > _GREP_MAX_ARCHIVOS_RESUMEN:
        out.append(f"  … (+{n_files - _GREP_MAX_ARCHIVOS_RESUMEN} archivos más)")
    out.append(
        "Acota: `ruta=<carpeta del módulo>` (p. ej. la del enunciado), un identificador más "
        "específico, o `read_range`/`outline` directo en el archivo que te interese."
    )
    return out


def grep(
    workspace: Path,
    regex: str,
    ruta: str = ".",
    max_resultados: int = 0,
    contexto: int = 0,
) -> list[str]:
    """Líneas que casan `regex` bajo `ruta`. Formato: `archivo:linea: contenido`.

    Con `contexto=N` devuelve además las N líneas de alrededor de cada hit (como `grep -C`),
    para ver el código que rodea al match sin una segunda llamada a read_range: el modelo
    acertaba el archivo pero fallaba la ventana al leer.

    Si hay más hits que `max_resultados` (por defecto `_GREP_MAX_DETALLE*`), devuelve un
    RESUMEN por archivo (todos los archivos, ordenados por nº de hits) en vez de los primeros
    N en orden de recorrido: así el modelo ve en qué archivos/carpetas se concentra el término
    y acota, en lugar de quedarse con los de la primera carpeta alfabética."""
    if max_resultados <= 0:
        max_resultados = _GREP_MAX_DETALLE if contexto > 0 else _GREP_MAX_DETALLE_SIN_CONTEXTO
    base, nota = _resolve_tolerant(workspace, ruta)
    ws = Path(workspace).resolve()
    # Una ruta inexistente (typo del modelo) devolvía "(sin resultados)": una señal FALSA que le
    # hace concluir "aquí no hay nada" y seguir por mal camino. Debe fallar ruidosamente.
    if not base.exists():
        raise ToolError(f"No existe la ruta: {ruta}")
    try:
        pat = re.compile(regex)
    except re.error as exc:
        raise ToolError(f"Regex inválida: {exc}") from exc
    if base.is_file():
        # Archivo puntual pedido por su ruta: respétalo tal cual (el modelo eligió).
        files = [base]
    else:
        # Recorrido PODADO (no desciende a build/.git/…) + filtro por extensión y tamaño.
        files = [p for p in _walk_files(base, ws) if _grep_candidate(p.relative_to(ws), p)]
    # Pre-filtro: una sola pasada de regex sobre TODO el contenido (con MULTILINE, para que
    # ^/$ conserven la semántica por línea). Solo si hay match se parte en líneas. Sin esto
    # eran millones de `pat.search(line)` — un grep sin resultados sobre ~1700 .kt tardaba 75 s.
    try:
        prefilter = re.compile(regex, re.MULTILINE)
    except re.error:  # ya validada arriba; por si el flag cambia la compilación
        prefilter = pat
    # 1) Recoger TODOS los hits (rel, línea, texto) — barato gracias al pre-filtro — y las
    #    líneas de cada archivo con hits (para el contexto). Tope de seguridad en _GREP_MAX_HITS.
    hits: list[tuple[str, int, str]] = []
    lineas_por_archivo: dict[str, list[str]] = {}
    truncado = False
    for f in files:
        if not _inside(ws, f):  # symlink que escapa del workspace
            continue
        try:
            content = f.read_text(encoding="utf-8", errors="replace")  # NOSONAR
        except OSError:
            continue
        if not prefilter.search(content):
            continue
        rel = f.relative_to(ws).as_posix()
        lines = content.splitlines()
        lineas_por_archivo[rel] = lines
        for i, line in enumerate(lines, 1):
            if pat.search(line):
                hits.append((rel, i, line))
                if len(hits) >= _GREP_MAX_HITS:
                    truncado = True
                    break
        if truncado:
            break
    # 2) Muchos hits → resumen por archivo; pocos → detalle (con contexto si se pidió).
    if len(hits) > max_resultados:
        return ([nota] if nota else []) + _grep_resumen(hits, truncado)
    out: list[str] = [nota] if nota else []
    if nota and not hits:
        out.append("(sin resultados)")
    for rel, i, line in hits:
        if contexto > 0:
            lines = lineas_por_archivo[rel]
            lo, hi = max(1, i - contexto), min(len(lines), i + contexto)
            out.append(f"{rel}:{i}:")
            for j in range(lo, hi + 1):
                marca = ">" if j == i else " "
                out.append(f"  {marca}{j}: {lines[j - 1][:200]}")
        else:
            out.append(f"{rel}:{i}: {line.strip()[:200]}")
    return out


# Declaraciones de alto nivel (Kotlin/Java/Python/TS/JS/…): fun/class/object/interface/enum/def.
# Da el "esqueleto" de un archivo con sus números de línea sin leerlo entero: el modelo salta a
# la función correcta con read_range. Deliberadamente NO lista val/var (serían ruido de campos).
_DECL_RE = re.compile(
    r"^\s*(?:@[\w.]+\s*)*"
    r"(?:(?:public|private|protected|internal|open|final|abstract|sealed|data|inner|"
    r"companion|static|suspend|override|inline|operator|infix|export|external)\s+)*"
    r"(?:fun|class|object|interface|enum\s+class|enum|def|function)\b"
)


def outline(workspace: Path, ruta: str, max_items: int = 200) -> list[str]:
    """Esqueleto de un archivo: sus declaraciones (`linea: declaración`), sin leerlo completo."""
    p, nota = _resolve_tolerant(workspace, ruta)
    if not p.is_file():
        raise ToolError(f"No existe el archivo: {ruta}")
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()  # NOSONAR: _resolve()
    out: list[str] = [nota] if nota else []
    for i, line in enumerate(lines, 1):
        if _DECL_RE.match(line):
            out.append(f"{i}: {line.strip()[:160]}")
            if len(out) >= max_items:
                break
    return out


def _har_entries(p: Path) -> list[dict[str, Any]]:
    try:
        data = json.loads(p.read_text(encoding="utf-8", errors="replace"))  # NOSONAR: _resolve()
    except (json.JSONDecodeError, OSError) as exc:
        raise ToolError(f"No se pudo leer el HAR {p.name}: {exc}") from exc
    entries = data.get("log", {}).get("entries")
    if not isinstance(entries, list):
        raise ToolError(f"{p.name} no parece un HAR (falta log.entries)")
    return entries


def har(workspace: Path, ruta: str, filtro: str = "", indice: int | None = None) -> str:
    """Consulta un `.har` sin volcarlo (suelen pesar >1 MB: no caben en el contexto).

    - Sin `indice`: lista las peticiones `#i  METHOD status  url` (filtradas por `filtro`,
      subcadena sobre la URL).
    - Con `indice`: muestra esa petición en detalle — headers relevantes, query, body del
      request y principio de la respuesta. Es lo que hace falta para comparar web vs mobile.
    """
    p = _resolve(workspace, ruta)
    if not p.is_file():
        raise ToolError(f"No existe el archivo: {ruta}")
    entries = _har_entries(p)

    if indice is None:
        out: list[str] = []
        for i, e in enumerate(entries):
            req, res = e.get("request", {}), e.get("response", {})
            url = req.get("url", "")
            if filtro and filtro.lower() not in url.lower():
                continue
            out.append(f"#{i}  {req.get('method', '?')} {res.get('status', '?')}  {url[:180]}")
            if len(out) >= 200:
                out.append("… (más entradas; acota con `filtro`)")
                break
        total = len(entries)
        cab = f"{p.name}: {total} peticiones" + (f" · filtro={filtro!r}" if filtro else "")
        return cab + "\n" + ("\n".join(out) or "(ninguna casa el filtro)")

    if not 0 <= indice < len(entries):
        raise ToolError(f"indice fuera de rango (0..{len(entries) - 1})")
    e = entries[indice]
    req, res = e.get("request", {}), e.get("response", {})
    interesantes = {"component", "authorization", "content-type", "accept", "x-", "cookie"}

    def _heads(hs: list[dict[str, Any]]) -> list[str]:
        out = []
        for h in hs or []:
            n = str(h.get("name", "")).lower()
            if any(n.startswith(k) or n == k for k in interesantes):
                v = str(h.get("value", ""))
                out.append(f"    {h.get('name')}: {v[:120]}")
        return out

    partes = [
        f"#{indice}  {req.get('method', '?')} {res.get('status', '?')}  {req.get('url', '')}",
        "  request headers:",
        *_heads(req.get("headers", [])),
    ]
    qs = req.get("queryString") or []
    if qs:
        partes.append("  query: " + ", ".join(f"{q.get('name')}={q.get('value')}" for q in qs))
    body = (req.get("postData") or {}).get("text")
    if body:
        partes += ["  request body:", "    " + body[:1500]]
    rtext = (res.get("content") or {}).get("text")
    if rtext:
        partes += ["  response body (inicio):", "    " + rtext[:800]]
    return "\n".join(partes)


def read_range(workspace: Path, ruta: str, inicio: int, fin: int) -> str:
    """Lee las líneas [inicio, fin] (1-indexadas) con número de línea.

    Cierra con un pie: total de líneas del archivo y las 3 declaraciones que vienen DESPUÉS
    de la ventana. En la eval §6 v3 (13/09/2026) el modelo leyó 1-160 del archivo correcto y
    paró; la función causante empezaba en la 184. Con el pie la ve sin otra ronda."""
    p, nota = _resolve_tolerant(workspace, ruta)
    if not p.is_file():
        raise ToolError(f"No existe el archivo: {ruta}")
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines()  # NOSONAR: _resolve()
    inicio = max(1, inicio)
    fin = min(len(lines), fin)
    out = [nota] if nota else []
    out += [f"{i}: {lines[i - 1]}" for i in range(inicio, fin + 1)]
    siguientes = []
    for j in range(fin + 1, len(lines) + 1):
        if _DECL_RE.match(lines[j - 1]):
            siguientes.append(f"{j}: {lines[j - 1].strip()[:100]}")
            if len(siguientes) == 3:
                break
    pie = f"(archivo de {len(lines)} líneas"
    if siguientes:
        pie += "; declaraciones siguientes → " + " | ".join(siguientes)
    out.append(pie + ")")
    return "\n".join(out)


def _indent_of(line: str) -> str:
    return line[: len(line) - len(line.lstrip(" \t"))]


def _match_ignoring_indent(lines: list[str], old: str) -> list[int]:
    """Índices de línea donde `old` casa con el archivo comparando cada línea SIN su
    indentación ni espacios finales. Devuelve todos los inicios posibles."""
    old_lines = [ln.strip() for ln in old.strip("\r\n").splitlines()]
    if not old_lines or not any(old_lines):
        return []
    stripped = [ln.strip() for ln in lines]
    n = len(old_lines)
    return [i for i in range(len(lines) - n + 1) if stripped[i : i + n] == old_lines]


def _reindent(new: str, model_first_indent: str, file_first_indent: str) -> list[str]:
    """Reubica `new` a la indentación real del archivo conservando la indentación RELATIVA
    que el modelo dio a cada línea respecto a su primera línea del texto viejo."""
    out = []
    for ln in new.strip("\r\n").splitlines():
        ind = _indent_of(ln)
        if ind.startswith(model_first_indent):
            ind = file_first_indent + ind[len(model_first_indent) :]
        else:  # menos indentado que la 1ª línea (raro): mantener su indentación relativa
            dedent = len(model_first_indent) - len(ind)
            ind = file_first_indent[: max(0, len(file_first_indent) - dedent)]
        out.append(ind + ln.lstrip(" \t"))
    return out


def _echo_region(content: str, first_line: int, last_line: int, margin: int = 2) -> str:
    """Devuelve las líneas editadas (con `margin` de contexto) numeradas: el modelo VE lo que
    produjo. Un reemplazo de sub-línea puede dejar restos (`val ` huérfano al sustituir
    `firstEntry = …` por otra expresión, visto en la eval §2 v4) y sin eco no se entera."""
    lines = content.splitlines()
    a = max(1, first_line - margin)
    b = min(len(lines), last_line + margin)
    return "\n".join(f"{k}: {lines[k - 1]}" for k in range(a, b + 1))


def edit(workspace: Path, ruta: str, texto_viejo: str, texto_nuevo: str) -> str:
    """Reemplazo único verificable. Primero exacto; si no casa, tolera diferencias de
    INDENTACIÓN (el modelo reconstruye el sangrado a ojo desde `read_range`, que lleva el
    prefijo `NNNN: `, y falla 7 veces seguidas en el mismo bloque) reubicando el texto nuevo
    a la indentación real del archivo. Falla si el bloque no es único o no aparece, y en ese
    caso señala las líneas parecidas para que el modelo copie el texto exacto."""
    p = _resolve(workspace, ruta)
    if not p.is_file():
        raise ToolError(f"No existe el archivo: {ruta}")
    content = p.read_text(encoding="utf-8")  # NOSONAR: path validado por _resolve()
    n = content.count(texto_viejo)
    if n > 1:
        raise ToolError(f"Texto no único en {ruta} ({n} ocurrencias); añade contexto")
    if n == 1:
        pos = content.index(texto_viejo)
        new_content = content.replace(texto_viejo, texto_nuevo, 1)
        p.write_text(new_content, encoding="utf-8")  # NOSONAR: path validado por _resolve()
        first_line = content.count("\n", 0, pos) + 1
        last_line = first_line + texto_nuevo.count("\n")
        return f"editado: {ruta}\n" + _echo_region(new_content, first_line, last_line)

    lines = content.splitlines(keepends=True)
    starts = _match_ignoring_indent(lines, texto_viejo)
    if len(starts) > 1:
        raise ToolError(
            f"Texto no único en {ruta} (casa en las líneas {', '.join(str(s + 1) for s in starts)} "
            "ignorando indentación); añade contexto"
        )
    if len(starts) == 1:
        i = starts[0]
        count = len(texto_viejo.strip("\r\n").splitlines())
        block = lines[i : i + count]
        model_indent = _indent_of(texto_viejo.strip("\r\n").splitlines()[0])
        new_lines = _reindent(texto_nuevo, model_indent, _indent_of(block[0]))
        nl = "\r\n" if block[-1].endswith("\r\n") else "\n"
        replacement = nl.join(new_lines) + (nl if block[-1].endswith(("\n", "\r")) else "")
        new_content = "".join(lines[:i]) + replacement + "".join(lines[i + count :])
        p.write_text(new_content, encoding="utf-8")  # NOSONAR: path validado por _resolve()
        return f"editado: {ruta} (indentación ajustada al archivo)\n" + _echo_region(
            new_content, i + 1, i + len(new_lines)
        )

    # Nada casa: apuntar a las líneas del archivo que se parecen a la primera línea pedida
    # (o, si ni eso, a su primer identificador: el modelo suele haber juntado varias líneas).
    first = next((ln.strip() for ln in texto_viejo.splitlines() if ln.strip()), "")
    similar = [k for k, ln in enumerate(lines) if first and first in ln][:3]
    if not similar:
        m = re.search(r"[A-Za-z_]\w{3,}", first)
        token = m.group(0) if m else ""
        similar = [k for k, ln in enumerate(lines) if token and token in ln][:3]
    hint = ""
    if similar:
        hint = " Líneas parecidas: " + "; ".join(f"{k + 1}: {lines[k].rstrip()!r}" for k in similar)
        hint += ". Copia el texto EXACTO que devuelve read_range (sin el prefijo 'NNNN: ')."
    raise ToolError(f"Texto a reemplazar no encontrado en {ruta}.{hint}")
