#!/usr/bin/env python3
"""Valide un dossier de skill selon le contrat du runtime TIA."""

from __future__ import annotations

import argparse
from pathlib import Path
import re
import sys
from typing import Any

import yaml


MAX_NAME_LENGTH = 64
MAX_SKILL_CHARS = 131_072
MAX_FILES = 512
NAME_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class SkillValidationError(ValueError):
    """Le dossier ne respecte pas le contrat TIA."""


def parse_skill_md(skill_file: Path) -> tuple[dict[str, Any], str]:
    """Lit et valide le frontmatter ainsi que le corps de ``SKILL.md``."""
    try:
        content = skill_file.read_text(encoding="utf-8")
    except UnicodeDecodeError as exc:
        raise SkillValidationError("SKILL.md doit être encodé en UTF-8") from exc

    if len(content) > MAX_SKILL_CHARS:
        raise SkillValidationError(
            f"SKILL.md dépasse {MAX_SKILL_CHARS} caractères"
        )

    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillValidationError(
            "SKILL.md doit commencer par un frontmatter YAML"
        )

    closing_index = next(
        (
            index
            for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        ),
        None,
    )
    if closing_index is None:
        raise SkillValidationError("le frontmatter YAML de SKILL.md n'est pas fermé")

    try:
        metadata = yaml.safe_load("\n".join(lines[1:closing_index]))
    except yaml.YAMLError as exc:
        raise SkillValidationError(f"frontmatter YAML invalide: {exc}") from exc
    if not isinstance(metadata, dict):
        raise SkillValidationError("le frontmatter doit être un objet YAML")

    unexpected = set(metadata) - {"name", "description"}
    if unexpected:
        raise SkillValidationError(
            "clés de frontmatter non autorisées: " + ", ".join(sorted(unexpected))
        )

    instructions = "\n".join(lines[closing_index + 1 :]).strip()
    if not instructions:
        raise SkillValidationError("SKILL.md doit contenir des instructions")
    return metadata, instructions


def validate_skill(skill_path: Path) -> tuple[str, int]:
    """Valide le dossier et retourne son nom ainsi que son nombre de fichiers."""
    if not skill_path.exists():
        raise SkillValidationError(f"dossier introuvable: {skill_path}")
    if not skill_path.is_dir():
        raise SkillValidationError(f"le chemin n'est pas un dossier: {skill_path}")

    skill_root = skill_path.resolve()
    skill_file = (skill_root / "SKILL.md").resolve()
    if not skill_file.is_relative_to(skill_root) or not skill_file.is_file():
        raise SkillValidationError("SKILL.md obligatoire introuvable dans le dossier")

    metadata, _ = parse_skill_md(skill_file)
    name = metadata.get("name")
    description = metadata.get("description")

    if not isinstance(name, str) or not name.strip():
        raise SkillValidationError("name est obligatoire et doit être une chaîne")
    name = name.strip()
    if len(name) > MAX_NAME_LENGTH or not NAME_PATTERN.fullmatch(name):
        raise SkillValidationError(
            "name doit utiliser uniquement des minuscules, chiffres et tirets "
            f"sur {MAX_NAME_LENGTH} caractères maximum"
        )
    if name != skill_root.name:
        raise SkillValidationError(
            f"name '{name}' doit être identique au dossier '{skill_root.name}'"
        )

    if not isinstance(description, str) or not description.strip():
        raise SkillValidationError(
            "description est obligatoire et doit être une chaîne non vide"
        )

    file_count = 0
    for item in sorted(skill_root.rglob("*")):
        resolved = item.resolve()
        if not resolved.is_relative_to(skill_root):
            raise SkillValidationError(
                f"un fichier ou lien sort du dossier du skill: {item}"
            )
        if item.is_file():
            file_count += 1
            if file_count > MAX_FILES:
                raise SkillValidationError(
                    f"le skill dépasse la limite de {MAX_FILES} fichiers"
                )

    return name, file_count


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("skill_directory", type=Path)
    args = parser.parse_args()

    try:
        name, file_count = validate_skill(args.skill_directory)
    except (OSError, SkillValidationError) as exc:
        print(f"[ERREUR] {exc}", file=sys.stderr)
        return 1

    print(f"[OK] Skill valide: {name} ({file_count} fichiers)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
