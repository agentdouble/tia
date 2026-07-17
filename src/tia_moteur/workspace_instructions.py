"""Chargement déterministe des instructions propres au workspace."""

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path


class WorkspaceInstructionsError(ValueError):
    """Le fichier d'instructions ne peut pas être chargé en sécurité."""


@dataclass(frozen=True)
class WorkspaceInstructions:
    """Instructions chargées et prêtes à être injectées dans un run."""

    path: Path
    content: str
    digest: str
    scope: str = "du workspace"

    def as_prompt(self) -> str:
        return (
            f"Instructions {self.scope}, chargées par le runtime. "
            "Elles complètent les instructions générales de l'agent.\n\n"
            f"Source: {self.path}\n"
            "--- début AGENTS.md ---\n"
            f"{self.content}\n"
            "--- fin AGENTS.md ---"
        )


class WorkspaceInstructionsLoader:
    """Relit un unique ``AGENTS.md`` racine avant chaque tour utilisateur."""

    def __init__(
        self,
        workspace: Path,
        instructions_file: Path,
        max_chars: int,
        scope: str = "du workspace",
    ) -> None:
        self.workspace = workspace.resolve()
        self.instructions_file = instructions_file
        self.max_chars = max_chars
        self.scope = scope

    @property
    def configured_path(self) -> Path:
        return self.workspace / self.instructions_file

    def load(self) -> WorkspaceInstructions | None:
        path = self.configured_path.resolve()
        if not path.is_relative_to(self.workspace):
            raise WorkspaceInstructionsError(
                f"Le fichier d'instructions sort du workspace: {path}"
            )
        if not path.exists():
            return None
        if not path.is_file():
            raise WorkspaceInstructionsError(
                f"Le chemin d'instructions n'est pas un fichier: {path}"
            )

        try:
            content = path.read_text(encoding="utf-8").strip()
        except UnicodeDecodeError as exc:
            raise WorkspaceInstructionsError(
                f"Le fichier d'instructions n'est pas en UTF-8: {path}"
            ) from exc

        if not content:
            raise WorkspaceInstructionsError(
                f"Le fichier d'instructions est vide: {path}"
            )
        if len(content) > self.max_chars:
            raise WorkspaceInstructionsError(
                "Le fichier d'instructions dépasse la limite de "
                f"{self.max_chars} caractères: {path}"
            )

        return WorkspaceInstructions(
            path=path,
            content=content,
            digest=sha256(content.encode("utf-8")).hexdigest(),
            scope=self.scope,
        )
