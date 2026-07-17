"""Configuration portable globale et détection optionnelle d'un projet TIA."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import os
from pathlib import Path

from dotenv import dotenv_values


PROJECT_MARKERS = ("agent.setup.yaml", "AGENTS.md", ".agents")

DEFAULT_GLOBAL_ENV = """# Configuration globale de TIA
# Renseigne la clé ici pour utiliser `tia` hors d'un projet configuré.
OPENAI_API_KEY=
TIA_MODEL=openai-responses:gpt-5.6-luna
"""

DEFAULT_GLOBAL_SETUP = """version: 1

skills:
  enabled: true

tools:
  bash:
    enabled: true
    execution_mode: unrestricted_shell
    shell: /bin/zsh
"""


@dataclass(frozen=True)
class GlobalConfigInitialization:
    """Résultat idempotent de l'initialisation de ``~/.config/tia``."""

    root: Path
    created: tuple[Path, ...]


@dataclass(frozen=True)
class RuntimePaths:
    """Chemins globaux et projet résolus pour une exécution de TIA."""

    config_home: Path
    workspace: Path
    global_env: Path
    project_env: Path
    global_setup: Path
    project_setup: Path
    global_instructions: Path
    project_instructions: Path
    global_skills: Path
    project_skills: Path


def get_config_home(environment: Mapping[str, str] | None = None) -> Path:
    """Retourne la maison globale de TIA, surchargeable pour les tests."""
    source = os.environ if environment is None else environment
    configured = source.get("TIA_CONFIG_HOME")
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path.home() / ".config" / "tia").resolve()


def initialize_global_config(
    config_home: Path | None = None,
) -> GlobalConfigInitialization:
    """Crée les fichiers globaux manquants sans écraser la configuration."""
    root = (config_home or get_config_home()).expanduser().resolve()
    created: list[Path] = []

    if not root.exists():
        root.mkdir(parents=True, mode=0o700)
        created.append(root)
    elif not root.is_dir():
        raise NotADirectoryError(f"La configuration TIA n'est pas un dossier: {root}")

    env_file = root / ".env"
    if not env_file.exists():
        env_file.write_text(DEFAULT_GLOBAL_ENV, encoding="utf-8")
        env_file.chmod(0o600)
        created.append(env_file)

    setup_file = root / "agent.setup.yaml"
    if not setup_file.exists():
        setup_file.write_text(DEFAULT_GLOBAL_SETUP, encoding="utf-8")
        created.append(setup_file)

    skills_directory = root / "skills"
    if not skills_directory.exists():
        skills_directory.mkdir()
        created.append(skills_directory)
    elif not skills_directory.is_dir():
        raise NotADirectoryError(
            f"La racine globale des skills n'est pas un dossier: {skills_directory}"
        )

    return GlobalConfigInitialization(root=root, created=tuple(created))


def find_project_root(start: Path) -> Path:
    """Trouve le projet le plus proche, sinon conserve le dossier courant."""
    current = start.expanduser().resolve()
    if not current.is_dir():
        raise NotADirectoryError(f"Le workspace n'est pas un dossier: {current}")

    for candidate in (current, *current.parents):
        if any((candidate / marker).exists() for marker in PROJECT_MARKERS):
            return candidate
    return current


def read_env_file(path: Path) -> dict[str, str]:
    """Lit un fichier dotenv optionnel sans modifier le processus."""
    if not path.is_file():
        return {}
    return {
        key: value
        for key, value in dotenv_values(path).items()
        if value is not None
    }


def compose_environment(
    global_env: Mapping[str, str],
    project_env: Mapping[str, str],
    process_env: Mapping[str, str],
) -> dict[str, str]:
    """Applique la priorité processus > projet > global."""
    return {**global_env, **project_env, **process_env}


def resolve_workspace(
    explicit_workspace: Path | None,
    process_env: Mapping[str, str],
    global_env: Mapping[str, str],
    current_directory: Path | None = None,
) -> Path:
    """Résout le workspace explicite, configuré globalement ou auto-détecté."""
    if explicit_workspace is not None:
        return explicit_workspace.expanduser().resolve()

    configured = process_env.get("TIA_WORKSPACE") or global_env.get("TIA_WORKSPACE")
    if configured:
        return Path(configured).expanduser().resolve()

    return find_project_root(current_directory or Path.cwd())


def resolve_project_path(workspace: Path, configured_path: Path) -> Path:
    """Résout un fichier de projet relativement au workspace et non au shell."""
    path = configured_path.expanduser()
    if not path.is_absolute():
        path = workspace / path
    return path.resolve()


def build_runtime_paths(
    config_home: Path,
    workspace: Path,
    *,
    setup_file: Path,
    instructions_file: Path,
    skills_directory: Path,
    global_skills_directory: Path,
) -> RuntimePaths:
    """Construit les emplacements effectifs des deux couches de configuration."""
    return RuntimePaths(
        config_home=config_home,
        workspace=workspace,
        global_env=config_home / ".env",
        project_env=workspace / ".env",
        global_setup=config_home / "agent.setup.yaml",
        project_setup=resolve_project_path(workspace, setup_file),
        global_instructions=config_home / "AGENTS.md",
        project_instructions=resolve_project_path(workspace, instructions_file),
        global_skills=global_skills_directory.expanduser().resolve(),
        project_skills=resolve_project_path(workspace, skills_directory),
    )
