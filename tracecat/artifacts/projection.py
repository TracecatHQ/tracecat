"""Helpers for reducing artifact operations into persisted panel state."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, cast

from pydantic import TypeAdapter

from tracecat.artifacts.bindings import ArtifactSideEffect
from tracecat.artifacts.schemas import Artifact, ArtifactAdapter, ArtifactType

ArtifactsAdapter: TypeAdapter[list[Artifact]] = TypeAdapter(list[Artifact])

# Maximum artifacts retained in the panel projection. Oldest entries are
# evicted first so repeated tool calls don't accumulate unbounded tabs.
# Must match MAX_OPEN_ARTIFACT_TABS in
# frontend/src/hooks/use-workspace-chat-artifacts.ts.
MAX_OPEN_ARTIFACTS = 5


def artifact_key(artifact: Artifact) -> str:
    """Return the stable projection key for an artifact."""
    return f"{artifact.type}:{artifact.id}"


def serialize_artifact(artifact: Artifact) -> dict[str, Any]:
    """Serialize an artifact for JSONB persistence."""
    value = ArtifactAdapter.dump_python(
        artifact,
        mode="json",
        by_alias=True,
        exclude_none=True,
    )
    return cast(dict[str, Any], value)


def serialize_artifacts(artifacts: Iterable[Artifact]) -> list[dict[str, Any]]:
    """Serialize artifacts for JSONB persistence."""
    return [serialize_artifact(artifact) for artifact in artifacts]


def validate_artifacts(value: Any) -> list[Artifact]:
    """Validate the persisted artifact projection."""
    if value is None:
        return []
    return ArtifactsAdapter.validate_python(value)


def apply_artifact_side_effects(
    artifacts: Iterable[Artifact],
    effects: Iterable[ArtifactSideEffect],
) -> list[Artifact]:
    """Apply artifact operations, keeping at most the most recent artifacts."""
    projected = {artifact_key(artifact): artifact for artifact in artifacts}
    for effect in effects:
        key = artifact_key(effect.artifact)
        match effect.op:
            case "upsert":
                # Pop first so re-upserting an open artifact refreshes its
                # recency instead of keeping its original panel position.
                projected.pop(key, None)
                projected[key] = effect.artifact
            case "remove":
                projected.pop(key, None)
    result = list(projected.values())
    if len(result) > MAX_OPEN_ARTIFACTS:
        result = result[-MAX_OPEN_ARTIFACTS:]
    return result


def remove_artifact(
    artifacts: Iterable[Artifact],
    *,
    artifact_type: ArtifactType,
    artifact_id: str,
) -> list[Artifact]:
    """Remove one artifact from a projected artifact list."""
    remove_key = f"{artifact_type}:{artifact_id}"
    return [artifact for artifact in artifacts if artifact_key(artifact) != remove_key]
