"""Tests du registre, du code embarqué et de l'activation des skills."""

from pathlib import Path

import pytest

from tia_moteur.skills import (
    SkillNotFoundError,
    SkillRegistry,
    SkillRegistryError,
    build_skill_run_context,
)


def write_skill(
    root: Path,
    name: str = "python-quality",
    *,
    description: str = "Vérifie la qualité d'un projet Python.",
    instructions: str = "# Procédure\n\nExécute scripts/check.py.",
) -> Path:
    skill_root = root / name
    skill_root.mkdir(parents=True)
    (skill_root / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        "---\n\n"
        f"{instructions}\n",
        encoding="utf-8",
    )
    scripts = skill_root / "scripts"
    scripts.mkdir()
    (scripts / "check.py").write_text("print('ok')\n", encoding="utf-8")
    return skill_root


def make_registry(*roots: Path, max_files: int = 512) -> SkillRegistry:
    return SkillRegistry(
        list(roots),
        max_skill_chars=10_000,
        max_skill_files=max_files,
    )


def test_discovers_metadata_without_injecting_instructions(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    write_skill(root)
    registry = make_registry(root)

    discovered = registry.discover()
    catalog = registry.catalog_prompt(discovered)

    assert list(discovered) == ["python-quality"]
    assert catalog is not None
    assert "Vérifie la qualité" in catalog
    assert "scripts/check.py" not in catalog
    assert "Exécute scripts" not in catalog


def test_loads_skill_md_and_inventory_including_code(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill_root = write_skill(root)
    registry = make_registry(root)

    loaded = registry.load("python-quality")

    assert loaded.root == str(skill_root)
    assert "Exécute scripts/check.py" in loaded.instructions
    assert loaded.files == ["SKILL.md", "scripts/check.py"]
    assert "scripts/check.py" in loaded.as_prompt()


def test_explicit_dollar_activation_preloads_full_skill(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    write_skill(root)
    registry = make_registry(root)

    context = build_skill_run_context(
        registry,
        "$python-quality vérifie ce projet",
    )

    assert [skill.name for skill in context.activated] == ["python-quality"]
    assert context.prompt is not None
    assert "--- début SKILL.md ---" in context.prompt
    assert "Exécute scripts/check.py" in context.prompt


def test_shell_variable_is_not_treated_as_unknown_skill(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    write_skill(root)

    context = build_skill_run_context(make_registry(root), "affiche $HOME")

    assert context.activated == ()


def test_rejects_folder_without_skill_md(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    (root / "incomplet").mkdir(parents=True)

    with pytest.raises(SkillRegistryError, match="SKILL.md obligatoire"):
        make_registry(root).discover()


def test_rejects_name_different_from_folder(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill_root = write_skill(root, "bon-nom")
    content = (skill_root / "SKILL.md").read_text(encoding="utf-8")
    (skill_root / "SKILL.md").write_text(
        content.replace("name: bon-nom", "name: autre-nom"),
        encoding="utf-8",
    )

    with pytest.raises(SkillRegistryError, match="dossier du même nom"):
        make_registry(root).discover()


def test_rejects_duplicate_names_across_roots(tmp_path: Path) -> None:
    first = tmp_path / "project"
    second = tmp_path / "global"
    write_skill(first)
    write_skill(second)

    with pytest.raises(SkillRegistryError, match="Skill dupliqué"):
        make_registry(first, second).discover()


def test_rejects_file_symlink_outside_skill(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    skill_root = write_skill(root)
    outside = tmp_path / "secret.py"
    outside.write_text("secret = True\n", encoding="utf-8")
    (skill_root / "scripts" / "secret.py").symlink_to(outside)

    with pytest.raises(SkillRegistryError, match="sort de son dossier"):
        make_registry(root).load("python-quality")


def test_rejects_too_many_files(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    write_skill(root)

    with pytest.raises(SkillRegistryError, match="dépasse 1 fichiers"):
        make_registry(root, max_files=1).load("python-quality")


def test_unknown_skill_returns_available_names(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    write_skill(root)

    with pytest.raises(SkillNotFoundError, match="python-quality"):
        make_registry(root).load("inconnu")


def test_installed_skill_creator_is_complete() -> None:
    project_root = Path(__file__).resolve().parents[1]
    registry = make_registry(project_root / ".agents" / "skills")

    loaded = registry.load("skill-creator")

    assert "scripts/init_skill.py" in loaded.files
    assert "scripts/quick_validate.py" in loaded.files
    assert "references/skill-format.md" in loaded.files
