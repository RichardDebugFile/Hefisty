# Desarrollo — Fase 2 (Núcleo mínimo)

Cómo levantar y trabajar el MVP en la máquina de desarrollo.

## Requisitos
- Python 3.12, [uv](https://docs.astral.sh/uv/) para el entorno.
- [Ollama](https://ollama.com) nativo en el host (Windows).
- Docker Desktop (Redis + Qdrant).
- [pnpm](https://pnpm.io) para el front (gestor seguro: cooldown de publicación y sin
  lifecycle scripts por defecto).

## Puesta en marcha
```powershell
pwsh -File scripts/setup.ps1        # verifica Ollama, baja modelos, levanta Redis+Qdrant
copy .env.example .env              # ajusta si hace falta (token, rutas)
uv sync --all-extras                # entorno Python
uv run hefisty serve                # gateway con la config (bind a HEFISTY_HOST; avisa si expone LAN sin token)
```
Alternativa cruda: `uv run uvicorn hefisty.gateway.app:app --host 127.0.0.1 --port 8080`.
Front:
```powershell
cd web; pnpm install; pnpm build    # genera web/dist que el gateway sirve en /
```
CLI:
```powershell
uv run hefisty status               # servicios y modelos cargados
uv run hefisty ask "hola, ¿quién eres?"
uv run hefisty sessions
uv run hefisty resume <id>
```

## Estructura del código (Fase 2)
| Módulo | Rol |
|---|---|
| `config.py` | Settings desde `.env` (sin dependencias extra). |
| `ollama_client.py` | Cliente async de la API nativa de Ollama (chat, streaming, keep_alive). |
| `orchestrator/sessions.py` | Sesiones SQLite (historial gzip). |
| `orchestrator/core.py` | Frontal: decide reply/delegate (JSON estructurado), streaming, cache, persistencia. |
| `orchestrator/router.py` | Política "un solo modelo grande en VRAM". |
| `cache.py` | Cache L1 exacta (Redis, hash SHA-256 del contexto). |
| `agents/coder.py` + `agents/tools.py` | Coder (streaming) + herramientas de archivo sandbox. |
| `gateway/app.py` | FastAPI: API OpenAI-compat + SSE, auth, sesiones, sirve el front. |
| `cli.py` | Cliente Typer del gateway. |
| `roles/` | Manifiestos declarativos (identidad de Hefisty, rol Coder). |

## Decisiones técnicas y desviaciones del diseño
1. **Ollama directo (httpx) en vez de LiteLLM en Fase 2.** Menos setup y control total del
   streaming/keep_alive. LiteLLM queda como extra opcional (`pip install hefisty[serving]`),
   no en el runtime por defecto; se puede intercalar más tarde sin tocar los agentes
   (hablan HTTP a un endpoint OpenAI-compat).
2. **Herramientas del Coder listas pero sin bucle agéntico autónomo.** `leer/escribir/listar`
   están implementadas, testeadas y registradas en `role.yaml`; el bucle buscar→leer→editar
   se integra en Fase 3 (coherente con el ROADMAP: navegación de código = Fase 3). En Fase 2
   el Coder responde en streaming.
3. **Cache L1 exacta por contexto completo.** La clave es el hash de la lista de mensajes; una
   petición idéntica (p. ej. el mismo primer turno en una sesión nueva) da hit sin llamar al
   modelo.
4. **Sesiones SQLite stdlib (sync)** invocadas desde el código async vía `asyncio.to_thread`;
   suficiente para un usuario local y sin dependencias nuevas.
5. **Auth Bearer opcional en dev.** Token vacío ⇒ auth desactivada, ya que el gateway solo
   escucha en `127.0.0.1`. En uso real se define `HEFISTY_API_TOKEN`.
6. **Servidor autoritativo de historial.** El front envía `messages` completos + `session_id`;
   el gateway toma solo el último mensaje de usuario y reconstruye el contexto desde la sesión
   persistida (evita duplicación).
7. **`httpx` movido a dependencia de runtime** (lo usan el cliente de Ollama y la CLI).
