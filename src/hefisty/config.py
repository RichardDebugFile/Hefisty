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
    # Descargar el modelo grande (Coder) de VRAM al terminar el turno/cadena, en vez de
    # dejarlo residente `keep_alive` minutos ocupando ~13 GB sin trabajar. Con presupuesto
    # de VRAM ajustado (15.46/16) conviene True; recarga en 1-3 s desde el page cache de RAM
    # (48 GB). Configurable con HEFISTY_UNLOAD_CODER (0 lo mantiene residente).
    unload_coder_after_turn: bool = True

    # Directorios locales.
    workspace_dir: Path = REPO_ROOT / "workspace"
    data_dir: Path = REPO_ROOT / "data"

    # Cache L1 (segundos) y límites.
    cache_ttl_chat: int = 3600
    cache_ttl_code: int = 600
    max_input_chars: int = 32000

    # Conocimiento (RAG).
    chunk_tokens: int = 512
    chunk_overlap: int = 64
    retrieval_k: int = 6
    retrieval_score_min: float = 0.4
    # Umbral de similitud para la cache semántica. 0.80 calibrado para nomic-embed-text
    # (parafraseos ~0.84, no-relacionados ~0.69); el 0.95 del diseño es irreal para este
    # modelo. Configurable con HEFISTY_SEMANTIC_THRESHOLD.
    semantic_threshold: float = 0.80

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
            unload_coder_after_turn=e("HEFISTY_UNLOAD_CODER", "1").lower()
            not in ("0", "false", "no", "off"),
            workspace_dir=Path(e("HEFISTY_WORKSPACE_DIR", str(REPO_ROOT / "workspace"))),
            data_dir=Path(e("HEFISTY_DATA_DIR", str(REPO_ROOT / "data"))),
            cache_ttl_chat=int(e("HEFISTY_CACHE_TTL_CHAT", "3600")),
            cache_ttl_code=int(e("HEFISTY_CACHE_TTL_CODE", "600")),
            max_input_chars=int(e("HEFISTY_MAX_INPUT_CHARS", "32000")),
            chunk_tokens=int(e("HEFISTY_CHUNK_TOKENS", "512")),
            chunk_overlap=int(e("HEFISTY_CHUNK_OVERLAP", "64")),
            retrieval_k=int(e("HEFISTY_RETRIEVAL_K", "6")),
            retrieval_score_min=float(e("HEFISTY_RETRIEVAL_SCORE_MIN", "0.4")),
            semantic_threshold=float(e("HEFISTY_SEMANTIC_THRESHOLD", "0.80")),
        )


@lru_cache
def get_settings() -> Settings:
    """Settings cacheadas para todo el proceso."""
    return Settings.from_env()
