"""Assemblage réutilisable du moteur TIA, sans dépendance au terminal."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from copy import deepcopy
from dataclasses import dataclass, field
import os
from pathlib import Path
from types import MappingProxyType
from typing import Any, Protocol
from uuid import uuid4

from pydantic_ai import ModelMessage
from pydantic_ai.models import Model
from pydantic_ai.tools import Tool
from pydantic_core import PydanticUndefined

from tia_moteur.agent import build_agent
from tia_moteur.agent_setup import AgentSetup, load_layered_agent_setup
from tia_moteur.config import Settings
from tia_moteur.credentials import (
    ChainedCredentials,
    CredentialProvider,
    MappingCredentials,
    as_credentials,
    redact_text,
)
from tia_moteur.portable_config import (
    RuntimePaths,
    build_runtime_paths,
    compose_environment,
    get_config_home,
    initialize_global_config,
    read_env_file,
    resolve_workspace,
)
from tia_moteur.session import TiaSession
from tia_moteur.session_store import MemorySessionStore, SessionStore
from tia_moteur.skills import SkillRegistry
from tia_moteur.workspace_instructions import WorkspaceInstructionsLoader


AgentTool = Tool[Any] | Callable[..., Any]


@dataclass(frozen=True)
class SessionContext:
    """Contexte remis aux factories, distinct pour chaque session."""

    session_id: str
    settings: Settings
    paths: RuntimePaths
    setup: AgentSetup
    credentials: CredentialProvider = field(repr=False)


class ToolFactory(Protocol):
    def __call__(self, context: SessionContext) -> Sequence[AgentTool]: ...


class ModelFactory(Protocol):
    def __call__(self, context: SessionContext) -> Model | str: ...


@dataclass(frozen=True)
class RuntimeInfo:
    model: str
    workspace: Path
    config_home: Path
    instruction_paths: tuple[Path, ...]
    skills_enabled: bool
    available_skills: int


class TiaRuntime:
    """Configure TIA et crée des sessions isolées et injectables."""

    def __init__(
        self,
        settings: Settings,
        paths: RuntimePaths,
        setup: AgentSetup,
        *,
        model: Model | str | None = None,
        model_factory: ModelFactory | None = None,
        tools: Sequence[AgentTool] = (),
        tool_factories: Sequence[ToolFactory] = (),
        session_store: SessionStore | None = None,
        credentials: CredentialProvider | Mapping[str, str] | None = None,
        command_environment: Mapping[str, str] | None = None,
    ) -> None:
        if model is not None and model_factory is not None:
            raise ValueError("Utilise model ou model_factory, pas les deux.")
        self.settings = settings
        self.paths = paths
        self.setup = setup
        self.skill_registry = SkillRegistry.from_settings(settings)
        self._model = model
        self._model_factory = model_factory or _configured_model
        self._tools = tuple(tools)
        self._tool_factories = tuple(tool_factories)
        self.session_store = (
            session_store if session_store is not None else MemorySessionStore()
        )
        self.credentials = as_credentials(credentials)
        resolved_command_environment = MappingProxyType(
            dict(command_environment or {})
        )
        self._command_environment = (
            resolved_command_environment if command_environment is not None else None
        )

    @classmethod
    def from_environment(
        cls,
        *,
        workspace: Path | str | None = None,
        model: Model | str | None = None,
        environment: Mapping[str, str] | None = None,
        current_directory: Path | str | None = None,
        config_home: Path | str | None = None,
        model_factory: ModelFactory | None = None,
        tools: Sequence[AgentTool] = (),
        tool_factories: Sequence[ToolFactory] = (),
        session_store: SessionStore | None = None,
        credentials: CredentialProvider | Mapping[str, str] | None = None,
    ) -> "TiaRuntime":
        """Résout les couches globales/projet sans modifier ``os.environ``."""
        process_environment = dict(os.environ if environment is None else environment)
        resolved_home = (
            Path(config_home)
            if config_home is not None
            else get_config_home(process_environment)
        )
        initialization = initialize_global_config(resolved_home)
        global_environment = read_env_file(initialization.root / ".env")
        resolved_workspace = resolve_workspace(
            Path(workspace) if workspace is not None else None,
            process_environment,
            global_environment,
            current_directory=(
                Path(current_directory) if current_directory is not None else None
            ),
        )
        project_environment = read_env_file(resolved_workspace / ".env")
        effective_environment = compose_environment(
            global_environment,
            project_environment,
            process_environment,
        )

        settings_values = _settings_from_environment(effective_environment)
        settings_values["workspace"] = resolved_workspace
        if isinstance(model, str):
            settings_values["model"] = model
        if "TIA_GLOBAL_SKILLS_DIRECTORY" not in effective_environment:
            settings_values["global_skills_directory"] = initialization.root / "skills"
        settings = Settings.model_validate(settings_values)
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
        runtime_credentials = (
            credentials
            if credentials is not None
            else MappingCredentials(effective_environment, redact_all=False)
        )
        direct_model = model if isinstance(model, Model) else None
        return cls(
            settings,
            paths,
            setup,
            model=direct_model,
            model_factory=model_factory,
            tools=tools,
            tool_factories=tool_factories,
            session_store=session_store,
            credentials=runtime_credentials,
            command_environment=effective_environment,
        )

    def create_session(
        self,
        *,
        session_id: str | None = None,
        credentials: CredentialProvider | Mapping[str, str] | None = None,
        history: Sequence[ModelMessage] | None = None,
    ) -> TiaSession:
        """Crée un agent par session pour isoler factories et credentials."""
        active_session_id = session_id or str(uuid4())
        active_credentials = self.credentials
        if credentials is not None:
            active_credentials = ChainedCredentials(
                as_credentials(credentials),
                self.credentials,
            )
        context = SessionContext(
            session_id=active_session_id,
            settings=self.settings,
            paths=self.paths,
            setup=self.setup,
            credentials=active_credentials,
        )
        model = self._model if self._model is not None else self._model_factory(context)
        model_name = model.model_name if isinstance(model, Model) else str(model)
        injected_tools = list(self._tools)
        for factory in self._tool_factories:
            injected_tools.extend(factory(context))
        _validate_tool_names(injected_tools, self.setup)
        agent = build_agent(
            self.settings,
            self.skill_registry,
            self.setup,
            model=model,
            additional_tools=injected_tools,
            command_environment=self._command_environment,
        )
        return TiaSession(
            session_id=active_session_id,
            agent=agent,
            settings=self.settings,
            paths=self.paths,
            setup=self.setup,
            skill_registry=self.skill_registry,
            store=self.session_store,
            credentials=active_credentials,
            model_name=model_name,
            initial_history=history,
        )

    async def resume_session(
        self,
        session_id: str,
        *,
        credentials: CredentialProvider | Mapping[str, str] | None = None,
    ) -> TiaSession:
        session = self.create_session(
            session_id=session_id,
            credentials=credentials,
        )
        await session.load()
        return session

    def inspect(self) -> RuntimeInfo:
        """Retourne les informations utiles aux adaptateurs humains."""
        instruction_paths = []
        for loader in (
            WorkspaceInstructionsLoader(
                workspace=self.paths.config_home,
                instructions_file=Path("AGENTS.md"),
                max_chars=self.settings.max_instructions_chars,
                scope="globales de TIA",
            ),
            WorkspaceInstructionsLoader(
                workspace=self.settings.workspace,
                instructions_file=self.settings.instructions_file,
                max_chars=self.settings.max_instructions_chars,
                scope="du projet",
            ),
        ):
            instructions = loader.load()
            if instructions is not None:
                instruction_paths.append(instructions.path)
        available_skills = (
            len(self.skill_registry.discover()) if self.setup.skills.enabled else 0
        )
        if isinstance(self._model, Model):
            displayed_model = self._model.model_name
        elif isinstance(self._model, str):
            displayed_model = self._model
        else:
            displayed_model = self.settings.model
        return RuntimeInfo(
            model=displayed_model,
            workspace=self.settings.workspace,
            config_home=self.paths.config_home,
            instruction_paths=tuple(instruction_paths),
            skills_enabled=self.setup.skills.enabled,
            available_skills=available_skills,
        )

    def redact(self, text: str) -> str:
        """Masque les credentials connus dans un diagnostic public."""
        return redact_text(self.credentials, text)


def _settings_from_environment(environment: Mapping[str, str]) -> dict[str, Any]:
    values: dict[str, Any] = {}
    for field_name, field_info in Settings.model_fields.items():
        environment_name = f"TIA_{field_name.upper()}"
        if environment_name in environment:
            values[field_name] = environment[environment_name]
            continue
        default = field_info.get_default(call_default_factory=True)
        if default is not PydanticUndefined:
            values[field_name] = deepcopy(default)
    return values


def _configured_model(context: SessionContext) -> Model | str:
    """Injecte explicitement les credentials OpenAI lus depuis les couches dotenv."""
    configured = context.settings.model
    if ":" not in configured:
        return configured
    provider_name, model_name = configured.split(":", maxsplit=1)
    if provider_name not in {"openai", "openai-chat", "openai-responses"}:
        return configured

    api_key = context.credentials.get("OPENAI_API_KEY")
    base_url = context.credentials.get("OPENAI_BASE_URL")
    if not api_key and not base_url:
        return configured

    from pydantic_ai.providers.openai import OpenAIProvider

    provider = OpenAIProvider(api_key=api_key, base_url=base_url)
    if provider_name == "openai-responses":
        from pydantic_ai.models.openai import OpenAIResponsesModel

        return OpenAIResponsesModel(model_name, provider=provider)

    from pydantic_ai.models.openai import OpenAIChatModel

    return OpenAIChatModel(model_name, provider=provider)


def _validate_tool_names(tools: Sequence[AgentTool], setup: AgentSetup) -> None:
    names = []
    if setup.tools.bash.enabled:
        names.append("run_bash")
    if setup.skills.enabled:
        names.append("load_skill")
    for tool in tools:
        name = tool.name if isinstance(tool, Tool) else getattr(tool, "__name__", None)
        if not isinstance(name, str) or not name:
            raise ValueError("Un tool injecté doit avoir un nom stable.")
        names.append(name)
    duplicates = sorted(name for name, count in Counter(names).items() if count > 1)
    if duplicates:
        raise ValueError(f"Tools dupliqués: {', '.join(duplicates)}")
