"""Découverte et chargement borné des dossiers de skills."""

from pathlib import Path
from typing import TYPE_CHECKING, Any

import yaml
from pydantic import ValidationError

from tia_moteur.skills.models import LoadedSkill, SkillDefinition, SkillMetadata

if TYPE_CHECKING:
    from tia_moteur.config import Settings


class SkillRegistryError(ValueError):
    """Un dossier de skill ne respecte pas le contrat TIA."""


class SkillNotFoundError(LookupError):
    """Le modèle demande un skill absent du catalogue."""


class SkillRegistry:
    """Registre de dossiers contenant chacun un ``SKILL.md`` obligatoire."""

    def __init__(
        self,
        roots: list[Path] | tuple[Path, ...],
        *,
        max_skill_chars: int,
        max_skill_files: int,
    ) -> None:
        self.roots = tuple(root.expanduser().resolve() for root in roots)
        self.max_skill_chars = max_skill_chars
        self.max_skill_files = max_skill_files

    @classmethod
    def from_settings(cls, settings: "Settings") -> "SkillRegistry":
        project_root = settings.skills_directory.expanduser()
        if not project_root.is_absolute():
            project_root = settings.workspace / project_root

        roots = [project_root]
        if settings.global_skills_directory is not None:
            roots.append(settings.global_skills_directory)

        return cls(
            roots,
            max_skill_chars=settings.max_skill_chars,
            max_skill_files=settings.max_skill_files,
        )

    def discover(self) -> dict[str, SkillDefinition]:
        """Relit les racines et retourne uniquement les définitions légères."""
        discovered: dict[str, SkillDefinition] = {}

        for registry_root in self.roots:
            if not registry_root.exists():
                continue
            if not registry_root.is_dir():
                raise SkillRegistryError(
                    f"La racine de skills n'est pas un dossier: {registry_root}"
                )

            for candidate in sorted(registry_root.iterdir()):
                if not candidate.is_dir():
                    continue
                definition = self._read_definition(registry_root, candidate)
                previous = discovered.get(definition.name)
                if previous is not None:
                    raise SkillRegistryError(
                        f"Skill dupliqué '{definition.name}': "
                        f"{previous.root} et {definition.root}"
                    )
                discovered[definition.name] = definition

        return discovered

    def catalog_prompt(
        self,
        skills: dict[str, SkillDefinition] | None = None,
    ) -> str | None:
        """Produit le catalogue léger injecté avant chaque requête modèle."""
        available = self.discover() if skills is None else skills
        if not available:
            return None

        entries = "\n".join(
            f"- {skill.name}: {skill.description}"
            for skill in available.values()
        )
        return (
            "Catalogue des skills disponibles dans ce runtime:\n"
            f"{entries}\n\n"
            "Un skill est un dossier avec SKILL.md et peut contenir du code. "
            "Ce catalogue ne contient que ses métadonnées. Avant d'appliquer un "
            "skill qui n'est pas déjà marqué comme activé, appelle load_skill avec "
            "un nom exact du catalogue, puis suis le SKILL.md retourné."
        )

    def load(
        self,
        name: str,
        skills: dict[str, SkillDefinition] | None = None,
    ) -> LoadedSkill:
        """Charge le ``SKILL.md`` complet et inventorie son code embarqué."""
        available = self.discover() if skills is None else skills
        definition = available.get(name)
        if definition is None:
            known = ", ".join(available) or "aucun"
            raise SkillNotFoundError(
                f"Skill inconnu '{name}'. Skills disponibles: {known}"
            )

        content = self._read_text(definition.skill_file)
        self._parse_skill_file(content, definition.skill_file)
        files = self._inventory(definition.root)
        return LoadedSkill(
            name=definition.name,
            description=definition.description,
            root=str(definition.root),
            instructions=content,
            files=files,
        )

    def _read_definition(
        self,
        registry_root: Path,
        candidate: Path,
    ) -> SkillDefinition:
        skill_root = candidate.resolve()
        if not skill_root.is_relative_to(registry_root):
            raise SkillRegistryError(
                f"Le dossier de skill sort de sa racine: {candidate}"
            )

        skill_file = (skill_root / "SKILL.md").resolve()
        if not skill_file.is_relative_to(skill_root):
            raise SkillRegistryError(
                f"SKILL.md sort du dossier de skill: {candidate}"
            )
        if not skill_file.is_file():
            raise SkillRegistryError(
                f"SKILL.md obligatoire introuvable dans: {candidate}"
            )

        content = self._read_text(skill_file)
        metadata = self._parse_skill_file(content, skill_file)
        if metadata.name != candidate.name:
            raise SkillRegistryError(
                f"Le skill '{metadata.name}' doit être dans un dossier du même "
                f"nom, pas '{candidate.name}'"
            )
        return SkillDefinition(
            metadata=metadata,
            root=skill_root,
            skill_file=skill_file,
        )

    def _read_text(self, path: Path) -> str:
        try:
            content = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            raise SkillRegistryError(f"Le fichier n'est pas en UTF-8: {path}") from exc
        if len(content) > self.max_skill_chars:
            raise SkillRegistryError(
                f"SKILL.md dépasse {self.max_skill_chars} caractères: {path}"
            )
        return content

    def _parse_skill_file(self, content: str, path: Path) -> SkillMetadata:
        lines = content.splitlines()
        if not lines or lines[0].strip() != "---":
            raise SkillRegistryError(
                f"SKILL.md doit commencer par un frontmatter YAML: {path}"
            )

        closing_index = next(
            (index for index, line in enumerate(lines[1:], start=1) if line.strip() == "---"),
            None,
        )
        if closing_index is None:
            raise SkillRegistryError(f"Frontmatter YAML non fermé: {path}")

        frontmatter = "\n".join(lines[1:closing_index])
        instructions = "\n".join(lines[closing_index + 1 :]).strip()
        if not instructions:
            raise SkillRegistryError(f"SKILL.md ne contient aucune instruction: {path}")

        try:
            raw_metadata: Any = yaml.safe_load(frontmatter)
            metadata = SkillMetadata.model_validate(raw_metadata)
        except (yaml.YAMLError, ValidationError) as exc:
            raise SkillRegistryError(
                f"Métadonnées invalides dans SKILL.md: {path}: {exc}"
            ) from exc
        return metadata

    def _inventory(self, skill_root: Path) -> list[str]:
        files: list[str] = []
        for item in sorted(skill_root.rglob("*")):
            resolved = item.resolve()
            if not resolved.is_relative_to(skill_root):
                raise SkillRegistryError(
                    f"Un fichier du skill sort de son dossier: {item}"
                )
            if item.is_file():
                files.append(str(item.relative_to(skill_root)))
                if len(files) > self.max_skill_files:
                    raise SkillRegistryError(
                        f"Le skill dépasse {self.max_skill_files} fichiers: {skill_root}"
                    )
        return files
