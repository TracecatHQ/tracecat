"""Discover installed session backends without replacing Tracecat modules."""

import re
from collections.abc import Mapping
from functools import lru_cache
from importlib.metadata import entry_points
from types import MappingProxyType

from tracecat.agent.session.backends.durable import DurableSessionBackend
from tracecat.agent.session.backends.types import SessionBackend, SessionHistoryAdapter
from tracecat.exceptions import TracecatValidationError

SESSION_BACKEND_ENTRY_POINT_GROUP = "tracecat.session_backends"
DEFAULT_SESSION_BACKEND = "v1"


@lru_cache(maxsize=1)
def get_session_backends() -> Mapping[str, SessionBackend]:
    """Load trusted installed factories once; reject conflicting registrations."""
    backends: dict[str, SessionBackend] = {
        DEFAULT_SESSION_BACKEND: DurableSessionBackend()
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
        if (
            not backend.supported_harnesses
            or backend.default_harness not in backend.supported_harnesses
        ):
            raise ValueError(
                f"Invalid default harness for session backend: {entry.name}"
            )
        if backend.history is not None and not isinstance(
            backend.history, SessionHistoryAdapter
        ):
            raise TypeError(f"Invalid session history adapter: {entry.name}")
        backends[entry.name] = backend
    return MappingProxyType(backends)


def find_session_backend(identifier: str | None) -> SessionBackend | None:
    """Find an installed provider for reads, including disabled providers."""
    key = DEFAULT_SESSION_BACKEND if identifier is None else identifier
    return get_session_backends().get(key)


def get_session_backend(
    identifier: str | None, *, harness_type: str | None = None
) -> SessionBackend:
    """Resolve an executable backend and validate its selected harness."""
    backend = find_session_backend(identifier)
    if backend is None or not backend.is_enabled():
        raise TracecatValidationError("Session backend is unavailable")
    if harness_type is not None and harness_type not in backend.supported_harnesses:
        raise TracecatValidationError("Session harness is unsupported by its backend")
    return backend


def session_backend_available(identifier: str | None, harness_type: str | None) -> bool:
    """Whether the installed provider can execute this session."""
    backend = find_session_backend(identifier)
    return (
        backend is not None
        and backend.is_enabled()
        and (harness_type is None or harness_type in backend.supported_harnesses)
    )
