"""Tests de non-contournement de la politique de commandes."""

from pathlib import Path

import pytest

from tia_moteur.agent_setup import load_agent_setup
from tia_moteur.tools import BashExecutor, BashPolicy, CommandRejectedError


@pytest.fixture
def policy() -> BashPolicy:
    setup = load_agent_setup(Path("tests/fixtures/agent.setup.safe.yaml"))
    return BashPolicy(setup.tools.bash)


def test_allows_read_only_command(policy: BashPolicy) -> None:
    assert policy.prepare("git status --short") == ["git", "status", "--short"]


@pytest.mark.parametrize(
    "command",
    [
        "rm -rf dossier",
        "/bin/rm -fr dossier",
        "ls; rm -rf dossier",
        "ls $(rm -rf dossier)",
        "find . -delete",
        "find . -exec rm -rf {} +",
        "find . -fprint resultat.txt",
        "file --compile",
        "git branch nouvelle-branche",
        "git clean -fd",
        "git diff --output=resultat.diff",
        "rg --pre rm motif",
        "sort -o resultat.txt entree.txt",
    ],
)
def test_rejects_destructive_or_indirect_commands(
    policy: BashPolicy,
    command: str,
) -> None:
    with pytest.raises(CommandRejectedError):
        policy.prepare(command)


async def test_blocked_command_never_reaches_subprocess(
    tmp_path: Path,
    policy: BashPolicy,
) -> None:
    victim = tmp_path / "a-conserver.txt"
    victim.write_text("important", encoding="utf-8")
    executor = BashExecutor(
        workspace=tmp_path,
        timeout_seconds=2,
        max_output_chars=1_000,
        policy=policy,
    )

    result = await executor.run("rm -rf a-conserver.txt")

    assert result.blocked is True
    assert result.exit_code is None
    assert "interdit" in (result.policy_reason or "")
    assert victim.read_text(encoding="utf-8") == "important"


def test_preserves_quoted_punctuation_as_argument(policy: BashPolicy) -> None:
    assert policy.prepare("printf 'a;b'") == ["printf", "a;b"]


def test_active_setup_allows_unrestricted_shell_commands() -> None:
    setup = load_agent_setup(Path("agent.setup.yaml"))
    policy = BashPolicy(setup.tools.bash)

    assert setup.skills.enabled is True
    assert policy.prepare("rm -rf cible") == [
        "/bin/zsh",
        "-lc",
        "rm -rf cible",
    ]
