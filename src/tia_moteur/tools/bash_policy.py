"""Validation des commandes autorisées par la politique Bash."""

import shlex
from pathlib import Path

from tia_moteur.agent_setup import BashToolSetup


SHELL_PUNCTUATION = ";&|<>()`"


class CommandRejectedError(ValueError):
    """Commande refusée avant toute création de processus."""


class BashPolicy:
    """Transforme une commande autorisée en argv sûr pour une exécution directe."""

    def __init__(self, setup: BashToolSetup) -> None:
        self.setup = setup

    def prepare(self, command: str) -> list[str]:
        if self.setup.execution_mode == "unrestricted_shell":
            return [str(self.setup.shell), "-lc", command]

        tokens = self._tokenize(command)
        if not tokens:
            raise CommandRejectedError("La commande est vide.")

        executable_token = tokens[0]
        executable = Path(executable_token).name

        if executable in self.setup.forbidden_executables:
            raise CommandRejectedError(f"L'exécutable '{executable}' est interdit.")
        if "/" in executable_token:
            raise CommandRejectedError("Les chemins d'exécutables explicites sont interdits.")
        if executable not in self.setup.allowed_executables:
            raise CommandRejectedError(f"L'exécutable '{executable}' n'est pas autorisé.")

        arguments = tokens[1:]
        self._validate_command_rule(executable, arguments)
        return tokens

    def _tokenize(self, command: str) -> list[str]:
        if "\n" in command or "\r" in command:
            raise CommandRejectedError("Les commandes multilignes sont interdites.")

        lexer = shlex.shlex(
            command,
            posix=True,
            punctuation_chars=SHELL_PUNCTUATION,
        )
        lexer.whitespace_split = True
        lexer.commenters = ""

        try:
            tokens = list(lexer)
        except ValueError as exc:
            raise CommandRejectedError(f"Commande invalide: {exc}") from exc

        for token in tokens:
            if token and all(character in SHELL_PUNCTUATION for character in token):
                raise CommandRejectedError(
                    f"L'opérateur shell '{token}' est interdit en mode direct."
                )
        return tokens

    def _validate_command_rule(self, executable: str, arguments: list[str]) -> None:
        rule = self.setup.command_rules.get(executable)
        if rule is None:
            return

        if rule.allowed_subcommands is not None:
            if not arguments or arguments[0] not in rule.allowed_subcommands:
                allowed = ", ".join(rule.allowed_subcommands)
                raise CommandRejectedError(
                    f"Sous-commande interdite pour '{executable}'. Autorisées: {allowed}."
                )

        for argument in arguments:
            if argument in rule.forbidden_arguments:
                raise CommandRejectedError(
                    f"L'argument '{argument}' est interdit pour '{executable}'."
                )
            if any(
                argument.startswith(prefix)
                for prefix in rule.forbidden_argument_prefixes
            ):
                raise CommandRejectedError(
                    f"L'argument '{argument}' est interdit pour '{executable}'."
                )
