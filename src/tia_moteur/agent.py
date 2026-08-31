"""Construction de l'agent et assemblage injectable de ses outils."""

from collections.abc import Mapping, Sequence
from typing import Any

from pydantic_ai import Agent
from pydantic_ai.models import Model
from pydantic_ai.toolsets import AbstractToolset

from tia_moteur.agent_setup import AgentSetup, load_agent_setup
from tia_moteur.config import Settings
from tia_moteur.skills import SkillRegistry, create_load_skill_tool
from tia_moteur.tools import BashExecutor, BashPolicy, create_bash_tool


AGENT_INSTRUCTIONS = """
Tu es un agent local capable d'inspecter et de modifier son environnement.
Utilise run_bash lorsque la demande nécessite de lire des fichiers, lancer une
commande ou vérifier un résultat. Travaille dans le workspace configuré.

Le runtime peut ajouter les instructions globales de TIA puis celles du fichier
AGENTS.md du projet. Elles complètent ces instructions générales. Les capacités
techniques réelles restent exclusivement déterminées par les tools et leur
configuration fusionnée.

Le runtime peut aussi injecter un catalogue de skills. Un skill est toujours un
dossier avec un fichier SKILL.md obligatoire et peut embarquer du code. Charge ses
instructions avec load_skill avant de l'appliquer, sauf si le runtime l'a déjà
explicitement marqué comme activé. Le code d'un skill utilise uniquement les tools
déjà disponibles et n'ajoute jamais implicitement de permissions.

Avant une modification, inspecte les fichiers concernés. Après une modification,
vérifie le résultat avec la commande adaptée. N'invente jamais la sortie d'une
commande. Évite les commandes destructrices sauf demande explicite de l'utilisateur.
Réponds de manière concise et indique clairement ce qui a réellement été fait.
""".strip()


def build_agent(
    settings: Settings,
    skill_registry: SkillRegistry | None = None,
    setup: AgentSetup | None = None,
    *,
    model: Model | str | None = None,
    additional_tools: Sequence[Any] = (),
    additional_toolsets: Sequence[AbstractToolset[Any]] = (),
    command_environment: Mapping[str, str] | None = None,
) -> Agent[None, str]:
    """Construit un agent avec les capacités natives puis les extensions injectées."""
    active_setup = setup or load_agent_setup(settings.setup_file)
    tools: list[Any] = []

    if active_setup.tools.bash.enabled:
        bash_executor = BashExecutor(
            workspace=settings.workspace,
            timeout_seconds=settings.command_timeout_seconds,
            max_output_chars=settings.max_output_chars,
            policy=BashPolicy(active_setup.tools.bash),
            environment=command_environment,
        )
        tools.append(create_bash_tool(bash_executor))

    if active_setup.skills.enabled:
        registry = skill_registry or SkillRegistry.from_settings(settings)
        tools.append(create_load_skill_tool(registry))

    tools.extend(additional_tools)

    return Agent(
        settings.model if model is None else model,
        instructions=AGENT_INSTRUCTIONS,
        tools=tools,
        toolsets=additional_toolsets,
        defer_model_check=True,
    )
