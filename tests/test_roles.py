"""Tests de creación dinámica de roles (Fase 4, criterio 4 - scaffolding)."""

import pytest
import yaml

from hefisty.roles import Role, create_role, load_role


def test_create_role_writes_valid_package(tmp_path):
    base = create_role(
        "finanzas",
        "Organiza las finanzas del mes",
        model="gpt-oss:20b",
        triggers=["gasto", "presupuesto"],
        roles_dir=tmp_path,
    )
    manifest = yaml.safe_load((base / "role.yaml").read_text(encoding="utf-8"))
    assert manifest["name"] == "finanzas"
    assert manifest["model"] == "gpt-oss:20b"
    assert manifest["knowledge"]["collection"] == "finanzas"
    assert manifest["triggers"] == ["gasto", "presupuesto"]
    assert (base / "prompts" / "finanzas.md").is_file()
    # load_role (con roles_dir por defecto no aplica aquí) — validamos la forma a mano.
    role = Role(
        name=manifest["name"],
        description=manifest["description"],
        model=manifest["model"],
        system_prompt=(base / manifest["system_prompt"]).read_text(encoding="utf-8"),
        triggers=manifest["triggers"],
        tools=manifest["tools"],
        collection=manifest["knowledge"]["collection"],
    )
    assert "finanzas" in role.system_prompt
    assert role.tools  # trae el set de herramientas por defecto


def test_create_role_rejects_system_dupes_and_bad_names(tmp_path):
    with pytest.raises(ValueError):
        create_role("coder", "x", model="m", roles_dir=tmp_path)  # rol del sistema
    with pytest.raises(ValueError):
        create_role("Nombre Inválido", "x", model="m", roles_dir=tmp_path)  # espacios/mayúsculas
    create_role("nuevo", "x", model="m", roles_dir=tmp_path)
    with pytest.raises(FileExistsError):
        create_role("nuevo", "otra", model="m", roles_dir=tmp_path)  # ya existe


def test_load_role_roundtrip_default_dir():
    # Los roles del sistema cargan con el load_role real (roles_dir por defecto).
    assert load_role("coder").name == "coder"
