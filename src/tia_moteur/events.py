"""Protocole public et versionné des événements émis par TIA."""

from __future__ import annotations

from typing import Annotated, Literal, TypeAlias

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class TiaEventBase(BaseModel):
    """Champs communs à toutes les lignes du protocole JSONL."""

    model_config = ConfigDict(extra="forbid")

    schema_version: Literal[1] = 1
    type: str
    session_id: str
    run_id: str
    sequence: int = Field(ge=0)


class RunStartedEvent(TiaEventBase):
    type: Literal["run.started"] = "run.started"
    model: str
    workspace: str
    restored: bool


class InstructionsChangedEvent(TiaEventBase):
    type: Literal["instructions.changed"] = "instructions.changed"
    scope: Literal["global", "project"]
    action: Literal["added", "reloaded", "removed"]
    path: str | None = None


class SkillActivatedEvent(TiaEventBase):
    type: Literal["skill.activated"] = "skill.activated"
    name: str
    source: Literal["prompt", "tool"]


class TextStartedEvent(TiaEventBase):
    """Début ou remplacement d'une part textuelle du provider."""

    type: Literal["text.started"] = "text.started"
    response_index: int = Field(ge=0)
    part_index: int = Field(ge=0)
    content: str


class TextDeltaEvent(TiaEventBase):
    type: Literal["text.delta"] = "text.delta"
    response_index: int = Field(ge=0)
    part_index: int = Field(ge=0)
    content: str


class ToolCalledEvent(TiaEventBase):
    type: Literal["tool.called"] = "tool.called"
    tool_call_id: str
    name: str
    arguments: JsonValue
    arguments_valid: bool | None = None


class ToolCompletedEvent(TiaEventBase):
    type: Literal["tool.completed"] = "tool.completed"
    tool_call_id: str
    name: str
    outcome: Literal["success", "failed", "denied", "retry", "aborted"]
    result: JsonValue


class Usage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0
    input_audio_tokens: int = 0
    cache_audio_read_tokens: int = 0
    output_audio_tokens: int = 0
    requests: int = 0
    tool_calls: int = 0
    details: dict[str, int] = Field(default_factory=dict)


class RunCompletedEvent(TiaEventBase):
    type: Literal["run.completed"] = "run.completed"
    output: str
    usage: Usage


class RunFailedEvent(TiaEventBase):
    type: Literal["run.failed"] = "run.failed"
    code: Literal[
        "configuration_error",
        "invalid_prompt",
        "run_error",
        "interrupted",
    ]
    message: str
    retryable: bool = False


TiaEvent: TypeAlias = Annotated[
    RunStartedEvent
    | InstructionsChangedEvent
    | SkillActivatedEvent
    | TextStartedEvent
    | TextDeltaEvent
    | ToolCalledEvent
    | ToolCompletedEvent
    | RunCompletedEvent
    | RunFailedEvent,
    Field(discriminator="type"),
]


def event_to_json_line(event: TiaEventBase) -> str:
    """Sérialise un événement en une ligne JSON UTF-8 compacte."""
    return event.model_dump_json(exclude_none=True)
