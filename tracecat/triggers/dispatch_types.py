"""Domain types of the trigger event dispatcher (``tracecat.triggers.dispatch``)."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Literal

from tracecat.dsl.common import DSLInput
from tracecat.registry.lock.types import RegistryLock

type StartOutcome = Literal["started", "already_started", "dropped"]


@dataclass(frozen=True, slots=True)
class RowOutcome:
    """How one row's starts went: all landed, some failed, or none began."""

    error: str | None = None
    deferred: bool = False


@dataclass(frozen=True, slots=True)
class ClaimedRow:
    """An outbox row this batch holds the lease on."""

    id: int
    workspace_id: uuid.UUID
    source_id: uuid.UUID
    occurrence_key: str
    # The normalized event exactly as ingest stored it (JSONB). It is handed to
    # workflows as their trigger input unchanged, so it stays a plain mapping.
    payload: dict[str, Any]
    attempts: int
    lease_until: datetime
    """The ``next_attempt_at`` this claim set. It fences the finish: no later
    claim, release or retry can set the same value again, unlike ``attempts``,
    which a release gives back."""


@dataclass(frozen=True, slots=True)
class WorkflowTarget:
    binding_id: uuid.UUID
    workflow_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class AgentTarget:
    binding_id: uuid.UUID
    agent_preset_id: uuid.UUID
    instruction: str
    trigger_name: str


type Target = WorkflowTarget | AgentTarget


@dataclass(frozen=True, slots=True)
class PublishedWorkflow:
    dsl: DSLInput
    registry_lock: RegistryLock | None


@dataclass(frozen=True, slots=True)
class Backlog:
    """Alert inputs: the oldest ready row's wait and the stopped rows."""

    has_ready_rows: bool
    oldest_ready_age_seconds: float
    stopped_rows: int | None
    """None when this read did not count them."""


@dataclass(frozen=True, slots=True)
class BatchResult:
    picked: int
    claimed: int
    delivered: int
    retrying: int
    stopped: int
    deferred: int = 0
