# Conocimiento (RAG) — ingesta y diccionarios

La capa de conocimiento indexa documentación y código en **Qdrant** (una colección por
diccionario) y el Coder la recupera durante la generación (top-k con umbral, citando la
fuente). Los embeddings se calculan con `nomic-embed-text` vía Ollama.

## Formatos soportados

| Extensión | Trato |
|---|---|
| `.md`, `.markdown` | troceo por headers (`#`..`######`) |
| `.html`, `.htm` | se extrae el texto (sin script/style) |
| `.pdf` | texto extraído con pypdf |
| código (`.py`, `.kt`, `.java`, `.ts`, …) | troceo por tamaño con solape |
| `.txt` | texto plano |

El troceo apunta a ~512 tokens con solape de 64 (aprox. por caracteres; configurable con
`HEFISTY_CHUNK_TOKENS` / `HEFISTY_CHUNK_OVERLAP`).

## Ingesta

Coloca las fuentes en un directorio (no se commitean: viven bajo `data/`, gitignoreado) y:

```powershell
hefisty knowledge ingest <coleccion> --path <directorio>
hefisty knowledge status              # lista colecciones y su conteo de chunks
hefisty knowledge delete <coleccion>  # borra una colección
```

La reingesta es idempotente (IDs deterministas por fuente+chunk): reejecutar actualiza
sin duplicar.

## Diccionario Kotlin/Android

Fuentes en `data/knowledge_sources/kotlin/` (semillas `00-05` + secciones `10-`..`70-`
construidas desde documentación oficial). Ingesta:

```powershell
hefisty knowledge ingest kotlin --path data/knowledge_sources/kotlin
```

**Detección de lenguaje:** si la petición menciona Kotlin/Android/Compose/Jetpack/Gradle
(o un archivo `.kt`), el Coder consulta la colección `kotlin` además de la colección
transversal `patrones` si existe. Ver `docs/ROLES.md` (Plan de diccionarios).

## Índice semántico del repo

Para que el Coder localice código por descripción vaga:

```powershell
hefisty index .        # embebe el repo a la colección repo__<nombre> (incremental por mtime)
```

Reindexar solo procesa archivos cuyo `mtime` cambió (manifiesto en `data/repo_index/`).

## Parámetros de retrieval

- `HEFISTY_RETRIEVAL_K` (def. 6) — número de chunks recuperados.
- `HEFISTY_RETRIEVAL_SCORE_MIN` (def. 0.4) — umbral de score (coseno) por debajo del cual
  se descarta un chunk.

Con `hefisty ask -v "..."` se ven las fuentes recuperadas (archivo, sección, score).
