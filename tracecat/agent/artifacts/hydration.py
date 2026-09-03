"""Artifact hydration contracts for agent working sets."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Protocol

from tracecat.artifacts.schemas import Artifact, ArtifactType
from tracecat.auth.types import Role

type ArtifactJsonPayload = dict[str, Any] | list[Any]


@dataclass(frozen=True, slots=True)
class ArtifactHydrationContext:
    """Context available to artifact hydrators."""

    session_id: uuid.UUID
    workspace_id: uuid.UUID
    role: Role


@dataclass(frozen=True, slots=True)
class MountedArtifactContent:
    """Hydrated artifact content to write into the agent working set."""

    filename: str
    content_type: str
    payload: ArtifactJsonPayload


class ArtifactHydrator(Protocol):
    """Hydrates an artifact projection into domain-specific mounted content."""

    async def hydrate(
        self,
        artifact: Artifact,
        ctx: ArtifactHydrationContext,
    ) -> MountedArtifactContent | None:
        """Return mounted content for an artifact, if available."""
        ...


class ArtifactHydratorRegistry:
    """Dispatch artifact hydration by artifact type."""

    def __init__(
        self,
        hydrators: dict[ArtifactType, ArtifactHydrator] | None = None,
    ) -> None:
        self._hydrators = dict(hydrators) if hydrators is not None else {}

    def register(self, artifact_type: ArtifactType, hydrator: ArtifactHydrator) -> None:
        """Register a hydrator for an artifact type."""
        self._hydrators[artifact_type] = hydrator

    async def hydrate(
        self,
        artifact: Artifact,
        ctx: ArtifactHydrationContext,
    ) -> MountedArtifactContent | None:
        """Hydrate an artifact using its registered hydrator."""
        hydrator = self._hydrators.get(artifact.type)
        if hydrator is None:
            return None
        return await hydrator.hydrate(artifact, ctx)
