"""Carga de manifiestos de rol (`role.yaml`) y de la identidad de Hefisty.

Un rol es una carpeta declarativa bajo `roles/` (ver docs/ROLES.md). Agregar un rol
no toca este código: basta el manifiesto + su prompt.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import yaml

from .config import REPO_ROOT

ROLES_DIR = REPO_ROOT / "roles"


@dataclass
class Role:
    name: str
    description: str
    model: str
    system_prompt: str
    triggers: list[str] = field(default_factory=list)
    tools: list[str] = field(default_factory=list)


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
    )


def load_identity() -> str:
    return (ROLES_DIR / "hefisty" / "prompts" / "identity.md").read_text(encoding="utf-8")
