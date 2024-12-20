from typing import Any, Literal

from pydantic import UUID4, BaseModel, Field

from tracecat.executor.enums import ResultsBackend


class ExecutorSyncInput(BaseModel):
    repository_id: UUID4


class ExecutorResult(BaseModel):
    """Base class for all executor results."""

    tc_backend_: ResultsBackend
    """Reserved field for internal use by the executor."""


class TaskResult(ExecutorResult):
    """Result of executing a workflow task."""

    tc_backend_: Literal[ResultsBackend.MEMORY] = Field(
        default=ResultsBackend.MEMORY, frozen=True
    )
    """Reserved field for internal use by the executor.
    NOTE: Please set this explicitly to include in serialization.
    """

    result: Any | None = None
    result_typename: str | None = None
    error: Any | None = None
    error_typename: str | None = None
