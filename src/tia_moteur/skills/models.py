"""Modèles publics décrivant un dossier de skill TIA."""

from dataclasses import dataclass
from pathlib import Path
import re

from pydantic import BaseModel, ConfigDict, field_validator


SKILL_NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SkillMetadata(BaseModel):
    """Métadonnées minimales lues dans le frontmatter de ``SKILL.md``."""

    model_config = ConfigDict(extra="allow")

    name: str
    description: str

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        name = value.strip()
        if len(name) > 64 or not SKILL_NAME_PATTERN.fullmatch(name):
            raise ValueError(
                "doit contenir uniquement des minuscules, chiffres et tirets"
            )
        return name

    @field_validator("description")
    @classmethod
    def validate_description(cls, value: str) -> str:
        description = value.strip()
        if not description:
            raise ValueError("ne peut pas être vide")
        if len(description) > 2_000:
            raise ValueError("dépasse 2 000 caractères")
        return description


@dataclass(frozen=True)
class SkillDefinition:
    """Skill découvert sans injecter ses instructions complètes."""

    metadata: SkillMetadata
    root: Path
    skill_file: Path

    @property
    def name(self) -> str:
        return self.metadata.name

    @property
    def description(self) -> str:
        return self.metadata.description


class LoadedSkill(BaseModel):
    """Contenu complet remis au modèle lors de l'activation d'un skill."""

    name: str
    description: str
    root: str
    instructions: str
    files: list[str]

    def as_prompt(self) -> str:
        inventory = "\n".join(f"- {path}" for path in self.files)
        return (
            f"Skill activé: {self.name}\n"
            f"Dossier du skill: {self.root}\n"
            "Les chemins de l'inventaire sont relatifs à ce dossier. Le code "
            "embarqué doit être utilisé avec les tools disponibles; il ne reçoit "
            "aucune permission supplémentaire.\n\n"
            "Fichiers du skill:\n"
            f"{inventory}\n\n"
            "--- début SKILL.md ---\n"
            f"{self.instructions}\n"
            "--- fin SKILL.md ---"
        )
