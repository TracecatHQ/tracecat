from contextvars import ContextVar
from dataclasses import dataclass
from typing import ClassVar, Self


@dataclass
class AgentContext:
    """Context state for agent execution, including optional Redis streaming configuration."""

    __var__: ClassVar[ContextVar[Self]] = ContextVar("agent")

    stream_key: str | None = None

    @classmethod
    def get(cls) -> Self | None:
        """Get the current agent state from context."""
        return cls.__var__.get(None)

    @classmethod
    def set(cls, stream_key: str | None = None) -> None:
        """Set the agent state in context."""
        cls.__var__.set(cls(stream_key=stream_key))

    @classmethod
    def set_from(cls, state: Self) -> None:
        """Set the agent state in context."""
        cls.__var__.set(state)
