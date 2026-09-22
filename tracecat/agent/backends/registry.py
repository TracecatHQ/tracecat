"""Discover installed agent backends without replacing Tracecat modules."""

import re
from collections.abc import Mapping
from functools import lru_cache
from importlib.metadata import entry_points
from types import MappingProxyType

from tracecat.agent.backends.durable import DurableAgentBackend
from tracecat.agent.backends.types import AgentBackend, SessionHistoryAdapter
from tracecat.exceptions import TracecatValidationError

AGENT_BACKEND_ENTRY_POINT_GROUP = "tracecat.agent_backends"
DEFAULT_AGENT_BACKEND = "oss"


@lru_cache(maxsize=1)
def get_agent_backends() -> Mapping[str, AgentBackend]:
    """Load trusted installed factories once; reject conflicting registrations."""
    backends: dict[str, AgentBackend] = {DEFAULT_AGENT_BACKEND: DurableAgentBackend()}
    for entry in sorted(
        entry_points(group=AGENT_BACKEND_ENTRY_POINT_GROUP), key=lambda ep: ep.name
    ):
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", entry.name):
            raise ValueError(f"Invalid agent backend identifier: {entry.name}")
        if entry.name in backends:
            raise ValueError(f"Duplicate agent backend: {entry.name}")
        factory = entry.load()
        if not callable(factory):
            raise TypeError(f"Agent backend {entry.name} must expose a factory")
        backend = factory()
        if not isinstance(backend, AgentBackend):
            raise TypeError(f"Invalid agent backend contract: {entry.name}")
        if (
            not backend.supported_harnesses
            or backend.default_harness not in backend.supported_harnesses
        ):
            raise ValueError(f"Invalid default harness for agent backend: {entry.name}")
        if backend.history is not None and not isinstance(
            backend.history, SessionHistoryAdapter
        ):
            raise TypeError(f"Invalid session history adapter: {entry.name}")
        backends[entry.name] = backend
    return MappingProxyType(backends)


def find_agent_backend(identifier: str | None) -> AgentBackend | None:
    """Find an installed provider for reads, including disabled providers.

    A persisted session can outlive its installed plugin. Return None in that
    case so reads can report unavailability without routing to another backend.
    Execution must use get_agent_backend(), which raises if unavailable.
    """
    key = DEFAULT_AGENT_BACKEND if identifier is None else identifier
    return get_agent_backends().get(key)


def get_agent_backend(
    identifier: str | None, *, harness_type: str | None = None
) -> AgentBackend:
    """Resolve an executable backend and validate its selected harness."""
    backend = find_agent_backend(identifier)
    if backend is None or not backend.is_enabled():
        raise TracecatValidationError("Agent backend is unavailable")
    if harness_type is not None and harness_type not in backend.supported_harnesses:
        raise TracecatValidationError("Session harness is unsupported by its backend")
    return backend


def agent_backend_available(identifier: str | None, harness_type: str | None) -> bool:
    """Whether the installed provider can execute this session."""
    backend = find_agent_backend(identifier)
    return (
        backend is not None
        and backend.is_enabled()
        and (harness_type is None or harness_type in backend.supported_harnesses)
    )
