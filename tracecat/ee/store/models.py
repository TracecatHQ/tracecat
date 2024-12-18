from __future__ import annotations

import abc
from typing import ClassVar, Self, TypedDict

from pydantic import BaseModel

from tracecat.identifiers import WorkflowExecutionID, WorkflowID
from tracecat.identifiers.action import ActionRef
from tracecat.identifiers.workflow import (
    WorkflowExecutionSuffixID,
    exec_id_from_parts,
    exec_id_to_parts,
)

type StoreObjectPath = str
"""Type alias for a structured storage path"""


class StoreObjectPtr(TypedDict):
    """A pointer to an object in object storage"""

    key: str


class StoreObjectHandle(abc.ABC, BaseModel):
    """Represents a structured storage path"""

    @abc.abstractmethod
    def to_path(self, ext: str = "json") -> StoreObjectPath:
        """Convert to relative object storage path string."""
        raise NotImplementedError

    @classmethod
    @abc.abstractmethod
    def from_path(cls, path: StoreObjectPath) -> Self:
        """Create a handle from a relative path string."""
        raise NotImplementedError

    def to_pointer(self) -> StoreObjectPtr:
        return StoreObjectPtr(key=self.to_path())


class ExecutionResultHandle(StoreObjectHandle):
    wf_exec_id: WorkflowExecutionID

    @property
    def workflow_exec_parts(self) -> tuple[WorkflowID, WorkflowExecutionSuffixID]:
        return exec_id_to_parts(self.wf_exec_id)


class WorkflowResultHandle(ExecutionResultHandle):
    """Represents a structured storage path for a workflow result."""

    file_name: ClassVar[str] = "_result"
    file_ext: ClassVar[str] = "json"

    def to_path(self) -> StoreObjectPath:
        wf_id, exec_suffix_id = self.workflow_exec_parts
        return f"{wf_id}/{exec_suffix_id}/{self.file_name}.{self.file_ext}"

    @classmethod
    def from_path(cls, path: StoreObjectPath) -> Self:
        segments = path.split("/")
        if len(segments) != 3:
            raise ValueError(f"Invalid path format: {path}")
        wf_id, exec_suffix_id, suffix = segments
        if suffix != f"{cls.file_name}.{cls.file_ext}":
            raise ValueError(f"Invalid path format: {path}")
        return cls(wf_exec_id=exec_id_from_parts(wf_id, exec_suffix_id))


class ActionResultHandle(ExecutionResultHandle):
    """Represents a structured storage path for an action result."""

    ref: ActionRef
    """The underlying action reference that this handle represents."""

    def to_path(self, ext: str = "json") -> StoreObjectPath:
        """Convert to storage path string"""
        wf_id, exec_suffix_id = self.workflow_exec_parts
        return f"{wf_id}/{exec_suffix_id}/{self.ref}.{ext}"

    @classmethod
    def from_path(cls, path: StoreObjectPath) -> Self:
        """Create an ActionRefHandle from a path string

        Args:
            path: Path string in format "workflow_id/execution_id/object_name.ext"

        Returns:
            ActionRefHandle instance

        Raises:
            ValueError: If path format is invalid
        """
        try:
            wf_id, exec_suffix_id, ref = path.split("/")
            ref = ref.rsplit(".", 1)[0]  # Remove extension
            return cls(wf_exec_id=exec_id_from_parts(wf_id, exec_suffix_id), ref=ref)
        except ValueError as e:
            raise ValueError(f"Invalid path format: {path}") from e
