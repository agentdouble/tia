"""Tests du tool Bash sans appel à un LLM."""

from pathlib import Path
import shlex

import pytest

from tia_moteur.tools import BashExecutor


class AllowAllPolicy:
    """Politique réservée aux tests unitaires de l'exécuteur bas niveau."""

    def prepare(self, command: str) -> list[str]:
        return shlex.split(command)


def make_executor(
    workspace: Path,
    *,
    timeout: float = 2.0,
    max_output: int = 1_000,
) -> BashExecutor:
    return BashExecutor(
        workspace=workspace,
        timeout_seconds=timeout,
        max_output_chars=max_output,
        policy=AllowAllPolicy(),
    )


async def test_runs_command_in_workspace(tmp_path: Path) -> None:
    result = await make_executor(tmp_path).run("pwd")

    assert result.exit_code == 0
    assert result.stdout == f"{tmp_path}\n"
    assert result.stderr == ""
    assert result.timed_out is False


async def test_returns_non_zero_exit_and_stderr(tmp_path: Path) -> None:
    result = await make_executor(tmp_path).run("ls fichier-absent")

    assert result.exit_code != 0
    assert "fichier-absent" in result.stderr


async def test_stops_command_after_timeout(tmp_path: Path) -> None:
    result = await make_executor(tmp_path, timeout=0.05).run("sleep 2")

    assert result.timed_out is True
    assert result.exit_code is not None


async def test_truncates_large_output(tmp_path: Path) -> None:
    result = await make_executor(tmp_path, max_output=1_000).run(
        "python3 -c \"print('x' * 2000)\""
    )

    assert result.output_truncated is True
    assert result.stdout.endswith("[sortie tronquée]")
    assert len(result.stdout) < 1_100
