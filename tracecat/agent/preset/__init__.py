"""Agent preset management package."""

from .schemas import AgentPresetCreate, AgentPresetRead, AgentPresetUpdate

__all__ = [
    "AgentPresetCreate",
    "AgentPresetRead",
    "AgentPresetUpdate",
    "AgentPresetService",
]


def __getattr__(name: str) -> object:
    if name == "AgentPresetService":
        from .service import AgentPresetService

        return AgentPresetService
    raise AttributeError(name)
