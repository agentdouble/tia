"""Test d'intégration léger de l'agent et de son tool."""

from pathlib import Path

from pydantic_ai import models
from pydantic_ai.models.test import TestModel

from tia_moteur import Settings, build_agent


models.ALLOW_MODEL_REQUESTS = False


async def test_agent_exposes_bash_tool(tmp_path: Path) -> None:
    agent = build_agent(Settings(workspace=tmp_path))
    test_model = TestModel(call_tools=[])

    with agent.override(model=test_model):
        await agent.run("Inspecte le workspace")

    tools = test_model.last_model_request_parameters.function_tools
    assert [tool.name for tool in tools] == ["run_bash", "load_skill"]
    assert "command" in tools[0].parameters_json_schema["properties"]
    assert "name" in tools[1].parameters_json_schema["properties"]


async def test_agent_does_not_expose_skill_tool_when_disabled(
    tmp_path: Path,
) -> None:
    settings = Settings(
        workspace=tmp_path,
        setup_file=Path("tests/fixtures/agent.setup.skills-disabled.yaml"),
    )
    agent = build_agent(settings)
    test_model = TestModel(call_tools=[])

    with agent.override(model=test_model):
        await agent.run("Inspecte le workspace")

    tools = test_model.last_model_request_parameters.function_tools
    assert [tool.name for tool in tools] == ["run_bash"]
