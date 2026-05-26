"""Shared helpers for resolving and ordering registry artifact URIs."""

from __future__ import annotations

import tracecat_registry

from tracecat import config
from tracecat.auth.types import Role
from tracecat.dsl.schemas import RunActionInput
from tracecat.executor.registry_artifacts import bundled_builtin_registry_uri
from tracecat.executor.service import (
    RegistryArtifactsContext,
    get_registry_artifacts_for_lock,
)
from tracecat.logger import logger
from tracecat.registry.constants import (
    DEFAULT_REGISTRY_ORIGIN,
    PLATFORM_REGISTRY_NAMESPACE,
)
from tracecat.registry.sync.prebuilt import load_prebuilt_builtin_registry_manifest
from tracecat.registry.versions.schemas import registry_manifest_fingerprint


async def get_registry_artifact_uris(
    input: RunActionInput,
    role: Role,
) -> list[str]:
    """Get registry artifact URIs for the execution environment.

    The returned list may include a pseudo-URI for the builtin registry package
    already installed in the executor image.
    """
    if config.TRACECAT__LOCAL_REPOSITORY_ENABLED:
        return []

    origins = dict(input.registry_lock.origins)
    artifact_uris: list[str] = []

    if (
        origins.get(DEFAULT_REGISTRY_ORIGIN) == tracecat_registry.__version__
        and (builtin_version := origins.get(DEFAULT_REGISTRY_ORIGIN)) is not None
        and _bundled_builtin_matches_lock(input.registry_lock, builtin_version)
    ):
        del origins[DEFAULT_REGISTRY_ORIGIN]
        artifact_uris.append(bundled_builtin_registry_uri(builtin_version))

    if not origins:
        return artifact_uris

    if role.organization_id is None:
        raise ValueError("organization_id is required for registry artifacts lookup")

    artifacts = await get_registry_artifacts_for_lock(
        origins,
        role.organization_id,
    )
    return artifact_uris + sort_registry_artifact_uris(artifacts)


def _bundled_builtin_matches_lock(registry_lock: object, version: str) -> bool:
    """Return whether this executor can satisfy the locked builtin from its image."""
    origin_fingerprints = getattr(registry_lock, "origin_fingerprints", None)
    if not isinstance(origin_fingerprints, dict):
        return True

    expected = origin_fingerprints.get(DEFAULT_REGISTRY_ORIGIN)
    if expected is None:
        return True

    manifest = load_prebuilt_builtin_registry_manifest(
        origin=DEFAULT_REGISTRY_ORIGIN,
        target_version=version,
        storage_namespace=PLATFORM_REGISTRY_NAMESPACE,
    )
    if manifest is None:
        logger.info(
            "Bundled builtin registry fingerprint unavailable; using artifact lookup",
            registry_version=version,
        )
        return False

    actual = registry_manifest_fingerprint(manifest)
    if actual != expected:
        logger.info(
            "Bundled builtin registry fingerprint mismatch; using artifact lookup",
            registry_version=version,
            expected_fingerprint=expected,
            actual_fingerprint=actual,
        )
        return False

    return True


def sort_registry_artifact_uris(
    artifacts: list[RegistryArtifactsContext],
) -> list[str]:
    """Sort artifacts: tracecat_registry first, then lexicographically by origin."""
    builtin_uris: list[str] = []
    other_uris: list[tuple[str, str]] = []

    for artifact in artifacts:
        if not artifact.artifact_uri:
            continue
        if artifact.origin == DEFAULT_REGISTRY_ORIGIN:
            builtin_uris.append(artifact.artifact_uri)
        else:
            other_uris.append((artifact.origin, artifact.artifact_uri))

    other_uris.sort(key=lambda item: item[0])
    return builtin_uris + [uri for _, uri in other_uris]
