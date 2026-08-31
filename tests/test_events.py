"""Tests du protocole public TIA."""

import json

import pytest
from pydantic import ValidationError

from tia_moteur.events import ToolCompletedEvent, TextDeltaEvent, event_to_json_line


def test_event_is_a_single_versioned_json_line() -> None:
    event = TextDeltaEvent(
        session_id="session-1",
        run_id="run-1",
        sequence=2,
        response_index=0,
        part_index=0,
        content="Bonjour\nJérémy",
    )

    encoded = event_to_json_line(event)
    decoded = json.loads(encoded)

    assert "\n" not in encoded
    assert decoded == {
        "schema_version": 1,
        "type": "text.delta",
        "session_id": "session-1",
        "run_id": "run-1",
        "sequence": 2,
        "response_index": 0,
        "part_index": 0,
        "content": "Bonjour\nJérémy",
    }


def test_event_rejects_non_json_tool_payload() -> None:
    with pytest.raises(ValidationError):
        ToolCompletedEvent(
            session_id="session-1",
            run_id="run-1",
            sequence=0,
            tool_call_id="tool-1",
            name="unsafe",
            outcome="success",
            result=object(),
        )
