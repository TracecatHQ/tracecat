"""Agent preset management package."""


def __getattr__(name: str) -> object:
    """Lazily resolve ``AgentPresetService`` to avoid eager import cycles."""
    if name == "AgentPresetService":
        from .service import AgentPresetService

        return AgentPresetService
    raise AttributeError(name)
