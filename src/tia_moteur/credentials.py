"""Injection de credentials sans exposition dans les événements ou les repr."""

from __future__ import annotations

from collections.abc import Mapping
from types import MappingProxyType
from typing import Protocol


SENSITIVE_NAME_PARTS = (
    "API_KEY",
    "AUTH",
    "CREDENTIAL",
    "COOKIE",
    "PASSWORD",
    "SECRET",
    "TOKEN",
    "PRIVATE_KEY",
    "DSN",
    "DATABASE_URL",
    "URI",
)


class CredentialProvider(Protocol):
    """Contrat minimal utilisé par les factories de modèle et de tools."""

    def get(self, name: str, default: str | None = None) -> str | None: ...

    def redact(self, text: str) -> str: ...


class MappingCredentials:
    """Credentials immuables dont la représentation masque les valeurs."""

    __slots__ = ("_redact_all", "_values")

    def __init__(
        self,
        values: Mapping[str, str] | None = None,
        *,
        redact_all: bool = True,
    ) -> None:
        self._values = MappingProxyType(dict(values or {}))
        self._redact_all = redact_all

    def get(self, name: str, default: str | None = None) -> str | None:
        return self._values.get(name, default)

    def require(self, name: str) -> str:
        value = self.get(name)
        if value is None or not value:
            raise KeyError(f"Credential obligatoire absent: {name}")
        return value

    def redact(self, text: str) -> str:
        redacted = text
        for name, value in self._values.items():
            if (not self._redact_all and not _is_sensitive_name(name)) or len(value) < 4:
                continue
            redacted = redacted.replace(value, "[redacted]")
        return redacted

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<redacted>, count={len(self._values)})"


class ChainedCredentials:
    """Cherche d'abord dans la session, puis dans le runtime."""

    __slots__ = ("_primary", "_fallback")

    def __init__(
        self,
        primary: CredentialProvider,
        fallback: CredentialProvider,
    ) -> None:
        self._primary = primary
        self._fallback = fallback

    def get(self, name: str, default: str | None = None) -> str | None:
        value = self._primary.get(name)
        if value is not None:
            return value
        return self._fallback.get(name, default)

    def redact(self, text: str) -> str:
        return redact_text(
            self._fallback,
            redact_text(self._primary, text),
        )

    def __repr__(self) -> str:
        return f"{type(self).__name__}(<redacted>)"


def as_credentials(
    value: CredentialProvider | Mapping[str, str] | None,
) -> CredentialProvider:
    """Normalise une mapping simple ou conserve un provider injecté."""
    if value is None:
        return MappingCredentials()
    if isinstance(value, Mapping):
        return MappingCredentials(value)
    return value


def redact_text(provider: CredentialProvider, text: str) -> str:
    """Applique la redaction quand le provider injecté la supporte."""
    redactor = getattr(provider, "redact", None)
    return redactor(text) if callable(redactor) else text


def _is_sensitive_name(name: str) -> bool:
    upper_name = name.upper()
    return any(part in upper_name for part in SENSITIVE_NAME_PARTS)
