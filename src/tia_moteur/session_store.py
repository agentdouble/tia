"""Persistance injectable de l'historique des sessions TIA."""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Protocol

from pydantic_ai import ModelMessage


class SessionStore(Protocol):
    """Stockage asynchrone minimal, remplaçable par SQLite ou PostgreSQL."""

    async def load(self, session_id: str) -> Sequence[ModelMessage] | None: ...

    async def save(
        self,
        session_id: str,
        messages: Sequence[ModelMessage],
    ) -> None: ...


class MemorySessionStore:
    """Stockage local concurrent, utile par défaut et dans les tests."""

    def __init__(self) -> None:
        self._messages: dict[str, tuple[ModelMessage, ...]] = {}
        self._lock = asyncio.Lock()

    async def load(self, session_id: str) -> tuple[ModelMessage, ...] | None:
        async with self._lock:
            messages = self._messages.get(session_id)
            return tuple(messages) if messages is not None else None

    async def save(
        self,
        session_id: str,
        messages: Sequence[ModelMessage],
    ) -> None:
        async with self._lock:
            self._messages[session_id] = tuple(messages)
