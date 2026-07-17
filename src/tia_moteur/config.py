"""Configuration typée de l'application."""

from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Configuration finale lue depuis l'environnement composé par la CLI."""

    model_config = SettingsConfigDict(
        env_prefix="TIA_",
        extra="ignore",
    )

    model: str = "openai-responses:gpt-5.6-luna"
    workspace: Path = Field(default_factory=Path.cwd)
    setup_file: Path = Path("agent.setup.yaml")
    instructions_file: Path = Path("AGENTS.md")
    max_instructions_chars: int = Field(default=65_536, ge=1_000, le=1_000_000)
    skills_directory: Path = Path(".agents/skills")
    global_skills_directory: Path | None = Path("~/.config/tia/skills")
    max_skill_chars: int = Field(default=131_072, ge=1_000, le=1_000_000)
    max_skill_files: int = Field(default=512, ge=1, le=10_000)
    command_timeout_seconds: float = Field(default=30.0, gt=0, le=300)
    max_output_chars: int = Field(default=12_000, ge=1_000, le=1_000_000)

    @field_validator("workspace")
    @classmethod
    def validate_workspace(cls, value: Path) -> Path:
        workspace = value.expanduser().resolve()
        if not workspace.is_dir():
            raise ValueError(f"Le workspace n'existe pas: {workspace}")
        return workspace
