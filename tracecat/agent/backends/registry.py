"""Discover installed agent backends without replacing Tracecat modules."""

import re
from collections.abc import Mapping
from functools import lru_cache
from importlib.metadata import EntryPoint, entry_points
from types import MappingProxyType
from typing import Any

from tracecat.agent.backends.base import AgentBackend
from tracecat.agent.backends.types import SessionHistoryAdapter
from tracecat.exceptions import TracecatValidationError

AGENT_BACKEND_ENTRY_POINT_GROUP = "tracecat.agent_backends"
DEFAULT_AGENT_BACKEND = "oss"


def _validate_backend(identifier: str, backend: AgentBackend[Any, Any]) -> None:
    """Reject incomplete installed providers before exposing them to requests."""
    # Plugin annotations do not enforce attribute presence at runtime. Dynamic
    # inspection here gives missing and malformed fields the same clear error.
    for field in ("name", "task_queue"):
        value: object = getattr(backend, field, None)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(
                f"Agent backend {identifier}: {field} must be a nonempty string"
            )
    default_harness: object = getattr(backend, "default_harness", None)
    if not isinstance(default_harness, str):
        raise TypeError(f"Agent backend {identifier}: default_harness must be a string")
    supported_harnesses: object = getattr(backend, "supported_harnesses", None)
    if not isinstance(supported_harnesses, frozenset):
        raise TypeError(
            f"Agent backend {identifier}: supported_harnesses must be a frozenset"
        )
    if not supported_harnesses or default_harness not in supported_harnesses:
        raise ValueError(f"Invalid default harness for agent backend: {identifier}")
    for harness in supported_harnesses:
        if not isinstance(harness, str):
            raise TypeError(
                f"Agent backend {identifier}: supported_harnesses must contain only strings"
            )
        if not re.fullmatch(r"[a-z][a-z0-9_]{0,49}", harness):
            raise ValueError(
                f"Invalid harness identifier for agent backend: {identifier}"
            )

    # Inspect the workflow dynamically for the same missing-attribute boundary.
    workflow_class: object = getattr(backend, "workflow", None)
    if not isinstance(workflow_class, type):
        raise TypeError(f"Agent backend {identifier}: workflow must be a class")
    for method in ("run", "set_approvals", "request_cancel"):
        if not callable(getattr(workflow_class, method, None)):
            raise TypeError(
                f"Agent backend {identifier}: workflow.{method} must be callable"
            )
    if backend.history is not None and not isinstance(
        backend.history, SessionHistoryAdapter
    ):
        raise TypeError(f"Invalid session history adapter: {identifier}")


@lru_cache(maxsize=1)
def get_agent_backends() -> Mapping[str, AgentBackend[Any, Any]]:
    """Load trusted installed factories once; reject conflicting registrations."""
    backends: dict[str, AgentBackend[Any, Any]] = {}
    # Load the built-in factory lazily too: its workflow imports session services.
    builtin = EntryPoint(
        name=DEFAULT_AGENT_BACKEND,
        value="tracecat.agent.backends.default:DefaultBackend",
        group=AGENT_BACKEND_ENTRY_POINT_GROUP,
    )
    installed = sorted(
        entry_points(group=AGENT_BACKEND_ENTRY_POINT_GROUP), key=lambda ep: ep.name
    )
    for entry in (builtin, *installed):
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
        _validate_backend(entry.name, backend)
        backends[entry.name] = backend
    return MappingProxyType(backends)


def find_agent_backend(identifier: str | None) -> AgentBackend[Any, Any] | None:
    """Find an installed provider for reads, including disabled providers.

    A persisted session can outlive its installed plugin. Return None in that
    case so reads can report unavailability without routing to another backend.
    Execution must use get_agent_backend(), which raises if unavailable.
    """
    key = DEFAULT_AGENT_BACKEND if identifier is None else identifier
    return get_agent_backends().get(key)


def get_agent_backend(
    identifier: str | None, *, harness_type: str | None = None
) -> AgentBackend[Any, Any]:
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
