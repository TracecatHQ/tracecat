"""Shared types for per-organization concurrency limits.

Safe to import from Temporal workflow code: no Redis, database or network
imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError, model_validator

NOT_ADMITTED_TYPE: Final = "tracecat.admission.not_admitted"
"""Discriminator of the result an executor returns when an org is at its limit."""

ADMISSION_ACK_SIGNAL: Final = "admission_ack"
"""Signal an executor sends a run when it admits a probe; carries the activity ID."""


class ConcurrencyResource(StrEnum):
    """A resource with a per-organization concurrency limit."""

    ACTIONS = "actions"
    TOOL_CALLS = "tool_calls"
    AGENT_TURNS = "agent_turns"
    WORKFLOWS = "workflows"


@dataclass(frozen=True, slots=True)
class Admitted:
    """The lease was taken."""

    in_use: int
    limit: int


@dataclass(frozen=True, slots=True)
class Busy:
    """The organization is at its limit; nothing was taken."""

    in_use: int
    limit: int


class NotAdmittedReason(StrEnum):
    """Why an attempt was not admitted."""

    AT_LIMIT = "at_limit"
    """The organization is at its limit."""
    EXECUTOR_OVERLOADED = "executor_overloaded"
    """The executor was too loaded to reach Redis in time; counts are unknown."""
    LIMIT_UNAVAILABLE = "limit_unavailable"
    """The organization's limit couldn't be read; counts are unknown."""


UNKNOWN_COUNT: Final = -1
"""``in_use`` and ``limit`` of an attempt deferred without an answer from Redis."""


@dataclass(frozen=True, slots=True)
class Unenforced:
    """Redis could not answer; the work runs uncounted."""

    error: BaseException


@dataclass(frozen=True, slots=True)
class Deferred:
    """No answer, but not a Redis outage: the work waits instead of running uncounted."""

    reason: NotAdmittedReason
    error: BaseException


type Admission = Admitted | Busy | Unenforced | Deferred


class NotAdmitted(BaseModel):
    """Activity result for an attempt that was not admitted.

    The attempt executed nothing. The workflow waits and dispatches again.
    """

    type: Literal["tracecat.admission.not_admitted"] = Field(default=NOT_ADMITTED_TYPE)
    resource: ConcurrencyResource
    in_use: int
    limit: int
    attempt: int = Field(
        description="Temporal attempt number of the activity that was not admitted."
    )
    reason: str = Field(
        default=NotAdmittedReason.AT_LIMIT,
        description=(
            "A ``NotAdmittedReason`` value. A plain string, so a reader that "
            "predates a new reason still parses the result as not admitted."
        ),
    )

    @model_validator(mode="before")
    @classmethod
    def _always_tag(cls, data: Any) -> Any:
        """Mark ``type`` as set, so ``exclude_unset`` serialization keeps the tag."""
        if isinstance(data, dict) and "type" not in data:
            return {**data, "type": NOT_ADMITTED_TYPE}
        return data


_NotAdmittedValidator: TypeAdapter[NotAdmitted] = TypeAdapter(NotAdmitted)


def parse_not_admitted(result: Any) -> NotAdmitted | None:
    """Return the ``NotAdmitted`` result if an activity result is one, else ``None``.

    Activities dispatched by name return decoded JSON, so the result is a plain
    mapping here, not a model.
    """
    match result:
        case NotAdmitted():
            return result
        case {"type": "tracecat.admission.not_admitted"}:
            try:
                return _NotAdmittedValidator.validate_python(result)
            except ValidationError:
                # Malformed: never hide history or skip work on a partial match.
                return None
        case _:
            return None
