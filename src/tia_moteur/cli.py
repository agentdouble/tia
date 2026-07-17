"""Interface en ligne de commande de l'agent."""

import argparse
import asyncio
import os
from pathlib import Path
import sys

from pydantic_ai import (
    AgentRunResultEvent,
    FunctionToolCallEvent,
    ModelMessage,
)
from tia_moteur.agent import build_agent
from tia_moteur.agent_setup import AgentSetup, load_layered_agent_setup
from tia_moteur.config import Settings
from tia_moteur.portable_config import (
    RuntimePaths,
    build_runtime_paths,
    compose_environment,
    get_config_home,
    initialize_global_config,
    read_env_file,
    resolve_workspace,
)
from tia_moteur.skills import (
    SkillRegistry,
    build_skill_run_context,
    combine_runtime_instructions,
)
from tia_moteur.tool_display import format_tool_call
from tia_moteur.workspace_instructions import WorkspaceInstructionsLoader


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tia",
        description="Agent local Pydantic AI avec accès à un tool Bash.",
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


async def run_interactive(
    settings: Settings,
    first_prompt: str | None,
    *,
    paths: RuntimePaths,
    setup: AgentSetup,
) -> None:
    skill_registry = SkillRegistry.from_settings(settings)
    agent = build_agent(settings, skill_registry, setup)
    global_instructions_loader = WorkspaceInstructionsLoader(
        workspace=paths.config_home,
        instructions_file=Path("AGENTS.md"),
        max_chars=settings.max_instructions_chars,
        scope="globales de TIA",
    )
    project_instructions_loader = WorkspaceInstructionsLoader(
        workspace=settings.workspace,
        instructions_file=settings.instructions_file,
        max_chars=settings.max_instructions_chars,
        scope="du projet",
    )
    history: list[ModelMessage] = []
    prompt = first_prompt

    initial_global_instructions = global_instructions_loader.load()
    initial_project_instructions = project_instructions_loader.load()
    initial_skill_context = (
        build_skill_run_context(skill_registry, "")
        if setup.skills.enabled
        else None
    )
    instructions_digests = {
        "global": (
            initial_global_instructions.digest
            if initial_global_instructions is not None
            else None
        ),
        "project": (
            initial_project_instructions.digest
            if initial_project_instructions is not None
            else None
        ),
    }

    print(f"TIA — modèle: {settings.model}")
    print(f"Workspace: {settings.workspace}")
    print(f"Configuration globale: {paths.config_home}")
    instruction_paths = [
        instructions.path
        for instructions in (
            initial_global_instructions,
            initial_project_instructions,
        )
        if instructions is not None
    ]
    if not instruction_paths:
        print("Instructions: prompt de base uniquement")
    else:
        sources = " + ".join(str(path) for path in instruction_paths)
        print(f"Instructions: prompt de base + {sources}")
    if initial_skill_context is None:
        print("Skills: désactivés par agent.setup.yaml")
    else:
        print(f"Skills: {initial_skill_context.available_count} détecté(s)")

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

        try:
            global_instructions = global_instructions_loader.load()
            project_instructions = project_instructions_loader.load()
            current_instruction_layers = {
                "global": global_instructions,
                "project": project_instructions,
            }
            for layer, instructions in current_instruction_layers.items():
                current_digest = instructions.digest if instructions is not None else None
                if current_digest != instructions_digests[layer]:
                    if instructions is None:
                        print(f"\n[instructions] AGENTS.md {layer} retiré")
                    else:
                        print(f"\n[instructions] rechargé: {instructions.path}")
                    instructions_digests[layer] = current_digest

            skill_context = (
                build_skill_run_context(skill_registry, prompt)
                if setup.skills.enabled
                else None
            )
            if skill_context is not None:
                for skill in skill_context.activated:
                    print(f"\n[skill] {skill.name}")

            result = None
            async with agent.run_stream_events(
                prompt,
                message_history=history or None,
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
                async for event in stream:
                    if isinstance(event, FunctionToolCallEvent):
                        print(format_tool_call(event.part))
                    elif isinstance(event, AgentRunResultEvent):
                        result = event.result
        except Exception as exc:  # CLI: transformer l'erreur provider en message lisible.
            print(f"\nErreur: {exc}")
        else:
            if result is None:
                raise RuntimeError("L'agent s'est terminé sans résultat final.")
            print(f"\nTIA > {result.output}")
            history = result.all_messages()

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


def main() -> None:
    if len(sys.argv) > 1 and sys.argv[1] == "init":
        _run_init_command()
        return

    initialization = initialize_global_config()
    args = build_parser().parse_args()
    process_environment = dict(os.environ)
    global_environment = read_env_file(initialization.root / ".env")
    workspace = resolve_workspace(
        args.workspace,
        process_environment,
        global_environment,
    )
    project_environment = read_env_file(workspace / ".env")
    effective_environment = compose_environment(
        global_environment,
        project_environment,
        process_environment,
    )
    os.environ.update(effective_environment)

    overrides = {}
    overrides["workspace"] = workspace
    if args.model:
        overrides["model"] = args.model
    if "TIA_GLOBAL_SKILLS_DIRECTORY" not in effective_environment:
        overrides["global_skills_directory"] = initialization.root / "skills"

    settings = Settings(**overrides)
    global_skills_directory = (
        settings.global_skills_directory or initialization.root / "skills"
    )
    paths = build_runtime_paths(
        initialization.root,
        settings.workspace,
        setup_file=settings.setup_file,
        instructions_file=settings.instructions_file,
        skills_directory=settings.skills_directory,
        global_skills_directory=global_skills_directory,
    )
    setup = load_layered_agent_setup(paths.global_setup, paths.project_setup)
    asyncio.run(
        run_interactive(
            settings,
            args.prompt,
            paths=paths,
            setup=setup,
        )
    )


if __name__ == "__main__":
    main()
