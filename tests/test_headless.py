"""Tests de la vraie commande headless dans un processus enfant."""

import json
import os
from pathlib import Path
import select
import signal
import subprocess
import sys
import time

import pytest


SAFE_SETUP = """version: 1

skills:
  enabled: false

tools:
  bash:
    enabled: false
    execution_mode: direct
    shell: /bin/zsh
"""

TOOL_SETUP = """version: 1

skills:
  enabled: false

tools:
  bash:
    enabled: true
    execution_mode: direct
    shell: /bin/zsh
    allowed_executables:
      - a
"""


def headless_environment(config_home: Path) -> dict[str, str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith("TIA_")
    }
    environment["TIA_CONFIG_HOME"] = str(config_home)
    return environment


def prepare_runtime(
    tmp_path: Path,
    *,
    setup: str = SAFE_SETUP,
    workspace_name: str = "workspace",
) -> tuple[Path, dict[str, str]]:
    config_home = tmp_path / "config"
    workspace = tmp_path / workspace_name
    config_home.mkdir()
    workspace.mkdir()
    (config_home / "agent.setup.yaml").write_text(setup, encoding="utf-8")
    return workspace, headless_environment(config_home)


def command(workspace: Path, *arguments: str) -> list[str]:
    return [
        sys.executable,
        "-m",
        "tia_moteur.cli",
        "run",
        "--format",
        "jsonl",
        "--model",
        "test",
        "--workspace",
        str(workspace),
        *arguments,
    ]


def parse_events(stdout: str) -> list[dict]:
    return [json.loads(line) for line in stdout.splitlines()]


def test_headless_argument_outputs_only_jsonl(tmp_path: Path) -> None:
    workspace, environment = prepare_runtime(tmp_path)

    completed = subprocess.run(
        command(workspace, "Bonjour"),
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
        check=False,
    )

    events = parse_events(completed.stdout)
    assert completed.returncode == 0
    assert completed.stderr == ""
    assert events[0]["type"] == "run.started"
    assert events[-1]["type"] == "run.completed"
    assert events[-1]["output"] == "success (no tool calls)"
    assert [event["sequence"] for event in events] == list(range(len(events)))
    assert len({event["session_id"] for event in events}) == 1
    assert len({event["run_id"] for event in events}) == 1
    assert "TIA —" not in completed.stdout


def test_headless_reads_prompt_from_stdin(tmp_path: Path) -> None:
    workspace, environment = prepare_runtime(tmp_path)

    completed = subprocess.run(
        command(workspace),
        input="Bonjour depuis stdin\n",
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0
    assert parse_events(completed.stdout)[-1]["type"] == "run.completed"


def test_headless_empty_prompt_is_usage_error(tmp_path: Path) -> None:
    workspace, environment = prepare_runtime(tmp_path)

    completed = subprocess.run(
        command(workspace),
        input="",
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 2
    assert completed.stdout == ""
    assert "prompt" in completed.stderr


def test_headless_configuration_error_is_json(tmp_path: Path) -> None:
    workspace, environment = prepare_runtime(tmp_path)
    config_home = Path(environment["TIA_CONFIG_HOME"])
    (config_home / "agent.setup.yaml").write_text("invalid: true\n", encoding="utf-8")

    completed = subprocess.run(
        command(workspace, "Bonjour"),
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
        check=False,
    )

    events = parse_events(completed.stdout)
    assert completed.returncode == 1
    assert completed.stderr == ""
    assert len(events) == 1
    assert events[0]["type"] == "run.failed"
    assert events[0]["code"] == "configuration_error"


def test_legacy_one_shot_cli_keeps_human_rendering(tmp_path: Path) -> None:
    workspace, environment = prepare_runtime(tmp_path)

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "tia_moteur.cli",
            "--model",
            "test",
            "--workspace",
            str(workspace),
            "Bonjour",
        ],
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0
    assert "TIA — modèle: test" in completed.stdout
    assert "TIA > success (no tool calls)" in completed.stdout
    assert completed.stderr == ""


def test_headless_writes_utf8_when_host_stdout_is_ascii(tmp_path: Path) -> None:
    workspace, environment = prepare_runtime(
        tmp_path,
        workspace_name="wérkspace",
    )
    environment["PYTHONIOENCODING"] = "ascii"

    completed = subprocess.run(
        command(workspace, "Bonjour"),
        capture_output=True,
        env=environment,
        timeout=10,
        check=False,
    )

    stdout = completed.stdout.decode("utf-8")
    assert completed.returncode == 0
    assert completed.stderr == b""
    assert parse_events(stdout)[0]["workspace"].endswith("wérkspace")


def test_headless_propagates_explicit_session_id(tmp_path: Path) -> None:
    workspace, environment = prepare_runtime(tmp_path)

    completed = subprocess.run(
        command(workspace, "--session-id", "session-fixed", "Bonjour"),
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
        check=False,
    )

    assert completed.returncode == 0
    assert {
        event["session_id"] for event in parse_events(completed.stdout)
    } == {"session-fixed"}


def test_headless_streams_correlated_tool_events(tmp_path: Path) -> None:
    workspace, environment = prepare_runtime(tmp_path, setup=TOOL_SETUP)
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    executable = bin_directory / "a"
    executable.write_text("#!/bin/sh\nprintf 'tool-ok'\n", encoding="utf-8")
    executable.chmod(0o700)
    environment["PATH"] = f"{bin_directory}:{environment.get('PATH', '')}"

    completed = subprocess.run(
        command(workspace, "Lance le tool"),
        capture_output=True,
        text=True,
        env=environment,
        timeout=10,
        check=False,
    )

    events = parse_events(completed.stdout)
    called = next(event for event in events if event["type"] == "tool.called")
    finished = next(
        event for event in events if event["type"] == "tool.completed"
    )
    assert completed.returncode == 0
    assert completed.stderr == ""
    assert called["tool_call_id"] == finished["tool_call_id"]
    assert called["name"] == finished["name"] == "run_bash"
    assert finished["outcome"] == "success"
    assert finished["result"]["stdout"] == "tool-ok"


def test_headless_sigint_while_reading_stdin_is_clean(tmp_path: Path) -> None:
    workspace, environment = prepare_runtime(tmp_path)
    process = subprocess.Popen(
        command(workspace),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
    )
    time.sleep(0.5)

    process.send_signal(signal.SIGINT)
    stdout, stderr = process.communicate(timeout=10)

    assert process.returncode == 130
    assert stderr == ""
    events = parse_events(stdout)
    assert events[-1]["type"] == "run.failed"
    assert events[-1]["code"] == "interrupted"


@pytest.mark.parametrize(
    ("termination_signal", "expected_code"),
    [(signal.SIGINT, 130), (signal.SIGTERM, 143)],
)
def test_headless_signal_stops_active_tool_process(
    tmp_path: Path,
    termination_signal: signal.Signals,
    expected_code: int,
) -> None:
    workspace, environment = prepare_runtime(tmp_path, setup=TOOL_SETUP)
    bin_directory = tmp_path / "bin"
    bin_directory.mkdir()
    pid_file = tmp_path / "tool.pid"
    executable = bin_directory / "a"
    executable.write_text(
        f"#!/bin/sh\necho $$ > {pid_file}\nexec sleep 60\n",
        encoding="utf-8",
    )
    executable.chmod(0o700)
    environment["PATH"] = f"{bin_directory}:{environment.get('PATH', '')}"
    process = subprocess.Popen(
        command(workspace, "Lance le tool"),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=environment,
        bufsize=1,
    )
    observed_lines: list[str] = []
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        ready, _, _ = select.select([process.stdout], [], [], 0.1)
        if not ready:
            continue
        line = process.stdout.readline()
        if not line:
            break
        observed_lines.append(line)
        if json.loads(line)["type"] == "tool.called":
            break
    assert observed_lines
    for _ in range(200):
        if pid_file.exists():
            break
        time.sleep(0.01)
    assert pid_file.exists()
    tool_pid = int(pid_file.read_text(encoding="utf-8"))

    process.send_signal(termination_signal)
    stdout_tail, stderr = process.communicate(timeout=10)
    stdout = "".join(observed_lines) + stdout_tail

    assert process.returncode == expected_code
    assert stderr == ""
    events = parse_events(stdout)
    assert events[-2]["type"] == "tool.completed"
    assert events[-2]["outcome"] == "aborted"
    assert events[-1]["type"] == "run.failed"
    assert events[-1]["code"] == "interrupted"
    assert [event["sequence"] for event in events] == list(range(len(events)))
    for _ in range(200):
        try:
            os.kill(tool_pid, 0)
        except ProcessLookupError:
            break
        time.sleep(0.01)
    with pytest.raises(ProcessLookupError):
        os.kill(tool_pid, 0)
