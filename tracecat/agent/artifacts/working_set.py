"""Runtime-facing artifact working-set contracts."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from tracecat.agent.common.types import MCPToolDefinition
from tracecat.artifacts.schemas import Artifact, ArtifactType
from tracecat.auth.types import Role


class ArtifactWorkingSetSchema(BaseModel):
    """Base schema for artifact working-set files."""

    model_config = ConfigDict(extra="forbid")


class ArtifactWorkingSetEntry(ArtifactWorkingSetSchema):
    """One artifact mounted into the agent working set."""

    artifact_id: str
    type: ArtifactType
    id: str
    title: str
    path: str
    capabilities: list[Literal["read", "scratch_edit"]] = Field(
        default_factory=lambda: ["read", "scratch_edit"]
    )
    metadata: dict[str, Any] = Field(default_factory=dict)


class ArtifactWorkingSetManifest(ArtifactWorkingSetSchema):
    """Manifest describing artifact files mounted into the agent sandbox."""

    version: Literal[1] = 1
    root: str
    active_artifact_id: str | None = None
    commit_available: bool = False
    artifacts: list[ArtifactWorkingSetEntry] = Field(default_factory=list)


@dataclass(frozen=True, slots=True)
class ArtifactWorkingSetInput:
    """Resolved artifact projection for one agent turn."""

    workspace_id: uuid.UUID
    role: Role
    artifacts: tuple[Artifact, ...] = ()


@dataclass(frozen=True, slots=True)
class ArtifactWorkingSetContext:
    """Inputs available to an artifact working-set provider."""

    session_id: uuid.UUID
    workspace_id: uuid.UUID
    role: Role
    artifacts: Sequence[Artifact]
    host_work_dir: Path
    runtime_work_dir: Path


@dataclass(frozen=True, slots=True)
class ArtifactWorkingSetResult:
    """Provider output for one prepared agent turn."""

    manifest: ArtifactWorkingSetManifest
    prompt_fragment: str | None = None


class ArtifactWorkingSetProvider(Protocol):
    """Provider that prepares artifact files before an agent turn."""

    async def prepare_turn(
        self,
        ctx: ArtifactWorkingSetContext,
    ) -> ArtifactWorkingSetResult:
        """Prepare artifact files for a turn."""
        ...

    def mcp_tools(self) -> list[MCPToolDefinition]:
        """Return trusted MCP tools exposed by this provider."""
        ...
