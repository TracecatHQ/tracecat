"""Workflow storage abstractions for Tracecat."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol

from pydantic import BaseModel


class Source[IdT](BaseModel):
    """Represents a source file in an external repository."""

    id: IdT
    path: str
    sha: str
    version: int | None = None


class ExternalStore[SourceT: Source](Protocol):
    """Protocol for external storage systems."""

    async def list_sources(self) -> Iterable[SourceT]:
        """List all sources available in the store.

        Returns:
            Iterable of Source objects representing available sources.
        """
        ...

    async def fetch_content(self, source: SourceT) -> str:
        """Fetch the content for a specific source.

        Args:
            path: Path to the source file in the repository.
            sha: Git SHA of the specific version to fetch.

        Returns:
            File content as a string.

        Raises:
            Exception: If the file cannot be fetched or does not exist.
        """
        ...
