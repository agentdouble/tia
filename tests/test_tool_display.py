"""Tests du rendu terminal des tools."""

from pydantic_ai.messages import ToolCallPart

from tia_moteur.tool_display import format_tool_call


def test_formats_tool_call() -> None:
    part = ToolCallPart("run_bash", {"command": "pwd"})

    rendered = format_tool_call(part)

    assert rendered == "\n[tool] run_bash: pwd"


def test_only_displays_name_for_other_tools() -> None:
    part = ToolCallPart("read_file", {"path": "/tmp/secret.txt"})

    rendered = format_tool_call(part)

    assert rendered == "\n[tool] read_file"
    assert "secret" not in rendered


def test_formats_skill_activation_separately() -> None:
    part = ToolCallPart("load_skill", {"name": "python-quality"})

    rendered = format_tool_call(part)

    assert rendered == "\n[skill] python-quality"
