"""Tests du runtime, des sessions et des points d'injection."""

from collections.abc import Sequence
import os
from pathlib import Path

import pytest
from pydantic_ai import models
from pydantic_ai.models.test import TestModel

from tia_moteur.agent_setup import AgentSetup
from tia_moteur.credentials import MappingCredentials
from tia_moteur.events import (
    RunCompletedEvent,
    RunFailedEvent,
    RunStartedEvent,
    TextDeltaEvent,
    TextStartedEvent,
    ToolCalledEvent,
    ToolCompletedEvent,
)
from tia_moteur.portable_config import RuntimePaths
from tia_moteur.runtime import TiaRuntime
from tia_moteur.session import TiaRunError
from tia_moteur.session_store import MemorySessionStore
from tia_moteur.config import Settings


models.ALLOW_MODEL_REQUESTS = False


def make_runtime(
    tmp_path: Path,
    *,
    model: TestModel,
    tools=(),
    tool_factories=(),
    model_factory=None,
    store=None,
    credentials=None,
) -> TiaRuntime:
    config_home = tmp_path / "config"
    workspace = tmp_path / "workspace"
    config_home.mkdir(exist_ok=True)
    workspace.mkdir(exist_ok=True)
    settings = Settings.model_validate(
        {
            "workspace": workspace,
            "global_skills_directory": config_home / "skills",
        }
    )
    setup = AgentSetup.model_validate(
        {
            "version": 1,
            "skills": {"enabled": False},
            "tools": {
                "bash": {
                    "enabled": False,
                    "execution_mode": "direct",
                    "shell": "/bin/zsh",
                }
            },
        }
    )
    paths = RuntimePaths(
        config_home=config_home,
        workspace=workspace,
        global_env=config_home / ".env",
        project_env=workspace / ".env",
        global_setup=config_home / "agent.setup.yaml",
        project_setup=workspace / "agent.setup.yaml",
        global_instructions=config_home / "AGENTS.md",
        project_instructions=workspace / "AGENTS.md",
        global_skills=config_home / "skills",
        project_skills=workspace / ".agents" / "skills",
    )
    return TiaRuntime(
        settings,
        paths,
        setup,
        model=None if model_factory is not None else model,
        model_factory=model_factory,
        tools=tools,
        tool_factories=tool_factories,
        session_store=store,
        credentials=credentials,
    )


async def collect(session, prompt: str):
    return [event async for event in session.stream(prompt, run_id="run-1")]


async def test_session_streams_text_and_terminal_result(tmp_path: Path) -> None:
    runtime = make_runtime(
        tmp_path,
        model=TestModel(call_tools=[], custom_output_text="Bonjour TIA"),
    )

    events = await collect(runtime.create_session(session_id="session-1"), "Bonjour")

    assert isinstance(events[0], RunStartedEvent)
    assert events[0].model == "test"
    assert isinstance(events[-1], RunCompletedEvent)
    assert events[-1].output == "Bonjour TIA"
    streamed_text = "".join(
        event.content
        for event in events
        if isinstance(event, (TextStartedEvent, TextDeltaEvent))
    )
    assert streamed_text == "Bonjour TIA"
    started = [event for event in events if isinstance(event, TextStartedEvent)]
    assert len(started) == 1
    assert started[0].content == ""
    assert [event.sequence for event in events] == list(range(len(events)))
    assert {event.session_id for event in events} == {"session-1"}
    assert {event.run_id for event in events} == {"run-1"}


async def test_injected_tool_emits_correlated_events(tmp_path: Path) -> None:
    async def echo(value: str) -> str:
        return value

    runtime = make_runtime(
        tmp_path,
        model=TestModel(call_tools=["echo"], custom_output_text="Terminé"),
        tools=[echo],
    )

    events = await collect(runtime.create_session(), "Utilise echo")

    calls = [event for event in events if isinstance(event, ToolCalledEvent)]
    completions = [
        event for event in events if isinstance(event, ToolCompletedEvent)
    ]
    assert len(calls) == len(completions) == 1
    assert calls[0].name == completions[0].name == "echo"
    assert calls[0].tool_call_id == completions[0].tool_call_id
    assert completions[0].outcome == "success"
    assert completions[0].result == "a"
    text_events = [
        event
        for event in events
        if isinstance(event, (TextStartedEvent, TextDeltaEvent))
    ]
    assert {event.response_index for event in text_events} == {1}


async def test_store_resumes_history_by_session_id(tmp_path: Path) -> None:
    store = MemorySessionStore()
    runtime = make_runtime(
        tmp_path,
        model=TestModel(call_tools=[], custom_output_text="Réponse"),
        store=store,
    )
    first = runtime.create_session(session_id="persistent")
    await first.run("Premier tour")

    resumed = await runtime.resume_session("persistent")
    events = await collect(resumed, "Deuxième tour")

    assert resumed.history
    assert isinstance(events[0], RunStartedEvent)
    assert events[0].restored is True
    stored = await store.load("persistent")
    assert stored == resumed.history


async def test_failed_run_does_not_persist_history(tmp_path: Path) -> None:
    async def explode(value: str) -> str:
        raise RuntimeError("boom")

    store = MemorySessionStore()
    runtime = make_runtime(
        tmp_path,
        model=TestModel(call_tools=["explode"]),
        tools=[explode],
        store=store,
    )
    session = runtime.create_session(session_id="failed")

    events = await collect(session, "Échoue")

    assert isinstance(events[-1], RunFailedEvent)
    assert events[-1].code == "run_error"
    aborted = [
        event
        for event in events
        if isinstance(event, ToolCompletedEvent) and event.outcome == "aborted"
    ]
    assert len(aborted) == 1
    assert session.history == ()
    assert await store.load("failed") is None
    with pytest.raises(TiaRunError, match="boom"):
        await session.run("Échoue encore")
    assert not session._run_lock.locked()


def test_factories_receive_session_credentials_without_leaking_them(
    tmp_path: Path,
) -> None:
    received: list[str | None] = []
    context_reprs: list[str] = []

    def tool_factory(context) -> Sequence:
        received.append(context.credentials.get("API_TOKEN"))
        context_reprs.append(repr(context))

        async def injected_tool(value: str) -> str:
            return value

        return [injected_tool]

    runtime = make_runtime(
        tmp_path,
        model=TestModel(call_tools=[]),
        tool_factories=[tool_factory],
        credentials={"API_TOKEN": "runtime-secret"},
    )

    runtime.create_session(credentials={"API_TOKEN": "session-secret"})

    assert received == ["session-secret"]
    assert "session-secret" not in context_reprs[0]
    assert "runtime-secret" not in context_reprs[0]
    assert "runtime-secret" not in repr(runtime.credentials)
    assert "session-secret" not in repr(MappingCredentials({"x": "session-secret"}))
    assert MappingCredentials({"AUTH": "business-secret"}).redact(
        "value=business-secret"
    ) == "value=[redacted]"


async def test_tool_payload_redacts_known_credentials(tmp_path: Path) -> None:
    async def reveal(value: str) -> str:
        del value
        return "token=session-secret"

    runtime = make_runtime(
        tmp_path,
        model=TestModel(call_tools=["reveal"], custom_output_text="Terminé"),
        tools=[reveal],
        credentials={"API_TOKEN": "session-secret"},
    )

    events = await collect(runtime.create_session(), "Utilise reveal")

    completed = [
        event for event in events if isinstance(event, ToolCompletedEvent)
    ]
    assert completed[0].result == "token=[redacted]"


def test_duplicate_injected_tool_is_rejected(tmp_path: Path) -> None:
    async def first(value: str) -> str:
        return value

    async def second(value: str) -> str:
        return value

    second.__name__ = first.__name__
    runtime = make_runtime(
        tmp_path,
        model=TestModel(call_tools=[]),
        tools=[first, second],
    )

    with pytest.raises(ValueError, match="Tools dupliqués: first"):
        runtime.create_session()


def test_environment_factory_receives_dotenv_credentials_without_global_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config_home = tmp_path / "portable-config"
    workspace = tmp_path / "portable-workspace"
    config_home.mkdir()
    workspace.mkdir()
    (config_home / ".env").write_text(
        "SERVICE_TOKEN=dotenv-secret\n",
        encoding="utf-8",
    )
    (config_home / "agent.setup.yaml").write_text(
        """version: 1
skills:
  enabled: false
tools:
  bash:
    enabled: false
    execution_mode: direct
    shell: /bin/zsh
""",
        encoding="utf-8",
    )
    received: list[str | None] = []

    def model_factory(context):
        received.append(context.credentials.get("SERVICE_TOKEN"))
        return TestModel(call_tools=[])

    monkeypatch.delenv("SERVICE_TOKEN", raising=False)
    monkeypatch.setenv("TIA_MODEL", "model-from-real-process")
    runtime = TiaRuntime.from_environment(
        workspace=str(workspace),
        environment={"TIA_CONFIG_HOME": str(config_home)},
        model_factory=model_factory,
    )

    runtime.create_session()

    assert received == ["dotenv-secret"]
    assert "SERVICE_TOKEN" not in os.environ
    assert runtime.settings.model == "openai-responses:gpt-5.6-luna"


async def test_session_preserves_prompt_whitespace(tmp_path: Path) -> None:
    runtime = make_runtime(
        tmp_path,
        model=TestModel(call_tools=[], custom_output_text="OK"),
    )
    session = runtime.create_session()

    await session.run("  bloc de code\n")

    first_request = session.history[0]
    contents = [
        getattr(part, "content", None)
        for part in first_request.parts
    ]
    assert "  bloc de code\n" in contents
