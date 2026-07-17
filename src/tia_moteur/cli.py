"""Adaptateurs terminal interactif et headless JSONL de TIA."""

from __future__ import annotations

import argparse
import asyncio
from contextlib import redirect_stdout
from dataclasses import dataclass, field
from pathlib import Path
import signal
import sys
from uuid import uuid4

from tia_moteur.events import (
    InstructionsChangedEvent,
    RunCompletedEvent,
    RunFailedEvent,
    SkillActivatedEvent,
    TiaEventBase,
    ToolCalledEvent,
    ToolCompletedEvent,
    event_to_json_line,
)
from tia_moteur.portable_config import initialize_global_config
from tia_moteur.runtime import TiaRuntime
from tia_moteur.tool_display import format_tool_values


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tia",
        description="Agent local Pydantic AI avec accès à des tools.",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        help="Demande unique. Sans prompt, démarre une conversation interactive.",
    )
    parser.add_argument(
        "--model",
        help="Modèle Pydantic AI, ex: openai-responses:gpt-5.6-luna",
    )
    parser.add_argument(
        "--workspace",
        type=Path,
        help="Répertoire de travail initial des commandes Bash.",
    )
    return parser


def build_headless_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tia run",
        description="Exécute TIA sans interface et émet un flux JSONL versionné.",
    )
    parser.add_argument(
        "prompt",
        nargs="?",
        help="Demande unique. Sans argument, elle est lue sur stdin jusqu'à EOF.",
    )
    parser.add_argument(
        "--format",
        choices=("jsonl",),
        default="jsonl",
        help="Format machine de stdout (défaut: jsonl).",
    )
    parser.add_argument("--session-id", help="Identifiant de corrélation de session.")
    parser.add_argument("--model", help="Modèle Pydantic AI à utiliser.")
    parser.add_argument("--workspace", type=Path, help="Workspace du run.")
    return parser


async def run_interactive(
    runtime: TiaRuntime,
    first_prompt: str | None,
) -> None:
    """Rend les événements d'une session dans le terminal historique."""
    info = runtime.inspect()
    session = runtime.create_session()
    prompt = first_prompt

    print(f"TIA — modèle: {info.model}")
    print(f"Workspace: {info.workspace}")
    print(f"Configuration globale: {info.config_home}")
    if not info.instruction_paths:
        print("Instructions: prompt de base uniquement")
    else:
        sources = " + ".join(str(path) for path in info.instruction_paths)
        print(f"Instructions: prompt de base + {sources}")
    if not info.skills_enabled:
        print("Skills: désactivés par agent.setup.yaml")
    else:
        print(f"Skills: {info.available_skills} détecté(s)")

    while True:
        if prompt is None:
            try:
                prompt = input("\nVous > ").strip()
            except (EOFError, KeyboardInterrupt):
                print("\nAu revoir.")
                return

        if prompt.lower() in {"exit", "quit", "/exit", "/quit"}:
            print("Au revoir.")
            return
        if not prompt:
            prompt = None
            continue

        async for event in session.stream(prompt):
            if isinstance(event, InstructionsChangedEvent):
                if event.action == "removed":
                    print(f"\n[instructions] AGENTS.md {event.scope} retiré")
                else:
                    print(f"\n[instructions] rechargé: {event.path}")
            elif isinstance(event, SkillActivatedEvent):
                print(f"\n[skill] {event.name}")
            elif isinstance(event, ToolCalledEvent):
                if event.name != "load_skill":
                    arguments = event.arguments if isinstance(event.arguments, dict) else {}
                    print(format_tool_values(event.name, arguments))
            elif isinstance(event, RunCompletedEvent):
                print(f"\nTIA > {event.output}")
            elif isinstance(event, RunFailedEvent):
                print(f"\nErreur: {event.message}")

        if first_prompt is not None:
            return
        prompt = None


def _run_init_command() -> None:
    parser = argparse.ArgumentParser(
        prog="tia init",
        description="Initialise la configuration globale portable de TIA.",
    )
    parser.parse_args(sys.argv[2:])
    result = initialize_global_config()
    if result.created:
        print(f"Configuration TIA initialisée: {result.root}")
        for path in result.created:
            print(f"  créé: {path}")
    else:
        print(f"Configuration TIA déjà initialisée: {result.root}")


def _headless_prompt(parser: argparse.ArgumentParser, prompt: str | None) -> str:
    if prompt is not None:
        raw_prompt = prompt
    elif sys.stdin.isatty():
        parser.error("un prompt en argument ou sur stdin est obligatoire")
    else:
        raw_prompt = sys.stdin.read()
    if not raw_prompt.strip():
        parser.error("le prompt ne peut pas être vide")
    return raw_prompt


class _JsonlWriter:
    """Écrit toujours le protocole en UTF-8, indépendamment du locale hôte."""

    def __init__(self) -> None:
        self._binary_stream = getattr(sys.stdout, "buffer", None)
        self._text_stream = sys.stdout

    def write(self, event: TiaEventBase) -> None:
        line = event_to_json_line(event) + "\n"
        if self._binary_stream is not None:
            self._binary_stream.write(line.encode("utf-8"))
            self._binary_stream.flush()
        else:
            self._text_stream.write(line)
            self._text_stream.flush()


@dataclass
class _HeadlessState:
    last_sequence: int = -1
    interrupted_event_written: bool = False
    interrupt_exit_code: int = 130
    pending_tools: dict[str, str] = field(default_factory=dict)


async def _stream_headless(
    runtime: TiaRuntime,
    *,
    prompt: str,
    session_id: str,
    run_id: str,
    state: _HeadlessState,
    writer: _JsonlWriter,
) -> int:
    try:
        session = runtime.create_session(session_id=session_id)
    except Exception as exc:
        failure = RunFailedEvent(
            session_id=session_id,
            run_id=run_id,
            sequence=0,
            code="configuration_error",
            message=runtime.redact(str(exc)) or "Création de session impossible.",
        )
        writer.write(failure)
        state.last_sequence = 0
        return 1

    exit_code = 1
    try:
        async for event in session.stream(prompt, run_id=run_id):
            writer.write(event)
            state.last_sequence = event.sequence
            if isinstance(event, ToolCalledEvent):
                state.pending_tools[event.tool_call_id] = event.name
            elif isinstance(event, ToolCompletedEvent):
                state.pending_tools.pop(event.tool_call_id, None)
            if isinstance(event, RunCompletedEvent):
                exit_code = 0
            elif isinstance(event, RunFailedEvent):
                exit_code = 1
    except asyncio.CancelledError:
        for tool_call_id, tool_name in tuple(state.pending_tools.items()):
            state.last_sequence += 1
            writer.write(
                ToolCompletedEvent(
                    session_id=session_id,
                    run_id=run_id,
                    sequence=state.last_sequence,
                    tool_call_id=tool_call_id,
                    name=tool_name,
                    outcome="aborted",
                    result={"reason": "interrupted"},
                )
            )
        state.pending_tools.clear()
        writer.write(
            RunFailedEvent(
                session_id=session_id,
                run_id=run_id,
                sequence=state.last_sequence + 1,
                code="interrupted",
                message="Exécution interrompue.",
            )
        )
        state.interrupted_event_written = True
        raise
    return exit_code


def _run_headless_command() -> int:
    parser = build_headless_parser()
    args = parser.parse_args(sys.argv[2:])
    session_id = args.session_id or str(uuid4())
    run_id = str(uuid4())
    state = _HeadlessState()
    writer = _JsonlWriter()
    previous_sigterm_handler = None

    if hasattr(signal, "SIGTERM"):
        previous_sigterm_handler = signal.getsignal(signal.SIGTERM)

        def handle_sigterm(signum, frame) -> None:
            del frame
            state.interrupt_exit_code = 128 + signum
            raise KeyboardInterrupt

        signal.signal(signal.SIGTERM, handle_sigterm)

    try:
        prompt = _headless_prompt(parser, args.prompt)
        with redirect_stdout(sys.stderr):
            try:
                runtime = TiaRuntime.from_environment(
                    workspace=args.workspace,
                    model=args.model,
                )
            except Exception:
                writer.write(
                    RunFailedEvent(
                        session_id=session_id,
                        run_id=run_id,
                        sequence=0,
                        code="configuration_error",
                        message="Configuration TIA invalide.",
                    )
                )
                return 1

            try:
                return asyncio.run(
                    _stream_headless(
                        runtime,
                        prompt=prompt,
                        session_id=session_id,
                        run_id=run_id,
                        state=state,
                        writer=writer,
                    )
                )
            except Exception as exc:
                writer.write(
                    RunFailedEvent(
                        session_id=session_id,
                        run_id=run_id,
                        sequence=state.last_sequence + 1,
                        code="run_error",
                        message=(
                            runtime.redact(str(exc)) or "Le run TIA a échoué."
                        ),
                    )
                )
                return 1
    except KeyboardInterrupt:
        if not state.interrupted_event_written:
            writer.write(
                RunFailedEvent(
                    session_id=session_id,
                    run_id=run_id,
                    sequence=state.last_sequence + 1,
                    code="interrupted",
                    message="Exécution interrompue.",
                )
            )
        return state.interrupt_exit_code
    except BrokenPipeError:
        return 0
    finally:
        if previous_sigterm_handler is not None:
            signal.signal(signal.SIGTERM, previous_sigterm_handler)


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        _run_init_command()
        return
    if len(sys.argv) > 1 and sys.argv[1] == "run":
        exit_code = _run_headless_command()
        if exit_code:
            raise SystemExit(exit_code)
        return

    args = build_parser().parse_args()
    runtime = TiaRuntime.from_environment(
        workspace=args.workspace,
        model=args.model,
    )
    asyncio.run(run_interactive(runtime, args.prompt))


if __name__ == "__main__":
    main()
