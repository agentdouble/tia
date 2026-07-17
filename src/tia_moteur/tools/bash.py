"""Exécution asynchrone de commandes shell pour l'agent."""

import asyncio
import os
import signal
from collections.abc import Awaitable, Callable, Mapping
from pathlib import Path
from typing import Protocol

from pydantic import BaseModel

from tia_moteur.tools.bash_policy import CommandRejectedError


class BashResult(BaseModel):
    """Résultat structuré renvoyé au modèle après une commande shell."""

    command: str
    cwd: str
    exit_code: int | None
    stdout: str
    stderr: str
    timed_out: bool = False
    output_truncated: bool = False
    blocked: bool = False
    policy_reason: str | None = None


class CommandPolicy(Protocol):
    """Contrat minimal d'une politique préparant une commande sûre."""

    def prepare(self, command: str) -> list[str]: ...


class BashExecutor:
    """Exécute une commande dans un workspace avec timeout et sortie bornée."""

    def __init__(
        self,
        workspace: Path,
        timeout_seconds: float,
        max_output_chars: int,
        policy: CommandPolicy,
        environment: Mapping[str, str] | None = None,
    ) -> None:
        self.workspace = workspace
        self.timeout_seconds = timeout_seconds
        self.max_output_chars = max_output_chars
        self.policy = policy
        self._environment = dict(environment) if environment is not None else None

    async def run(self, command: str) -> BashResult:
        """Exécute une commande et retourne stdout, stderr et le code de sortie."""
        try:
            argv = self.policy.prepare(command)
        except CommandRejectedError as exc:
            return BashResult(
                command=command,
                cwd=str(self.workspace),
                exit_code=None,
                stdout="",
                stderr=str(exc),
                blocked=True,
                policy_reason=str(exc),
            )

        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                cwd=self.workspace,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                start_new_session=True,
                env=self._environment,
            )
        except FileNotFoundError:
            return BashResult(
                command=command,
                cwd=str(self.workspace),
                exit_code=127,
                stdout="",
                stderr=f"Commande introuvable: {argv[0]}",
            )

        stdout_task = asyncio.create_task(self._read_stream(process.stdout))
        stderr_task = asyncio.create_task(self._read_stream(process.stderr))
        timed_out = False

        try:
            await asyncio.wait_for(process.wait(), timeout=self.timeout_seconds)
        except TimeoutError:
            timed_out = True
            await self._stop_process(process)
        except BaseException:
            await asyncio.shield(self._stop_process(process))
            await asyncio.gather(
                stdout_task,
                stderr_task,
                return_exceptions=True,
            )
            raise

        stdout, stdout_truncated = await stdout_task
        stderr, stderr_truncated = await stderr_task

        return BashResult(
            command=command,
            cwd=str(self.workspace),
            exit_code=process.returncode,
            stdout=stdout,
            stderr=stderr,
            timed_out=timed_out,
            output_truncated=stdout_truncated or stderr_truncated,
        )

    async def _read_stream(
        self, stream: asyncio.StreamReader | None
    ) -> tuple[str, bool]:
        if stream is None:
            return "", False

        kept = bytearray()
        truncated = False
        byte_limit = self.max_output_chars * 4

        while chunk := await stream.read(4096):
            remaining = byte_limit - len(kept)
            if remaining > 0:
                kept.extend(chunk[:remaining])
            if len(chunk) > remaining:
                truncated = True

        text = kept.decode(errors="replace")
        if len(text) > self.max_output_chars:
            text = text[: self.max_output_chars]
            truncated = True
        if truncated:
            text += "\n[sortie tronquée]"
        return text, truncated

    async def _stop_process(self, process: asyncio.subprocess.Process) -> None:
        if process.returncode is not None:
            return

        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            return

        try:
            await asyncio.wait_for(process.wait(), timeout=1.0)
        except TimeoutError:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()


def create_bash_tool(
    executor: BashExecutor,
) -> Callable[[str], Awaitable[BashResult]]:
    """Crée le tool typé que Pydantic AI expose au modèle."""

    async def run_bash(command: str) -> BashResult:
        """Exécute une commande shell dans le workspace de l'agent.

        Args:
            command: Commande shell complète à exécuter.
        """
        return await executor.run(command)

    return run_bash
