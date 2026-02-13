"""Shared helpers for resolving and ordering registry tarball URIs."""

from __future__ import annotations

from tracecat import config
from tracecat.auth.types import Role
from tracecat.dsl.schemas import RunActionInput
from tracecat.executor.service import (
    RegistryArtifactsContext,
    get_registry_artifacts_for_lock,
)
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN


async def get_registry_tarball_uris(
    input: RunActionInput,
    role: Role,
) -> list[str]:
    """Get tarball URIs for registry environment in deterministic order."""
    if config.TRACECAT__LOCAL_REPOSITORY_ENABLED:
        return []

    if role.organization_id is None:
        raise ValueError("organization_id is required for registry artifacts lookup")

    artifacts = await get_registry_artifacts_for_lock(
        input.registry_lock.origins, role.organization_id
    )
    return sort_registry_tarball_uris(artifacts)


def sort_registry_tarball_uris(artifacts: list[RegistryArtifactsContext]) -> list[str]:
    """Sort tarballs: tracecat_registry first, then lexicographically by origin."""
    builtin_uris: list[str] = []
    other_uris: list[tuple[str, str]] = []

    for artifact in artifacts:
        if not artifact.tarball_uri:
            continue
        if artifact.origin == DEFAULT_REGISTRY_ORIGIN:
            builtin_uris.append(artifact.tarball_uri)
        else:
            other_uris.append((artifact.origin, artifact.tarball_uri))

    other_uris.sort(key=lambda item: item[0])
    return builtin_uris + [uri for _, uri in other_uris]
