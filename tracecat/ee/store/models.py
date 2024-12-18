from __future__ import annotations

import abc
import re
from typing import Annotated, ClassVar, Literal, Self

from pydantic import BaseModel, Field, StringConstraints, TypeAdapter

from tracecat.executor.enums import ResultsBackend
from tracecat.executor.models import ExecutorResult, TaskResult
from tracecat.identifiers import WorkflowExecutionID, WorkflowID
from tracecat.identifiers.action import ActionRef
from tracecat.identifiers.workflow import (
    WorkflowExecutionSuffixID,
    exec_id_from_parts,
    exec_id_to_parts,
)

StoreObjectKey = str
"""Type alias for an S3 comatible object storage key"""


EXECUTION_RESULT_KEY_REGEX = (
    r"^workflows/(?P<workflow_id>[^/]+)"
    r"/executions/(?P<execution_id>[^/]+)"
    r"/(?P<file_name>[^.]+)\.(?P<file_ext>[^/]+)$"
)

ExecutionResultKey = Annotated[
    str, StringConstraints(pattern=EXECUTION_RESULT_KEY_REGEX)
]
"""A key for an execution result."""
EXECTION_RESULT_KEY_PATTERN = re.compile(EXECUTION_RESULT_KEY_REGEX)
EXECUTION_RESULT_KEY_TEMPLATE = (
    "workflows/{workflow_id}/executions/{execution_id}/{file_name}.{file_ext}"
)


class StoreContextContainer(BaseModel):
    """Utility model for parsing action context with store backend."""

    context: dict[ActionRef, StoreResult]


class StoreResult(ExecutorResult):
    """A pointer to an object in object storage"""

    tc_backend_: Literal[ResultsBackend.STORE] = Field(
        default=ResultsBackend.STORE, frozen=True
    )
    key: StoreObjectKey


ResultVariant = Annotated[TaskResult | StoreResult, Field(discriminator="tc_backend_")]
ResultVariantValidator: TypeAdapter[ResultVariant] = TypeAdapter(ResultVariant)  # type: ignore


class StoreObjectHandle(abc.ABC, BaseModel):
    """Represents a structured storage path"""

    @abc.abstractmethod
    def to_key(self, ext: str = "json") -> StoreObjectKey:
        """Convert to relative object storage path string."""
        raise NotImplementedError

    @classmethod
    @abc.abstractmethod
    def from_key(cls, key: StoreObjectKey) -> Self:
        """Create a handle from a relative path string."""
        raise NotImplementedError

    def to_pointer(self) -> StoreResult:
        # NOTE: Explicitly set to avoid exclusiondue to value unset in dsl/_converter
        return StoreResult(tc_backend_=ResultsBackend.STORE, key=self.to_key())


class TaskResultHandle(StoreObjectHandle):
    wf_exec_id: WorkflowExecutionID
    """The workflow execution ID of the workflow that produced the task result."""

    @property
    def workflow_exec_parts(self) -> tuple[WorkflowID, WorkflowExecutionSuffixID]:
        """The workflow ID and execution suffix ID of the workflow that produced the task result."""
        return exec_id_to_parts(self.wf_exec_id)

    @staticmethod
    def from_key(key: StoreObjectKey) -> TaskResultHandle:
        if key.endswith(
            f"{WorkflowResultHandle.file_name}.{WorkflowResultHandle.file_ext}"
        ):
            handle = WorkflowResultHandle.from_key(key)
        else:
            handle = ActionResultHandle.from_key(key)
        return handle


class WorkflowResultHandle(TaskResultHandle):
    """Represents a structured storage path for a workflow result."""

    file_name: ClassVar[str] = "_result"
    file_ext: ClassVar[str] = "json"

    def to_key(self) -> StoreObjectKey:
        wf_id, exec_suffix_id = self.workflow_exec_parts
        return EXECUTION_RESULT_KEY_TEMPLATE.format(
            workflow_id=wf_id,
            execution_id=exec_suffix_id,
            file_name=self.file_name,
            file_ext=self.file_ext,
        )

    @classmethod
    def from_key(cls, key: StoreObjectKey) -> Self:
        match = EXECTION_RESULT_KEY_PATTERN.match(key)
        if not match:
            raise ValueError(f"Invalid path format: {key}")
        return cls(
            wf_exec_id=exec_id_from_parts(
                match.group("workflow_id"), match.group("execution_id")
            )
        )


class ActionResultHandle(TaskResultHandle):
    """Represents a structured storage path for an action result."""

    ref: ActionRef
    """The underlying action reference that this handle represents."""

    def to_key(self, ext: str = "json") -> StoreObjectKey:
        """Convert to storage key string"""
        wf_id, exec_suffix_id = self.workflow_exec_parts
        return EXECUTION_RESULT_KEY_TEMPLATE.format(
            workflow_id=wf_id,
            execution_id=exec_suffix_id,
            file_name=self.ref,
            file_ext=ext,
        )

    @classmethod
    def from_key(cls, key: StoreObjectKey) -> Self:
        """Create an ActionRefHandle from a key string

        Args:
            key: Key string in format "workflow_id/execution_id/object_name.ext"

        Returns:
            ActionRefHandle instance

        Raises:
            ValueError: If path format is invalid
        """
        match = EXECTION_RESULT_KEY_PATTERN.match(key)
        if not match:
            raise ValueError(f"Invalid path format: {key}")
        return cls(
            wf_exec_id=exec_id_from_parts(
                match.group("workflow_id"), match.group("execution_id")
            ),
            ref=match.group("file_name"),
        )


if __name__ == "__main__":
    wf_id = "wf-" + "0" * 32
    exec_id = "exec-" + "0" * 32
    key = f"workflows/{wf_id}/executions/{exec_id}/test.json"
    x = ActionResultHandle.from_key(key)
    print(x.model_dump_json())
    print(x.to_pointer().model_dump_json())

    y = StoreResult(key=key)
    print(y.model_dump_json())
