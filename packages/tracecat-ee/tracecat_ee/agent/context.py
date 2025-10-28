from __future__ import annotations

import uuid
from contextvars import ContextVar
from dataclasses import dataclass
from typing import ClassVar, Self


@dataclass
class AgentContext:
    """Context state for agent execution, including optional Redis streaming configuration."""

    __var__: ClassVar[ContextVar[Self]] = ContextVar("agent")

    session_id: uuid.UUID

    @classmethod
    def get(cls) -> Self | None:
        """Get the current agent state from context."""
        return cls.__var__.get(None)

    @classmethod
    def set(
        cls,
        session_id: uuid.UUID,
    ) -> None:
        """Set the agent state in context."""
        cls.__var__.set(cls(session_id=session_id))

    @classmethod
    def set_from(cls, state: Self) -> None:
        """Set the agent state in context."""
        cls.__var__.set(state)
