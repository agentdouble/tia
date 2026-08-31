"""Tests du chargement déterministe de ``AGENTS.md``."""

from pathlib import Path

import pytest
from pydantic_ai.messages import ModelRequest
from pydantic_ai.models.test import TestModel

from tia_moteur.agent import AGENT_INSTRUCTIONS, build_agent
from tia_moteur.config import Settings
from tia_moteur.skills import combine_runtime_instructions
from tia_moteur.workspace_instructions import (
    WorkspaceInstructionsError,
    WorkspaceInstructionsLoader,
)


def make_loader(
    workspace: Path,
    max_chars: int = 65_536,
) -> WorkspaceInstructionsLoader:
    return WorkspaceInstructionsLoader(
        workspace=workspace,
        instructions_file=Path("AGENTS.md"),
        max_chars=max_chars,
    )


def test_loads_and_wraps_workspace_instructions(tmp_path: Path) -> None:
    agents_file = tmp_path / "AGENTS.md"
    agents_file.write_text("# Règles\n\n- Utilise uv.\n", encoding="utf-8")

    loaded = make_loader(tmp_path).load()

    assert loaded is not None
    assert loaded.path == agents_file
    assert "Utilise uv" in loaded.content
    assert "début AGENTS.md" in loaded.as_prompt()


def test_missing_file_keeps_only_base_prompt(tmp_path: Path) -> None:
    assert make_loader(tmp_path).load() is None


def test_reloads_content_and_changes_digest(tmp_path: Path) -> None:
    agents_file = tmp_path / "AGENTS.md"
    agents_file.write_text("Version 1", encoding="utf-8")
    loader = make_loader(tmp_path)
    first = loader.load()

    agents_file.write_text("Version 2", encoding="utf-8")
    second = loader.load()

    assert first is not None and second is not None
    assert first.content == "Version 1"
    assert second.content == "Version 2"
    assert first.digest != second.digest


def test_empty_file_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("  \n", encoding="utf-8")

    assert make_loader(tmp_path).load() is None


def test_rejects_oversized_file(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text("x" * 1_001, encoding="utf-8")

    with pytest.raises(WorkspaceInstructionsError, match="dépasse"):
        make_loader(tmp_path, max_chars=1_000).load()


def test_rejects_symlink_outside_workspace(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside-agents.md"
    outside.write_text("instructions externes", encoding="utf-8")
    (tmp_path / "AGENTS.md").symlink_to(outside)

    try:
        with pytest.raises(WorkspaceInstructionsError, match="sort du workspace"):
            make_loader(tmp_path).load()
    finally:
        outside.unlink()


async def test_base_prompt_and_agents_md_are_sent_together(tmp_path: Path) -> None:
    (tmp_path / "AGENTS.md").write_text(
        "Règle témoin du workspace.",
        encoding="utf-8",
    )
    loader = make_loader(tmp_path)
    loaded = loader.load()
    assert loaded is not None
    agent = build_agent(Settings(workspace=tmp_path))

    with agent.override(model=TestModel(call_tools=[])):
        result = await agent.run("Dis bonjour", instructions=loaded.as_prompt())

    request = next(
        message
        for message in result.all_messages()
        if isinstance(message, ModelRequest)
    )
    instructions = request.instructions or ""
    assert AGENT_INSTRUCTIONS in instructions
    assert "Règle témoin du workspace." in instructions


def test_global_instructions_are_composed_before_project_instructions(
    tmp_path: Path,
) -> None:
    global_root = tmp_path / "global"
    project_root = tmp_path / "project"
    global_root.mkdir()
    project_root.mkdir()
    (global_root / "AGENTS.md").write_text("Règle globale", encoding="utf-8")
    (project_root / "AGENTS.md").write_text("Règle projet", encoding="utf-8")
    global_instructions = WorkspaceInstructionsLoader(
        global_root,
        Path("AGENTS.md"),
        65_536,
        scope="globales de TIA",
    ).load()
    project_instructions = WorkspaceInstructionsLoader(
        project_root,
        Path("AGENTS.md"),
        65_536,
        scope="du projet",
    ).load()

    assert global_instructions is not None
    assert project_instructions is not None
    prompt = combine_runtime_instructions(
        global_instructions.as_prompt(),
        project_instructions.as_prompt(),
    )

    assert prompt is not None
    assert "Instructions globales de TIA" in prompt
    assert prompt.index("Règle globale") < prompt.index("Règle projet")
