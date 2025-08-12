"""Workflow storage abstractions for Tracecat."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from pydantic import BaseModel


class WorkflowSource(BaseModel):
    """Represents a workflow source file in a repository."""

    path: str
    sha: str
    workflow_id: str
    version: int | None = None


class ExternalWorkflowStore(Protocol):
    """Protocol for external workflow storage systems."""

    async def list_sources(self) -> Iterable[WorkflowSource]:
        """List all workflow sources available in the store.

        Returns:
            Iterable of WorkflowSource objects representing available workflows.
        """
        ...

    async def fetch_yaml(self, path: str, sha: str) -> str:
        """Fetch the YAML content for a specific workflow.

        Args:
            path: Path to the workflow file in the repository.
            sha: Git SHA of the specific version to fetch.

        Returns:
            YAML content as a string.

        Raises:
            Exception: If the file cannot be fetched or does not exist.
        """
        ...
