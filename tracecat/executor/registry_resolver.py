"""Registry resolver for versioned action resolution from manifests.

Provides O(1) action resolution using registry locks with action-level bindings.
"""

from __future__ import annotations

import asyncio

from aiocache import Cache, cached
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_registry import RegistrySecretType

from tracecat.db.engine import get_async_session_context_manager
from tracecat.db.models import RegistryRepository, RegistryVersion
from tracecat.exceptions import RegistryError
from tracecat.executor.schemas import ActionImplementation
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger
from tracecat.registry.actions.schemas import RegistryActionImplValidator
from tracecat.registry.lock.types import RegistryLock
from tracecat.registry.versions.schemas import (
    RegistryVersionManifest,
    RegistryVersionManifestAction,
)


def _build_impl_index(
    manifest: RegistryVersionManifest,
    origin: str,
) -> dict[str, ActionImplementation]:
    """Build action name -> ActionImplementation index from manifest."""
    index: dict[str, ActionImplementation] = {}

    for action_name, manifest_action in manifest.actions.items():
        impl = RegistryActionImplValidator.validate_python(
            manifest_action.implementation
        )

        if impl.type == "udf":
            index[action_name] = ActionImplementation(
                type="udf",
                action_name=action_name,
                module=impl.module,
                name=impl.name,
                origin=origin,
            )
        elif impl.type == "template":
            index[action_name] = ActionImplementation(
                type="template",
                action_name=action_name,
                template_definition=impl.template_action.definition.model_dump(
                    mode="json"
                ),
                origin=origin,
            )
        else:
            raise ValueError(f"Unknown implementation type: {impl}")

    return index


async def _fetch_manifest(
    session: AsyncSession,
    origin: str,
    version: str,
    organization_id: OrganizationID,
) -> RegistryVersionManifest:
    """Fetch manifest from database for a specific origin and version."""
    statement = (
        select(RegistryVersion.manifest)
        .join(
            RegistryRepository,
            RegistryVersion.repository_id == RegistryRepository.id,
        )
        .where(
            RegistryRepository.organization_id == organization_id,
            RegistryVersion.organization_id == organization_id,
            RegistryRepository.origin == origin,
            RegistryVersion.version == version,
        )
    )

    result = await session.execute(statement)
    manifest_dict = result.scalar_one_or_none()

    if manifest_dict is None:
        raise RegistryError(
            f"Registry version not found: origin={origin!r}, version={version!r}"
        )

    return RegistryVersionManifest.model_validate(manifest_dict)


def _manifest_key_builder(
    fn: object,
    origin: str,
    version: str,
    organization_id: OrganizationID,
) -> str:
    """Build cache key for manifest entries."""
    return f"manifest:{organization_id}:{origin}:{version}"


@cached(
    ttl=60,
    cache=Cache.MEMORY,
    key_builder=_manifest_key_builder,
)
async def _get_manifest_entry(
    origin: str,
    version: str,
    organization_id: OrganizationID,
) -> tuple[RegistryVersionManifest, dict[str, ActionImplementation]]:
    """Fetch manifest and build impl index (cached with TTL).

    This function handles its own DB session and caching via aiocache.
    """
    async with get_async_session_context_manager() as session:
        manifest = await _fetch_manifest(session, origin, version, organization_id)
        impl_index = _build_impl_index(manifest, origin)

        logger.debug(
            "Cached manifest impl index",
            origin=origin,
            version=version,
            num_actions=len(impl_index),
        )

        return (manifest, impl_index)


async def prefetch_lock(lock: RegistryLock, organization_id: OrganizationID) -> None:
    """Prefetch all manifests for a registry lock into cache.

    Call this once at the start of action execution to warm the cache.
    Uses aiocache with TTL - no session needed (managed internally).
    """
    tasks = [
        _get_manifest_entry(origin, version, organization_id)
        for origin, version in lock.origins.items()
    ]
    await asyncio.gather(*tasks)

    logger.debug(
        "Prefetched registry lock",
        num_origins=len(lock.origins),
        num_actions=len(lock.actions),
    )


async def resolve_action(
    action_name: str,
    lock: RegistryLock,
    organization_id: OrganizationID,
) -> ActionImplementation:
    """Resolve action implementation from registry lock.

    O(1) lookup using action-level bindings in the lock.
    Uses aiocache - returns cached value on hit, fetches on miss.

    Args:
        action_name: Full action name (e.g., "core.transform.reshape")
        lock: Registry lock with origins and action bindings

    Returns:
        ActionImplementation for the resolved action

    Raises:
        RegistryError: If action not bound in lock
    """
    # O(1) lookup: action_name -> origin
    if action_name not in lock.actions:
        raise RegistryError(
            f"Action '{action_name}' not bound in registry_lock. "
            f"Available actions: {list(lock.actions.keys())}"
        )

    origin = lock.actions[action_name]

    # O(1) lookup: origin -> version
    if origin not in lock.origins:
        raise RegistryError(
            f"Origin '{origin}' not found in registry_lock. "
            f"Available origins: {list(lock.origins.keys())}"
        )

    version = lock.origins[origin]

    # Get from cache (or fetch if miss)
    _, impl_index = await _get_manifest_entry(origin, version, organization_id)

    if action_name not in impl_index:
        raise RegistryError(
            f"Action '{action_name}' not found in manifest for "
            f"origin={origin!r}, version={version!r}"
        )

    return impl_index[action_name]


async def collect_action_secrets_from_manifest(
    action_name: str,
    lock: RegistryLock,
    organization_id: OrganizationID,
) -> set[RegistrySecretType]:
    """Collect all secrets required by an action from manifest.

    Recursively collects secrets for template actions by walking their steps.

    Args:
        action_name: Full action name
        lock: Registry lock with origins and action bindings

    Returns:
        Set of RegistrySecretType for all required secrets
    """
    secrets: set[RegistrySecretType] = set()

    origin = lock.actions.get(action_name)
    if origin is None:
        raise RegistryError(f"Action '{action_name}' not bound in registry_lock")

    version = lock.origins.get(origin)
    if version is None:
        raise RegistryError(f"Origin '{origin}' not found in registry_lock")

    # Get from cache (or fetch if miss)
    manifest, _ = await _get_manifest_entry(origin, version, organization_id)

    manifest_action = manifest.actions.get(action_name)
    if manifest_action is None:
        raise RegistryError(
            f"Action '{action_name}' not found in manifest for "
            f"origin={origin!r}, version={version!r}"
        )

    # Collect secrets from this action
    await _collect_secrets_recursive(manifest_action, lock, secrets, organization_id)

    return secrets


async def _collect_secrets_recursive(
    manifest_action: RegistryVersionManifestAction,
    lock: RegistryLock,
    secrets: set[RegistrySecretType],
    organization_id: OrganizationID,
) -> None:
    """Recursively collect secrets from an action and its template steps."""
    impl = RegistryActionImplValidator.validate_python(manifest_action.implementation)

    if impl.type == "udf":
        # UDF: collect declared secrets
        if manifest_action.secrets:
            secrets.update(manifest_action.secrets)
    elif impl.type == "template":
        # Template: collect from definition.secrets and recurse into steps
        if impl.template_action.definition.secrets:
            secrets.update(impl.template_action.definition.secrets)

        # Recurse into template steps
        for step in impl.template_action.definition.steps:
            step_action_name = step.action

            # Get step action from its origin's manifest
            step_origin = lock.actions.get(step_action_name)
            if step_origin is None:
                # Step action not bound - skip (will error at execution time)
                continue

            step_version = lock.origins.get(step_origin)
            if step_version is None:
                continue

            # Get from cache (will be a cache hit if prefetch was called)
            try:
                step_manifest, _ = await _get_manifest_entry(
                    step_origin, step_version, organization_id
                )
            except RegistryError:
                # Step manifest not available - skip
                continue

            step_manifest_action = step_manifest.actions.get(step_action_name)
            if step_manifest_action is None:
                continue

            await _collect_secrets_recursive(
                step_manifest_action, lock, secrets, organization_id
            )


async def clear_cache() -> None:
    """Clear the manifest cache. Useful for testing."""
    await _get_manifest_entry.cache.clear()  # pyright: ignore[reportAttributeAccessIssue]
