from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from sqlalchemy import func, select, tuple_
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import RegistryIndex, RegistryRepository, RegistryVersion
from tracecat.dsl.common import DSLInput
from tracecat.dsl.enums import PlatformAction
from tracecat.identifiers import OrganizationID
from tracecat.registry.actions.schemas import RegistryActionImplValidator
from tracecat.registry.versions.schemas import RegistryVersionManifest


@dataclass(frozen=True)
class RegistryActionResolutionError:
    action: str
    msg: str
    detail: dict[str, Any] | None = None


class WorkflowRegistryResolutionError(Exception):
    def __init__(self, errors: list[RegistryActionResolutionError]) -> None:
        super().__init__("Workflow registry resolution failed")
        self.errors = errors


async def resolve_action_origins_from_lock(
    *,
    session: AsyncSession,
    dsl: DSLInput,
    registry_lock: dict[str, str],
    organization_id: OrganizationID,
) -> tuple[dict[str, str], list[RegistryActionResolutionError]]:
    """Resolve action -> registry origin using RegistryIndex + RegistryVersion manifests.

    The resolution is computed for:
    - All workflow actions (excluding PlatformAction.* handled by the runner)
    - All transitive template step actions (recursively), using the pinned versions

    This is intended for publish-time resolution to avoid ambiguous action names at runtime.
    """
    roots = {stmt.action for stmt in dsl.actions}
    if not roots:
        return {}, []

    # Fetch exact RegistryVersion rows for the pinned lock.
    origin_version_to_version_id: dict[tuple[str, str], UUID] = {}
    manifest_by_version_id: dict[UUID, RegistryVersionManifest] = {}
    lock_pairs = list(registry_lock.items())
    if not lock_pairs:
        return {}, [
            RegistryActionResolutionError(
                action=action,
                msg="No registry versions are locked for this workflow",
                detail={"registry_lock": registry_lock},
            )
            for action in sorted(roots)
        ]
    lock_stmt = (
        select(
            RegistryRepository.origin,
            RegistryVersion.version,
            RegistryVersion.id,
            RegistryVersion.manifest,
        )
        .join(RegistryVersion, RegistryVersion.repository_id == RegistryRepository.id)
        .where(
            RegistryRepository.organization_id == organization_id,
            RegistryVersion.organization_id == organization_id,
            tuple_(RegistryRepository.origin, RegistryVersion.version).in_(lock_pairs),
        )
    )
    lock_result = await session.execute(lock_stmt)
    for origin, version, version_id, manifest in lock_result.all():
        origin_str = str(origin)
        version_str = str(version)
        origin_version_to_version_id[(origin_str, version_str)] = version_id
        manifest_by_version_id[version_id] = RegistryVersionManifest.model_validate(
            manifest
        )

    # Ensure every origin in lock actually exists in DB at that version.
    errors: list[RegistryActionResolutionError] = []
    for origin, version in registry_lock.items():
        if (origin, version) not in origin_version_to_version_id:
            errors.append(
                RegistryActionResolutionError(
                    action="*",
                    msg="Locked registry version not found",
                    detail={"origin": origin, "version": version},
                )
            )
    if errors:
        return {}, errors

    # Build list of registry_version_ids to search within.
    version_ids = list(origin_version_to_version_id.values())
    version_id_to_origin: dict[UUID, str] = {
        vid: origin for (origin, _version), vid in origin_version_to_version_id.items()
    }

    action_name_expr = func.concat(RegistryIndex.namespace, ".", RegistryIndex.name)

    resolved: dict[str, str] = {}
    queue: deque[str] = deque(sorted(roots))

    while queue:
        batch: set[str] = set()
        while queue and len(batch) < 100:
            action = queue.popleft()
            if action not in resolved:
                batch.add(action)
        if not batch:
            continue

        stmt = (
            select(
                RegistryIndex.registry_version_id,
                action_name_expr.label("action"),
                RegistryIndex.action_type,
            )
            .where(
                RegistryIndex.registry_version_id.in_(version_ids),
                action_name_expr.in_(batch),
            )
            .order_by(action_name_expr)
        )
        rows = (await session.execute(stmt)).all()

        matches: dict[str, list[tuple[UUID, str]]] = defaultdict(list)
        for registry_version_id, action_name, action_type in rows:
            matches[str(action_name)].append((registry_version_id, str(action_type)))

        for action_name in batch:
            action_matches = matches.get(action_name, [])
            if not action_matches:
                errors.append(
                    RegistryActionResolutionError(
                        action=action_name,
                        msg="Action not found in locked registries",
                        detail={"registry_lock": registry_lock},
                    )
                )
                continue
            unique_registry_versions = {rv_id for rv_id, _ in action_matches}
            if len(unique_registry_versions) > 1:
                origins = sorted(
                    {
                        version_id_to_origin[rv_id]
                        for rv_id in unique_registry_versions
                        if rv_id in version_id_to_origin
                    }
                )
                errors.append(
                    RegistryActionResolutionError(
                        action=action_name,
                        msg="Action is ambiguous across locked registries",
                        detail={
                            "origins": origins,
                            "registry_lock": registry_lock,
                        },
                    )
                )
                continue

            registry_version_id, action_type = action_matches[0]
            origin = version_id_to_origin[registry_version_id]
            resolved[action_name] = origin

            if action_type != "template":
                continue

            # Recurse into template steps using the frozen manifest implementation.
            manifest = manifest_by_version_id.get(registry_version_id)
            if manifest is None:
                errors.append(
                    RegistryActionResolutionError(
                        action=action_name,
                        msg="Missing manifest for locked registry version",
                        detail={
                            "origin": origin,
                            "registry_version_id": str(registry_version_id),
                        },
                    )
                )
                continue
            manifest_action = manifest.actions.get(action_name)
            if manifest_action is None:
                errors.append(
                    RegistryActionResolutionError(
                        action=action_name,
                        msg="Action missing from manifest for locked registry version",
                        detail={
                            "origin": origin,
                            "registry_version_id": str(registry_version_id),
                        },
                    )
                )
                continue
            impl = RegistryActionImplValidator.validate_python(
                manifest_action.implementation
            )
            if impl.type != "template":
                errors.append(
                    RegistryActionResolutionError(
                        action=action_name,
                        msg="RegistryIndex marked template but manifest implementation is not a template",
                        detail={
                            "origin": origin,
                            "registry_version_id": str(registry_version_id),
                        },
                    )
                )
                continue
            for step in impl.template_action.definition.steps:
                if PlatformAction.is_interface(step.action):
                    errors.append(
                        RegistryActionResolutionError(
                            action=step.action,
                            msg=f"Platform action '{step.action}' cannot be used inside template '{action_name}' (step '{step.ref}'). Use platform actions directly in workflows.",
                            detail={
                                "template": action_name,
                                "step_ref": step.ref,
                                "origin": origin,
                            },
                        )
                    )
                    continue
                if step.action not in resolved:
                    queue.append(step.action)

    if errors:
        return {}, errors
    return resolved, []
