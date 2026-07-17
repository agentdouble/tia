"""Activation explicite et composition du prompt runtime des skills."""

from dataclasses import dataclass
import re

from tia_moteur.skills.models import LoadedSkill
from tia_moteur.skills.registry import SkillRegistry


EXPLICIT_SKILL_PATTERN = re.compile(
    r"(?<![A-Za-z0-9_$])\$([a-z0-9]+(?:-[a-z0-9]+)*)"
)


@dataclass(frozen=True)
class SkillRunContext:
    """Catalogue courant et skills préchargés pour un tour utilisateur."""

    prompt: str | None
    activated: tuple[LoadedSkill, ...]
    available_count: int


def build_skill_run_context(
    registry: SkillRegistry,
    user_prompt: str,
) -> SkillRunContext:
    """Relit le registre et précharge les ``$skills`` explicitement demandés."""
    available = registry.discover()
    requested_names: list[str] = []
    for match in EXPLICIT_SKILL_PATTERN.finditer(user_prompt):
        name = match.group(1)
        if name in available and name not in requested_names:
            requested_names.append(name)

    activated = tuple(registry.load(name, available) for name in requested_names)
    prompt_parts = [part for part in [registry.catalog_prompt(available)] if part]
    prompt_parts.extend(skill.as_prompt() for skill in activated)

    return SkillRunContext(
        prompt="\n\n".join(prompt_parts) or None,
        activated=activated,
        available_count=len(available),
    )


def combine_runtime_instructions(*parts: str | None) -> str | None:
    """Assemble les couches d'instructions sans sections vides."""
    included = [part for part in parts if part]
    return "\n\n".join(included) or None
