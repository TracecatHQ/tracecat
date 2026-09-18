"""Discover installed session backends without replacing Tracecat modules."""

import re
from collections.abc import Mapping
from functools import lru_cache
from importlib.metadata import entry_points
from types import MappingProxyType

from tracecat.agent.session.backends.claude import ClaudeSessionBackend
from tracecat.agent.session.backends.types import SessionBackend, SessionHistoryAdapter
from tracecat.exceptions import TracecatValidationError

SESSION_BACKEND_ENTRY_POINT_GROUP = "tracecat.session_backends"
DEFAULT_SESSION_BACKEND = "claude_code"


@lru_cache(maxsize=1)
def get_session_backends() -> Mapping[str, SessionBackend]:
    """Load trusted installed factories once; reject conflicting registrations."""
    backends: dict[str, SessionBackend] = {
        DEFAULT_SESSION_BACKEND: ClaudeSessionBackend()
    }
    for entry in sorted(
        entry_points(group=SESSION_BACKEND_ENTRY_POINT_GROUP), key=lambda ep: ep.name
    ):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", entry.name):
            raise ValueError(f"Invalid session backend identifier: {entry.name}")
        if entry.name in backends:
            raise ValueError(f"Duplicate session backend: {entry.name}")
        factory = entry.load()
        if not callable(factory):
            raise TypeError(f"Session backend {entry.name} must expose a factory")
        backend = factory()
        if not isinstance(backend, SessionBackend):
            raise TypeError(f"Invalid session backend contract: {entry.name}")
        if backend.history is not None and not isinstance(
            backend.history, SessionHistoryAdapter
        ):
            raise TypeError(f"Invalid session history adapter: {entry.name}")
        backends[entry.name] = backend
    return MappingProxyType(backends)


def get_session_backend(identifier: str | None) -> SessionBackend:
    """Resolve persisted identity; never silently fall back for unknown backends."""
    key = DEFAULT_SESSION_BACKEND if identifier is None else identifier
    backend = get_session_backends().get(key)
    if backend is None or not backend.is_enabled():
        raise TracecatValidationError(f"Session backend is unavailable: {key}")
    return backend
