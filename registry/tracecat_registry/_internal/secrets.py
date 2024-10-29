from tracecat.secrets import secrets_manager


def get(name: str, default: str | None = None, /) -> str:
    """Get a secret that was set in the current context."""
    return secrets_manager.get(name, default)


def set(name: str, value: str, /) -> None:
    """Set a secret in the current context."""
    return secrets_manager.set(name, value)
