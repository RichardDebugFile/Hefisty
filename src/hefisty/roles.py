"""Carga de manifiestos de rol (`role.yaml`) y de la identidad de Hefisty.

Un rol es una carpeta declarativa bajo `roles/` (ver docs/ROLES.md). Agregar un rol
no toca este código: basta el manifiesto + su prompt.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .config import REPO_ROOT

ROLES_DIR = REPO_ROOT / "roles"

# Roles del sistema: no se crean/editan desde la UI ni se pueden recrear.
SYSTEM_ROLES = {"hefisty", "coder", "revisor", "docs"}
_DEFAULT_TOOLS = ["glob", "grep", "read_range", "leer_archivo", "escribir_archivo", "edit"]


@dataclass
class Role:
    name: str
    description: str
    model: str
    system_prompt: str
    triggers: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)
    collection: str = ""


def load_role(name: str) -> Role:
    base = ROLES_DIR / name
    manifest = yaml.safe_load((base / "role.yaml").read_text(encoding="utf-8"))
    system = (base / manifest["system_prompt"]).read_text(encoding="utf-8")
    return Role(
        name=manifest["name"],
        description=manifest.get("description", ""),
        model=manifest["model"],
        system_prompt=system,
        triggers=manifest.get("triggers", []),
        tools=manifest.get("tools", []),
        collection=(manifest.get("knowledge") or {}).get("collection", ""),
    )


def list_roles() -> list[Role]:
    """Roles instalados (carpetas con role.yaml bajo roles/)."""
    if not ROLES_DIR.is_dir():
        return []
    return [load_role(d.name) for d in sorted(ROLES_DIR.iterdir()) if (d / "role.yaml").is_file()]


def load_identity() -> str:
    return (ROLES_DIR / "hefisty" / "prompts" / "identity.md").read_text(encoding="utf-8")


def _prompt_skeleton(slug: str, description: str) -> str:
    return (
        f"# Rol: {slug}\n\n{description}\n\n"
        "Eres un agente especializado de Hefisty. Trabaja con precisión, cita las fuentes "
        "de tu diccionario cuando las uses y aplica los cambios directamente en los archivos "
        "en vez de solo describirlos.\n"
    )


def create_role(
    name: str,
    description: str,
    *,
    model: str,
    triggers: list[str] | None = None,
    tools: list[str] | None = None,
    roles_dir: Path = ROLES_DIR,
) -> Path:
    """Crea el paquete declarativo de un rol nuevo (manifiesto + prompt esqueleto).

    La colección Qdrant del rol se crea sola en la primera ingesta (`hefisty knowledge
    ingest <col>`); el retrieval ya tolera que aún no exista. `list_roles` lee en fresco,
    así que el rol es visible sin reiniciar."""
    slug = name.strip().lower()
    if not re.fullmatch(r"[a-z][a-z0-9_-]*", slug):
        raise ValueError(f"nombre de rol inválido: {name!r} (usa minúsculas, dígitos, - o _)")
    if slug in SYSTEM_ROLES:
        raise ValueError(f"'{slug}' es un rol del sistema; no se puede recrear")
    base = roles_dir / slug
    if (base / "role.yaml").exists():
        raise FileExistsError(f"el rol '{slug}' ya existe")
    (base / "prompts").mkdir(parents=True, exist_ok=True)
    prompt_rel = f"prompts/{slug}.md"
    manifest = {
        "name": slug,
        "description": description,
        "triggers": triggers or [],
        "model": model,
        "system_prompt": prompt_rel,
        "knowledge": {"collection": slug, "sources": []},
        "tools": tools or list(_DEFAULT_TOOLS),
        "lora": None,
    }
    (base / "role.yaml").write_text(
        yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False), encoding="utf-8"
    )
    (base / prompt_rel).write_text(_prompt_skeleton(slug, description), encoding="utf-8")
    return base
