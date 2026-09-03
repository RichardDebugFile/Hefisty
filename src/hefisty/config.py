"""Configuración central de Hefisty.

Carga un `.env` local (gitignoreado) sin dependencias extra y expone `Settings`.
Ningún secreto se hardcodea: todo viene del entorno / `.env`.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from pydantic import BaseModel

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_dotenv(path: Path) -> None:
    """Parser mínimo de `.env` (KEY=VALUE por línea).

    Ignora comentarios y líneas vacías y NO sobreescribe variables ya presentes
    en el entorno (el entorno real tiene prioridad).
    """
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(REPO_ROOT / ".env")


class Settings(BaseModel):
    """Configuración inmutable del proceso, construida desde el entorno."""

    # Red del gateway.
    host: str = "127.0.0.1"
    port: int = 8080
    api_token: str = ""

    # Servicios.
    ollama_url: str = "http://127.0.0.1:11434"
    redis_url: str = "redis://127.0.0.1:6379/0"
    qdrant_url: str = "http://127.0.0.1:6333"

    # Modelos.
    model_frontal: str = "qwen3:1.7b"
    model_coder: str = "gpt-oss:20b"
    model_embed: str = "nomic-embed-text"
    keep_alive: str = "10m"
    # Ventana de contexto del Coder en el bucle agéntico. Acotarla evita que el KV-cache
    # crezca sin límite y provoque OOM (500) en GPUs de VRAM justa (16 GB con modelo de ~13 GB):
    # Ollama trunca el contexto viejo en vez de fallar. 0 = usar el default del modelo.
    coder_num_ctx: int = 8192
    # Esfuerzo de razonamiento del Coder en modelos que lo soportan (gpt-oss: low|medium|high).
    # Va como campo `think` de la petición. Cadena vacía = no enviarlo (default del modelo).
    coder_reasoning: str = "high"
    # Tope de rondas del bucle agéntico. Las tareas multi-paso (leer evidencia + localizar +
    # editar) necesitan más presupuesto que un fix puntual. HEFISTY_CODER_MAX_ROUNDS.
    coder_max_rounds: int = 20
    # Descargar el modelo grande (Coder) de VRAM al terminar el turno/cadena, en vez de
    # dejarlo residente `keep_alive` minutos ocupando ~13 GB sin trabajar. Con presupuesto
    # de VRAM ajustado (15.46/16) conviene True; recarga en 1-3 s desde el page cache de RAM
    # (48 GB). Configurable con HEFISTY_UNLOAD_CODER (0 lo mantiene residente).
    unload_coder_after_turn: bool = True

    # Directorios locales.
    workspace_dir: Path = REPO_ROOT / "workspace"
    data_dir: Path = REPO_ROOT / "data"
    # Traza estructurada (JSONL) de cada corrida del Coder agéntico, bajo data_dir/agent_runs.
    # Permite auditar qué buscó/leyó/editó y por qué paró. HEFISTY_AUDIT=0 la apaga.
    audit_enabled: bool = True

    # Cache L1 (segundos) y límites.
    cache_ttl_chat: int = 3600
    cache_ttl_code: int = 600
    max_input_chars: int = 32000

    # Conocimiento (RAG).
    chunk_tokens: int = 512
    chunk_overlap: int = 64
    retrieval_k: int = 6
    retrieval_score_min: float = 0.4
    # Colecciones extra que el Coder consulta SIEMPRE, además de [lenguaje, patrones].
    # Para diccionarios por proyecto (p. ej. el repo objetivo). Coma-separado en el entorno.
    extra_collections: list[str] = []
    # Umbral de similitud para la cache semántica. 0.80 calibrado para nomic-embed-text
    # (parafraseos ~0.84, no-relacionados ~0.69); el 0.95 del diseño es irreal para este
    # modelo. Configurable con HEFISTY_SEMANTIC_THRESHOLD.
    semantic_threshold: float = 0.80

    # Memoria de largo plazo (Fase 4).
    memory_recall_k: int = 4  # recuerdos inyectados por turno
    memory_score_min: float = 0.55  # umbral de similitud para recuperar un recuerdo
    memory_dedup_min: float = 0.80  # por encima => se considera el mismo recuerdo (actualiza)
    memory_confidence_min: float = 0.6  # confianza mínima del frontal para memorizar
    memory_consolidate_every: int = 6  # turnos entre consolidaciones
    memory_decay_days: int = 30  # sin uso y más viejo que esto => se archiva

    @property
    def knowledge_dir(self) -> Path:
        return self.data_dir / "knowledge_sources"

    @property
    def db_path(self) -> Path:
        return self.data_dir / "sessions.db"

    @property
    def web_dist(self) -> Path:
        return REPO_ROOT / "web" / "dist"

    @classmethod
    def from_env(cls) -> Settings:
        e = os.environ.get
        return cls(
            host=e("HEFISTY_HOST", "127.0.0.1"),
            port=int(e("HEFISTY_PORT", "8080")),
            api_token=e("HEFISTY_API_TOKEN", ""),
            ollama_url=e("HEFISTY_OLLAMA_URL", "http://127.0.0.1:11434"),
            redis_url=e("HEFISTY_REDIS_URL", "redis://127.0.0.1:6379/0"),
            qdrant_url=e("HEFISTY_QDRANT_URL", "http://127.0.0.1:6333"),
            model_frontal=e("HEFISTY_MODEL_FRONTAL", "qwen3:1.7b"),
            model_coder=e("HEFISTY_MODEL_CODER", "gpt-oss:20b"),
            model_embed=e("HEFISTY_MODEL_EMBED", "nomic-embed-text"),
            keep_alive=e("HEFISTY_KEEP_ALIVE", "10m"),
            coder_num_ctx=int(e("HEFISTY_CODER_NUM_CTX", "8192")),
            coder_reasoning=e("HEFISTY_CODER_REASONING", "high").strip(),
            coder_max_rounds=int(e("HEFISTY_CODER_MAX_ROUNDS", "20")),
            unload_coder_after_turn=e("HEFISTY_UNLOAD_CODER", "1").lower()
            not in ("0", "false", "no", "off"),
            workspace_dir=Path(e("HEFISTY_WORKSPACE_DIR", str(REPO_ROOT / "workspace"))),
            data_dir=Path(e("HEFISTY_DATA_DIR", str(REPO_ROOT / "data"))),
            audit_enabled=e("HEFISTY_AUDIT", "1").lower() not in ("0", "false", "no", "off"),
            cache_ttl_chat=int(e("HEFISTY_CACHE_TTL_CHAT", "3600")),
            cache_ttl_code=int(e("HEFISTY_CACHE_TTL_CODE", "600")),
            max_input_chars=int(e("HEFISTY_MAX_INPUT_CHARS", "32000")),
            chunk_tokens=int(e("HEFISTY_CHUNK_TOKENS", "512")),
            chunk_overlap=int(e("HEFISTY_CHUNK_OVERLAP", "64")),
            retrieval_k=int(e("HEFISTY_RETRIEVAL_K", "6")),
            retrieval_score_min=float(e("HEFISTY_RETRIEVAL_SCORE_MIN", "0.4")),
            extra_collections=[
                c.strip() for c in e("HEFISTY_EXTRA_COLLECTIONS", "").split(",") if c.strip()
            ],
            semantic_threshold=float(e("HEFISTY_SEMANTIC_THRESHOLD", "0.80")),
            memory_recall_k=int(e("HEFISTY_MEMORY_RECALL_K", "4")),
            memory_score_min=float(e("HEFISTY_MEMORY_SCORE_MIN", "0.55")),
            memory_dedup_min=float(e("HEFISTY_MEMORY_DEDUP_MIN", "0.80")),
            memory_confidence_min=float(e("HEFISTY_MEMORY_CONFIDENCE_MIN", "0.6")),
            memory_consolidate_every=int(e("HEFISTY_MEMORY_CONSOLIDATE_EVERY", "6")),
            memory_decay_days=int(e("HEFISTY_MEMORY_DECAY_DAYS", "30")),
        )


@lru_cache
def get_settings() -> Settings:
    """Settings cacheadas para todo el proceso."""
    return Settings.from_env()
