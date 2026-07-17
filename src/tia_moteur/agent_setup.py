"""Chargement et validation du fichier de politique de l'agent."""

from pathlib import Path
from typing import Literal
from copy import deepcopy
from collections.abc import Mapping

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator


class CommandRule(BaseModel):
    """Restrictions supplémentaires propres à un exécutable."""

    model_config = ConfigDict(extra="forbid")

    allowed_subcommands: list[str] | None = None
    forbidden_arguments: list[str] = Field(default_factory=list)
    forbidden_argument_prefixes: list[str] = Field(default_factory=list)


class BashToolSetup(BaseModel):
    """Politique déclarative du tool ``run_bash``."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    execution_mode: Literal["direct", "unrestricted_shell"] = "direct"
    shell: Path = Path("/bin/zsh")
    allowed_executables: list[str] = Field(default_factory=list)
    forbidden_executables: list[str] = Field(default_factory=list)
    command_rules: dict[str, CommandRule] = Field(default_factory=dict)

    @field_validator("shell")
    @classmethod
    def validate_shell(cls, value: Path) -> Path:
        shell = value.expanduser().resolve()
        if not shell.is_file():
            raise ValueError(f"Le shell n'existe pas: {shell}")
        return shell


class ToolsSetup(BaseModel):
    model_config = ConfigDict(extra="forbid")

    bash: BashToolSetup


class SkillsSetup(BaseModel):
    """Activation globale du système de skills."""

    model_config = ConfigDict(extra="forbid")

    enabled: bool = True


class AgentSetup(BaseModel):
    """Schéma racine de ``agent.setup.yaml``."""

    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    skills: SkillsSetup = Field(default_factory=SkillsSetup)
    tools: ToolsSetup


def load_agent_setup(path: Path) -> AgentSetup:
    """Charge un setup YAML et échoue fermement s'il est absent ou invalide."""
    setup_path = path.expanduser().resolve()
    if not setup_path.is_file():
        raise FileNotFoundError(f"Fichier de setup introuvable: {setup_path}")

    raw_setup = yaml.safe_load(setup_path.read_text(encoding="utf-8"))
    return AgentSetup.model_validate(raw_setup)


def _load_setup_mapping(path: Path) -> dict:
    """Charge une couche YAML sous forme de mapping."""
    try:
        raw_setup = yaml.safe_load(path.read_text(encoding="utf-8"))
    except UnicodeDecodeError as exc:
        raise ValueError(f"Le setup TIA n'est pas en UTF-8: {path}") from exc
    if not isinstance(raw_setup, dict):
        raise ValueError(f"Le setup TIA doit être un objet YAML: {path}")
    return raw_setup


def _deep_merge(base: Mapping, override: Mapping) -> dict:
    """Fusionne récursivement les mappings et remplace les autres valeurs."""
    merged = deepcopy(dict(base))
    for key, value in override.items():
        current = merged.get(key)
        if isinstance(current, Mapping) and isinstance(value, Mapping):
            merged[key] = _deep_merge(current, value)
        else:
            merged[key] = deepcopy(value)
    return merged


def load_layered_agent_setup(
    global_path: Path,
    project_path: Path | None = None,
) -> AgentSetup:
    """Charge le setup global puis applique la couche projet optionnelle."""
    resolved_global = global_path.expanduser().resolve()
    if not resolved_global.is_file():
        raise FileNotFoundError(
            f"Setup global TIA introuvable: {resolved_global}. Lance `tia init`."
        )

    merged = _load_setup_mapping(resolved_global)
    if project_path is not None:
        resolved_project = project_path.expanduser().resolve()
        if resolved_project != resolved_global and resolved_project.exists():
            if not resolved_project.is_file():
                raise ValueError(
                    f"Le setup projet n'est pas un fichier: {resolved_project}"
                )
            merged = _deep_merge(merged, _load_setup_mapping(resolved_project))

    return AgentSetup.model_validate(merged)
