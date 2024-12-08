from __future__ import annotations

import abc
from typing import Self

from pydantic import BaseModel

from tracecat.identifiers import WorkflowExecutionID, WorkflowID
from tracecat.identifiers.action import ActionRef
from tracecat.identifiers.workflow import (
    WorkflowExecutionSuffixID,
    exec_id_from_parts,
    exec_id_to_parts,
)


class StoreObjectHandle(abc.ABC, BaseModel):
    """Represents a structured storage path"""

    @abc.abstractmethod
    def to_path(self, ext: str = "json") -> str:
        raise NotImplementedError

    @classmethod
    @abc.abstractmethod
    def from_path(cls, path: str) -> Self:
        raise NotImplementedError


class ActionRefHandle(StoreObjectHandle):
    """Represents a structured storage path for an action result."""

    wf_exec_id: WorkflowExecutionID
    ref: ActionRef

    @property
    def _wf_exec_parts(self) -> tuple[WorkflowID, WorkflowExecutionSuffixID]:
        return exec_id_to_parts(self.wf_exec_id)

    def to_path(self, ext: str = "json") -> str:
        """Convert to storage path string"""
        wf_id, exec_suffix_id = self._wf_exec_parts
        return f"{wf_id}/{exec_suffix_id}/{self.ref}.{ext}"

    @classmethod
    def from_path(cls, path: str) -> ActionRefHandle:
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
