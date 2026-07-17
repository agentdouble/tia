"""Session conversationnelle réutilisable, indépendante du terminal."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Sequence
from dataclasses import dataclass
import json
from pathlib import Path
from uuid import uuid4

from pydantic_core import to_jsonable_python
from pydantic_ai import (
    Agent,
    AgentRunResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    ModelMessage,
    PartDeltaEvent,
    PartStartEvent,
)
from pydantic_ai.messages import RetryPromptPart, TextPart, TextPartDelta, ToolReturnPart

from tia_moteur.agent_setup import AgentSetup
from tia_moteur.config import Settings
from tia_moteur.credentials import CredentialProvider, redact_text
from tia_moteur.events import (
    InstructionsChangedEvent,
    RunCompletedEvent,
    RunFailedEvent,
    RunStartedEvent,
    SkillActivatedEvent,
    TextDeltaEvent,
    TextStartedEvent,
    TiaEvent,
    ToolCalledEvent,
    ToolCompletedEvent,
    Usage,
)
from tia_moteur.portable_config import RuntimePaths
from tia_moteur.session_store import SessionStore
from tia_moteur.skills import (
    SkillRegistry,
    build_skill_run_context,
    combine_runtime_instructions,
)
from tia_moteur.workspace_instructions import (
    WorkspaceInstructions,
    WorkspaceInstructionsLoader,
)


@dataclass(frozen=True)
class TiaRunResult:
    """Résultat pratique retourné par ``TiaSession.run``."""

    session_id: str
    run_id: str
    output: str
    usage: Usage


class TiaRunError(RuntimeError):
    """Erreur terminale d'un run consommé sans streaming."""

    def __init__(self, event: RunFailedEvent) -> None:
        super().__init__(event.message)
        self.event = event
        self.code = event.code


class TiaSession:
    """Porte l'historique et sérialise les runs d'une conversation."""

    def __init__(
        self,
        *,
        session_id: str,
        agent: Agent[None, str],
        settings: Settings,
        paths: RuntimePaths,
        setup: AgentSetup,
        skill_registry: SkillRegistry,
        store: SessionStore,
        credentials: CredentialProvider,
        model_name: str,
        initial_history: Sequence[ModelMessage] | None = None,
    ) -> None:
        self.session_id = session_id
        self.agent = agent
        self.settings = settings
        self.paths = paths
        self.setup = setup
        self.skill_registry = skill_registry
        self.store = store
        self.credentials = credentials
        self.model_name = model_name
        self._history = tuple(initial_history or ())
        self._history_loaded = initial_history is not None
        self._restored = bool(initial_history)
        self._instruction_states: dict[
            str,
            tuple[str | None, str | None],
        ] | None = None
        self._run_lock = asyncio.Lock()
        self._global_instructions_loader = WorkspaceInstructionsLoader(
            workspace=paths.config_home,
            instructions_file=Path("AGENTS.md"),
            max_chars=settings.max_instructions_chars,
            scope="globales de TIA",
        )
        self._project_instructions_loader = WorkspaceInstructionsLoader(
            workspace=settings.workspace,
            instructions_file=settings.instructions_file,
            max_chars=settings.max_instructions_chars,
            scope="du projet",
        )

    @property
    def history(self) -> tuple[ModelMessage, ...]:
        """Retourne une copie immuable de l'historique courant."""
        return tuple(self._history)

    async def load(self) -> None:
        """Charge une éventuelle session persistée une seule fois."""
        if self._history_loaded:
            return
        stored = await self.store.load(self.session_id)
        self._history = tuple(stored or ())
        self._restored = stored is not None
        self._history_loaded = True

    async def run(self, prompt: str) -> TiaRunResult:
        """Exécute un tour et retourne son résultat final."""
        completed: RunCompletedEvent | None = None
        failed: RunFailedEvent | None = None
        async for event in self.stream(prompt):
            if isinstance(event, RunFailedEvent):
                failed = event
            elif isinstance(event, RunCompletedEvent):
                completed = event
        if failed is not None:
            raise TiaRunError(failed)
        if completed is None:
            raise RuntimeError("TIA s'est terminée sans événement terminal.")
        return TiaRunResult(
            session_id=completed.session_id,
            run_id=completed.run_id,
            output=completed.output,
            usage=completed.usage,
        )

    async def stream(
        self,
        prompt: str,
        *,
        run_id: str | None = None,
    ) -> AsyncIterator[TiaEvent]:
        """Exécute un tour et traduit Pydantic AI vers le protocole TIA."""
        active_run_id = run_id or str(uuid4())
        sequence = 0
        pending_tools: dict[str, str] = {}

        def event(event_type, **values):
            nonlocal sequence
            created = event_type(
                session_id=self.session_id,
                run_id=active_run_id,
                sequence=sequence,
                **values,
            )
            sequence += 1
            return created

        async with self._run_lock:
            try:
                await self.load()
                yield event(
                    RunStartedEvent,
                    model=self.model_name,
                    workspace=str(self.settings.workspace),
                    restored=self._restored,
                )

                if not prompt.strip():
                    yield event(
                        RunFailedEvent,
                        code="invalid_prompt",
                        message="Le prompt ne peut pas être vide.",
                    )
                    return

                global_instructions = self._global_instructions_loader.load()
                project_instructions = self._project_instructions_loader.load()
                current_layers = {
                    "global": global_instructions,
                    "project": project_instructions,
                }
                for change in self._instruction_changes(current_layers):
                    yield event(InstructionsChangedEvent, **change)

                skill_context = (
                    build_skill_run_context(self.skill_registry, prompt)
                    if self.setup.skills.enabled
                    else None
                )
                if skill_context is not None:
                    for skill in skill_context.activated:
                        yield event(
                            SkillActivatedEvent,
                            name=skill.name,
                            source="prompt",
                        )

                terminal_result = None
                tool_names: dict[str, str] = {}
                response_index = -1
                awaiting_model_response = True
                async with self.agent.run_stream_events(
                    prompt,
                    message_history=self._history or None,
                    instructions=combine_runtime_instructions(
                        global_instructions.as_prompt()
                        if global_instructions is not None
                        else None,
                        project_instructions.as_prompt()
                        if project_instructions is not None
                        else None,
                        skill_context.prompt if skill_context is not None else None,
                    ),
                ) as stream:
                    async for provider_event in stream:
                        if (
                            isinstance(provider_event, PartStartEvent)
                            and awaiting_model_response
                        ):
                            response_index += 1
                            awaiting_model_response = False
                        if isinstance(provider_event, PartStartEvent) and isinstance(
                            provider_event.part,
                            TextPart,
                        ):
                            yield event(
                                TextStartedEvent,
                                response_index=response_index,
                                part_index=provider_event.index,
                                content=provider_event.part.content,
                            )
                        elif isinstance(provider_event, PartDeltaEvent) and isinstance(
                            provider_event.delta,
                            TextPartDelta,
                        ):
                            if response_index < 0:
                                response_index = 0
                                awaiting_model_response = False
                            if provider_event.delta.content_delta:
                                yield event(
                                    TextDeltaEvent,
                                    response_index=response_index,
                                    part_index=provider_event.index,
                                    content=provider_event.delta.content_delta,
                                )
                        elif isinstance(provider_event, FunctionToolCallEvent):
                            part = provider_event.part
                            tool_names[part.tool_call_id] = part.tool_name
                            pending_tools[part.tool_call_id] = part.tool_name
                            yield event(
                                ToolCalledEvent,
                                tool_call_id=part.tool_call_id,
                                name=part.tool_name,
                                arguments=_tool_arguments(
                                    part,
                                    self.credentials,
                                    self.settings.max_output_chars,
                                ),
                                arguments_valid=provider_event.args_valid,
                            )
                        elif isinstance(provider_event, FunctionToolResultEvent):
                            result_part = provider_event.part
                            if isinstance(result_part, ToolReturnPart):
                                pending_tools.pop(result_part.tool_call_id, None)
                                yield event(
                                    ToolCompletedEvent,
                                    tool_call_id=result_part.tool_call_id,
                                    name=result_part.tool_name,
                                    outcome=result_part.outcome,
                                    result=_jsonable(
                                        result_part.content,
                                        self.credentials,
                                        self.settings.max_output_chars,
                                    ),
                                )
                                awaiting_model_response = True
                                if (
                                    result_part.tool_name == "load_skill"
                                    and result_part.outcome == "success"
                                ):
                                    skill_name = _loaded_skill_name(result_part.content)
                                    if skill_name is not None:
                                        yield event(
                                            SkillActivatedEvent,
                                            name=skill_name,
                                            source="tool",
                                        )
                            elif isinstance(result_part, RetryPromptPart):
                                pending_tools.pop(result_part.tool_call_id, None)
                                yield event(
                                    ToolCompletedEvent,
                                    tool_call_id=result_part.tool_call_id,
                                    name=(
                                        result_part.tool_name
                                        or tool_names.get(result_part.tool_call_id)
                                        or "unknown"
                                    ),
                                    outcome="retry",
                                    result=_jsonable(
                                        result_part.content,
                                        self.credentials,
                                        self.settings.max_output_chars,
                                    ),
                                )
                                awaiting_model_response = True
                        elif isinstance(provider_event, AgentRunResultEvent):
                            terminal_result = provider_event.result

                if terminal_result is None:
                    raise RuntimeError("L'agent s'est terminé sans résultat final.")

                messages = tuple(terminal_result.all_messages())
                await self.store.save(self.session_id, messages)
                self._history = messages
                usage = terminal_result.usage
                yield event(
                    RunCompletedEvent,
                    output=terminal_result.output,
                    usage=Usage(
                        input_tokens=usage.input_tokens,
                        output_tokens=usage.output_tokens,
                        cache_write_tokens=usage.cache_write_tokens,
                        cache_read_tokens=usage.cache_read_tokens,
                        input_audio_tokens=usage.input_audio_tokens,
                        cache_audio_read_tokens=usage.cache_audio_read_tokens,
                        output_audio_tokens=usage.output_audio_tokens,
                        requests=usage.requests,
                        tool_calls=usage.tool_calls,
                        details=usage.details,
                    ),
                )
            except Exception as exc:
                for call_id, tool_name in tuple(pending_tools.items()):
                    yield event(
                        ToolCompletedEvent,
                        tool_call_id=call_id,
                        name=tool_name,
                        outcome="aborted",
                        result={"reason": "run_failed"},
                    )
                yield event(
                    RunFailedEvent,
                    code="run_error",
                    message=(
                        redact_text(self.credentials, str(exc))
                        or type(exc).__name__
                    ),
                )

    def _instruction_changes(
        self,
        layers: dict[str, WorkspaceInstructions | None],
    ) -> list[dict[str, str | None]]:
        current_states = {
            scope: (
                instructions.digest if instructions is not None else None,
                str(instructions.path) if instructions is not None else None,
            )
            for scope, instructions in layers.items()
        }
        if self._instruction_states is None:
            self._instruction_states = current_states
            return []

        changes: list[dict[str, str | None]] = []
        for scope, current in current_states.items():
            previous = self._instruction_states[scope]
            if current[0] == previous[0]:
                continue
            if previous[0] is None:
                action = "added"
                path = current[1]
            elif current[0] is None:
                action = "removed"
                path = previous[1]
            else:
                action = "reloaded"
                path = current[1]
            changes.append({"scope": scope, "action": action, "path": path})
        self._instruction_states = current_states
        return changes


def _tool_arguments(
    part,
    credentials: CredentialProvider,
    max_chars: int,
) -> object:
    try:
        value = part.args_as_dict()
    except Exception:
        value = {"raw": part.args_as_json_str()}
    return _jsonable(value, credentials, max_chars)


def _jsonable(
    value,
    credentials: CredentialProvider,
    max_chars: int,
) -> object:
    try:
        converted = to_jsonable_python(
            value,
            serialize_unknown=False,
            bytes_mode="base64",
            inf_nan_mode="null",
        )
    except Exception:
        return {"serialization_error": type(value).__name__}
    redacted = _redact_json(converted, credentials)
    try:
        encoded = json.dumps(
            redacted,
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        )
    except (TypeError, ValueError):
        return {"serialization_error": type(value).__name__}
    if len(encoded) > max_chars:
        return {
            "truncated": True,
            "preview": encoded[:max_chars],
        }
    return redacted


def _redact_json(value, credentials: CredentialProvider):
    if isinstance(value, str):
        return redact_text(credentials, value)
    if isinstance(value, list):
        return [_redact_json(item, credentials) for item in value]
    if isinstance(value, dict):
        return {
            str(key): _redact_json(item, credentials)
            for key, item in value.items()
        }
    return value


def _loaded_skill_name(value) -> str | None:
    if isinstance(value, dict):
        name = value.get("name")
    else:
        name = getattr(value, "name", None)
    return name if isinstance(name, str) and name else None
