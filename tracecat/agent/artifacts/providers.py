"""Provider resolution for agent artifact working sets."""

from __future__ import annotations

import os
from functools import lru_cache
from importlib import import_module
from importlib.metadata import entry_points
from typing import cast

from tracecat.agent.artifacts.working_set import (
    ArtifactWorkingSetContext,
    ArtifactWorkingSetManifest,
    ArtifactWorkingSetProvider,
    ArtifactWorkingSetResult,
)
from tracecat.agent.common.types import MCPToolDefinition

ARTIFACT_PROVIDER_ENTRY_POINT_GROUP = "tracecat.agent_artifact_provider"
ARTIFACT_PROVIDER_ENV = "TRACECAT__AGENT_ARTIFACT_PROVIDER"


class NoopArtifactWorkingSetProvider:
    """Default provider used when no artifact extension is installed."""

    async def prepare_turn(
        self,
        ctx: ArtifactWorkingSetContext,
    ) -> ArtifactWorkingSetResult:
        """Return an empty manifest without writing files."""
        root = str(ctx.runtime_work_dir / ".tracecat" / "artifacts")
        return ArtifactWorkingSetResult(
            manifest=ArtifactWorkingSetManifest(root=root),
            prompt_fragment=None,
        )

    def mcp_tools(self) -> list[MCPToolDefinition]:
        """Return no tools."""
        return []


def build_noop_provider() -> ArtifactWorkingSetProvider:
    """Build the default no-op provider."""
    return NoopArtifactWorkingSetProvider()


def _load_provider_from_import_path(import_path: str) -> ArtifactWorkingSetProvider:
    module_name, separator, attribute = import_path.partition(":")
    if not separator or not module_name or not attribute:
        raise ValueError(
            f"Invalid artifact provider import path {import_path!r}; "
            "expected 'module:attribute'."
        )
    module = import_module(module_name)
    candidate = getattr(module, attribute)
    if callable(candidate):
        candidate = candidate()
    return cast(ArtifactWorkingSetProvider, candidate)


@lru_cache(maxsize=1)
def get_artifact_working_set_provider() -> ArtifactWorkingSetProvider:
    """Resolve the configured artifact working-set provider."""
    if import_path := os.environ.get(ARTIFACT_PROVIDER_ENV):
        return _load_provider_from_import_path(import_path)

    for entry_point in entry_points(group=ARTIFACT_PROVIDER_ENTRY_POINT_GROUP):
        candidate = entry_point.load()
        if callable(candidate):
            candidate = candidate()
        return cast(ArtifactWorkingSetProvider, candidate)

    return build_noop_provider()
