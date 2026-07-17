"""Tool permettant au modèle de charger un skill découvert."""

from collections.abc import Callable

from tia_moteur.skills.models import LoadedSkill
from tia_moteur.skills.registry import SkillRegistry


def create_load_skill_tool(
    registry: SkillRegistry,
) -> Callable[[str], LoadedSkill]:
    """Crée le tool ``load_skill`` lié au registre courant."""

    def load_skill(name: str) -> LoadedSkill:
        """Charge les instructions et l'inventaire d'un skill disponible.

        Args:
            name: Nom exact d'un skill présent dans le catalogue injecté.
        """
        return registry.load(name)

    return load_skill
