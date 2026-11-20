"""Shared types for per-organization concurrency limits.

Safe to import from Temporal workflow code: no Redis, database or network
imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final, Literal

from pydantic import BaseModel, Field, TypeAdapter, ValidationError, model_validator
from temporalio.exceptions import ApplicationError

CONCURRENCY_BUSY_TYPE: Final = "tracecat.concurrency.busy"
"""Discriminator of the result an executor returns when an org is at its limit."""


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


@dataclass(frozen=True, slots=True)
class Unenforced:
    """The lease store or the limit lookup could not answer; the work runs uncounted."""

    error: BaseException


type Admission = Admitted | Busy | Unenforced


class ConcurrencyBusyResult(BaseModel):
    """Activity result for an attempt that was not admitted.

    The attempt executed nothing. The workflow waits and dispatches again.
    """

    type: Literal["tracecat.concurrency.busy"] = Field(default=CONCURRENCY_BUSY_TYPE)
    resource: ConcurrencyResource
    in_use: int
    limit: int
    attempt: int = Field(
        description="Temporal attempt number of the activity that was not admitted."
    )

    @model_validator(mode="before")
    @classmethod
    def _always_tag(cls, data: Any) -> Any:
        """Mark ``type`` as set, so ``exclude_unset`` serialization keeps the tag."""
        if isinstance(data, dict) and "type" not in data:
            return {**data, "type": CONCURRENCY_BUSY_TYPE}
        return data


_BusyResultValidator: TypeAdapter[ConcurrencyBusyResult] = TypeAdapter(
    ConcurrencyBusyResult
)


def parse_busy_result(result: Any) -> ConcurrencyBusyResult | None:
    """Return the busy result if an activity result is one, else ``None``.

    Activities dispatched by name return decoded JSON, so the result is a plain
    mapping here, not a model.
    """
    match result:
        case ConcurrencyBusyResult():
            return result
        case {"type": "tracecat.concurrency.busy"}:
            try:
                return _BusyResultValidator.validate_python(result)
            except ValidationError:
                # Malformed: never hide history or skip work on a partial match.
                return None
        case _:
            return None


def ensure_not_busy[T](result: T | ConcurrencyBusyResult, *, caller: str) -> T:
    """Narrow an activity result for a caller that is never gated.

    Raises:
        ApplicationError: Non-retryable, if the result is a busy answer; that
            means a gate was added without a caller that can wait for it.
    """
    if (
        isinstance(result, ConcurrencyBusyResult)
        or parse_busy_result(result) is not None
    ):
        raise ApplicationError(
            f"{caller} received a concurrency busy result, but it is never gated",
            non_retryable=True,
            type=CONCURRENCY_BUSY_TYPE,
        )
    return result
