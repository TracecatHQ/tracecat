"""Agent preset management package."""


def __getattr__(name: str) -> object:
    if name == "AgentPresetService":
        from .service import AgentPresetService

        return AgentPresetService
    raise AttributeError(name)
