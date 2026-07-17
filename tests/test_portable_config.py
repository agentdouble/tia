"""Tests de la configuration globale portable et de la couche projet."""

from pathlib import Path

from tia_moteur.agent_setup import load_layered_agent_setup
from tia_moteur.config import Settings
from tia_moteur.portable_config import (
    build_runtime_paths,
    compose_environment,
    find_project_root,
    get_config_home,
    initialize_global_config,
    resolve_workspace,
)


def test_initializes_global_config_without_overwriting(tmp_path: Path) -> None:
    config_home = tmp_path / ".config" / "tia"

    first = initialize_global_config(config_home)
    env_file = config_home / ".env"
    env_file.write_text("OPENAI_API_KEY=personnelle\n", encoding="utf-8")
    second = initialize_global_config(config_home)

    assert first.root == config_home
    assert env_file in first.created
    assert (config_home / "agent.setup.yaml").is_file()
    assert (config_home / "skills").is_dir()
    assert second.created == ()
    assert env_file.read_text(encoding="utf-8") == "OPENAI_API_KEY=personnelle\n"


def test_config_home_can_be_overridden_for_isolated_installations(
    tmp_path: Path,
) -> None:
    configured = tmp_path / "tia-home"

    assert get_config_home({"TIA_CONFIG_HOME": str(configured)}) == configured


def test_finds_nearest_project_root_from_nested_directory(tmp_path: Path) -> None:
    project = tmp_path / "project"
    nested = project / "src" / "module"
    nested.mkdir(parents=True)
    (project / "AGENTS.md").write_text("Instructions", encoding="utf-8")

    assert find_project_root(nested) == project


def test_keeps_current_directory_when_no_project_marker(tmp_path: Path) -> None:
    workspace = tmp_path / "libre"
    workspace.mkdir()

    assert find_project_root(workspace) == workspace


def test_environment_precedence_is_process_then_project_then_global() -> None:
    effective = compose_environment(
        {"SHARED": "global", "GLOBAL_ONLY": "oui"},
        {"SHARED": "project", "PROJECT_ONLY": "oui"},
        {"SHARED": "process", "PROCESS_ONLY": "oui"},
    )

    assert effective == {
        "SHARED": "process",
        "GLOBAL_ONLY": "oui",
        "PROJECT_ONLY": "oui",
        "PROCESS_ONLY": "oui",
    }


def test_workspace_precedence_and_auto_detection(tmp_path: Path) -> None:
    detected = tmp_path / "detected"
    detected.mkdir()
    explicit = tmp_path / "explicit"
    explicit.mkdir()
    from_process = tmp_path / "process"
    from_process.mkdir()
    from_global = tmp_path / "global"
    from_global.mkdir()

    assert resolve_workspace(
        explicit,
        {"TIA_WORKSPACE": str(from_process)},
        {"TIA_WORKSPACE": str(from_global)},
        detected,
    ) == explicit
    assert resolve_workspace(
        None,
        {"TIA_WORKSPACE": str(from_process)},
        {"TIA_WORKSPACE": str(from_global)},
        detected,
    ) == from_process
    assert resolve_workspace(
        None,
        {},
        {"TIA_WORKSPACE": str(from_global)},
        detected,
    ) == from_global
    assert resolve_workspace(None, {}, {}, detected) == detected


def test_layered_setup_applies_partial_project_override(tmp_path: Path) -> None:
    config_home = tmp_path / ".config" / "tia"
    initialize_global_config(config_home)
    project_setup = tmp_path / "agent.setup.yaml"
    project_setup.write_text(
        "skills:\n"
        "  enabled: false\n"
        "tools:\n"
        "  bash:\n"
        "    enabled: false\n",
        encoding="utf-8",
    )

    setup = load_layered_agent_setup(
        config_home / "agent.setup.yaml",
        project_setup,
    )

    assert setup.version == 1
    assert setup.skills.enabled is False
    assert setup.tools.bash.enabled is False
    assert setup.tools.bash.execution_mode == "unrestricted_shell"


def test_runtime_paths_are_rooted_in_config_home_and_project(
    tmp_path: Path,
) -> None:
    config_home = tmp_path / "config" / "tia"
    workspace = tmp_path / "project"
    workspace.mkdir()
    settings = Settings(workspace=workspace)

    paths = build_runtime_paths(
        config_home,
        workspace,
        setup_file=settings.setup_file,
        instructions_file=settings.instructions_file,
        skills_directory=settings.skills_directory,
        global_skills_directory=config_home / "skills",
    )

    assert paths.global_setup == config_home / "agent.setup.yaml"
    assert paths.global_instructions == config_home / "AGENTS.md"
    assert paths.global_skills == config_home / "skills"
    assert paths.project_setup == workspace / "agent.setup.yaml"
    assert paths.project_instructions == workspace / "AGENTS.md"
    assert paths.project_skills == workspace / ".agents" / "skills"


def test_settings_does_not_load_dotenv_from_arbitrary_cwd(
    tmp_path: Path,
    monkeypatch,
) -> None:
    (tmp_path / ".env").write_text("TIA_MODEL=modele-indesirable\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("TIA_MODEL", raising=False)

    assert Settings().model == "openai-responses:gpt-5.6-luna"
