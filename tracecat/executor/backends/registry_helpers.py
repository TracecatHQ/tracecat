"""Shared helpers for resolving and ordering registry artifact URIs."""

from __future__ import annotations

import functools

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
from tracecat.registry.versions.schemas import (
    registry_base_version,
    registry_manifest_fingerprint,
)


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

    builtin_version = origins.get(DEFAULT_REGISTRY_ORIGIN)
    if builtin_version is not None and _bundled_builtin_matches_lock(
        input.registry_lock, builtin_version
    ):
        del origins[DEFAULT_REGISTRY_ORIGIN]
        artifact_uris.append(
            bundled_builtin_registry_uri(tracecat_registry.__version__)
        )

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
    """Return whether this executor can satisfy the locked builtin from its image.

    A lock on the installed version, or on a content version of it
    (``<installed>+content.<fingerprint>``), matches when its builtin manifest
    fingerprint equals the image's prebuilt manifest. Without a fingerprint only
    the exact installed version is trusted.
    """
    installed_version = tracecat_registry.__version__
    if registry_base_version(version) != installed_version:
        return False

    origin_fingerprints = getattr(registry_lock, "origin_fingerprints", None)
    expected = (
        origin_fingerprints.get(DEFAULT_REGISTRY_ORIGIN)
        if isinstance(origin_fingerprints, dict)
        else None
    )
    if expected is None:
        return version == installed_version

    actual = _bundled_builtin_fingerprint(
        config.TRACECAT__REGISTRY_SYNC_PREBUILT_ARTIFACTS_DIR, installed_version
    )
    if actual is None:
        logger.info(
            "Bundled builtin registry fingerprint unavailable; using artifact lookup",
            registry_version=version,
        )
        return False

    if actual != expected:
        logger.info(
            "Bundled builtin registry fingerprint mismatch; using artifact lookup",
            registry_version=version,
            expected_fingerprint=expected,
            actual_fingerprint=actual,
        )
        return False

    return True


@functools.cache
def _bundled_builtin_fingerprint(prebuilt_dir: str, version: str) -> str | None:
    """Fingerprint the image's prebuilt builtin manifest once per process.

    The manifest is baked into the image and never changes under a running
    process, so parsing the multi-megabyte file on every action is wasted work.
    ``prebuilt_dir`` is part of the key because the loader resolves it from config.
    """
    manifest = load_prebuilt_builtin_registry_manifest(
        origin=DEFAULT_REGISTRY_ORIGIN,
        target_version=version,
        storage_namespace=PLATFORM_REGISTRY_NAMESPACE,
    )
    if manifest is None:
        return None
    return registry_manifest_fingerprint(manifest)


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
