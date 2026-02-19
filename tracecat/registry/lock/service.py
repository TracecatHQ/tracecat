"""Service for resolving and managing registry version locks."""

from __future__ import annotations

from collections import deque

from sqlalchemy import select

from tracecat.db.models import (
    PlatformRegistryRepository,
    PlatformRegistryVersion,
    RegistryRepository,
    RegistryVersion,
)
from tracecat.dsl.enums import PlatformAction
from tracecat.exceptions import EntitlementRequired, RegistryError
from tracecat.registry.actions.schemas import RegistryActionImplValidator
from tracecat.registry.lock.types import RegistryLock
from tracecat.registry.versions.schemas import RegistryVersionManifest
from tracecat.service import BaseOrgService
from tracecat.tiers.enums import Entitlement


class RegistryLockService(BaseOrgService):
    """Service for resolving and managing registry version locks.

    Registry locks map repository origins to specific version strings,
    allowing workflows to pin their dependent registry versions for
    reproducible execution.
    """

    service_name = "registry_lock"

    async def resolve_lock_with_bindings(
        self,
        action_names: set[str],
    ) -> RegistryLock:
        """Resolve registry lock with action-level bindings.

        For each action in the workflow, determines which registry origin
        contains it and builds a mapping for O(1) resolution at execution time.

        This method recursively discovers template step actions, ensuring all
        actions needed for execution (including nested template steps) are
        included in the lock.

        Actions are resolved from both platform registries (globally shared)
        and organization registries. Organization registries take precedence
        when the same origin exists in both.

        Args:
            action_names: Top-level action names used in the workflow

        Returns:
            RegistryLock with origins and action bindings for all actions

        Raises:
            RegistryError: If an action is not found in any registry or is ambiguous
            RegistryError: If a repository has no current_version_id set
        """
        # 1. Query platform registries via current_version_id
        platform_statement = (
            select(
                PlatformRegistryRepository.origin,
                PlatformRegistryVersion.version,
                PlatformRegistryVersion.manifest,
            )
            .join(
                PlatformRegistryVersion,
                PlatformRegistryRepository.current_version_id
                == PlatformRegistryVersion.id,
            )
            .where(
                PlatformRegistryRepository.current_version_id.is_not(None),
            )
        )
        platform_result = await self.session.execute(platform_statement)
        platform_rows = platform_result.tuples().all()

        # 2. Query org registries via current_version_id
        org_statement = (
            select(
                RegistryRepository.origin,
                RegistryVersion.version,
                RegistryVersion.manifest,
            )
            .join(
                RegistryVersion,
                RegistryRepository.current_version_id == RegistryVersion.id,
            )
            .where(
                RegistryRepository.organization_id == self.organization_id,
                RegistryRepository.current_version_id.is_not(None),
            )
        )
        org_result = await self.session.execute(org_statement)
        org_rows = org_result.tuples().all()

        custom_registry_enabled = await self.has_entitlement(
            Entitlement.CUSTOM_REGISTRY
        )
        if not custom_registry_enabled and org_rows:
            self.logger.info(
                "Custom registry entitlement disabled; excluding org registry manifests from lock resolution",
                organization_id=str(self.organization_id),
                org_registry_count=len(org_rows),
            )

        # 3. Combine: platform first, then org (org overrides for same origin).
        # When custom registry entitlement is disabled, only platform registries
        # are considered for lock resolution.
        rows = list(platform_rows)
        if custom_registry_enabled:
            rows.extend(org_rows)

        # 2. Build origins dict and parse manifests
        origins: dict[str, str] = {}
        origin_manifests: dict[str, RegistryVersionManifest] = {}
        excluded_custom_origin_manifests: dict[str, RegistryVersionManifest] = {}

        for origin, version, manifest_dict in rows:
            origin_str = str(origin)
            origins[origin_str] = str(version)
            origin_manifests[origin_str] = RegistryVersionManifest.model_validate(
                manifest_dict
            )
        if not custom_registry_enabled:
            for origin, _version, manifest_dict in org_rows:
                origin_str = str(origin)
                excluded_custom_origin_manifests[origin_str] = (
                    RegistryVersionManifest.model_validate(manifest_dict)
                )

        # 3. Build action -> origin mapping using BFS to include template step actions
        actions: dict[str, str] = {}
        queue: deque[str] = deque(sorted(action_names))

        while queue:
            action_name = queue.popleft()

            # Skip if already resolved
            if action_name in actions:
                continue

            # Skip platform/interface actions that are handled entirely by
            # the workflow engine/scheduler and don't live in any registry.
            # NOTE: core.script.run_python is excluded from this check because
            # it has dual identity — it's both an interface action AND a real
            # registry action resolved by the executor.
            if (
                PlatformAction.is_interface(action_name)
                and action_name != PlatformAction.RUN_PYTHON
            ):
                continue

            matching_origins: list[str] = []
            for origin_str, manifest in origin_manifests.items():
                if action_name in manifest.actions:
                    matching_origins.append(origin_str)

            if len(matching_origins) == 0:
                if not custom_registry_enabled:
                    if any(
                        action_name in manifest.actions
                        for manifest in excluded_custom_origin_manifests.values()
                    ):
                        raise EntitlementRequired(
                            Entitlement.CUSTOM_REGISTRY.value,
                            unavailable_actions=[action_name],
                        )
                raise RegistryError(
                    f"Action '{action_name}' not found in any registry. "
                    f"Available registries: {list(origins.keys())}"
                )
            if len(matching_origins) > 1:
                raise RegistryError(
                    f"Ambiguous action '{action_name}' found in multiple registries: "
                    f"{matching_origins}. Please specify the registry explicitly."
                )

            resolved_origin = matching_origins[0]
            actions[action_name] = resolved_origin

            # If this is a template action, add its step actions to the queue
            manifest = origin_manifests[resolved_origin]
            manifest_action = manifest.actions.get(action_name)
            if manifest_action is not None:
                impl = RegistryActionImplValidator.validate_python(
                    manifest_action.implementation
                )
                if impl.type == "template":
                    for step in impl.template_action.definition.steps:
                        if not PlatformAction.is_template_step_supported(step.action):
                            raise RegistryError(
                                f"Template action '{action_name}' contains step '{step.ref}' using "
                                f"platform action '{step.action}'. Platform actions cannot be used "
                                f"inside templates - use them directly in workflows instead."
                            )
                        if step.action not in actions:
                            queue.append(step.action)

        # Only keep origins that are actually needed for the resolved actions.
        used_origins = set(actions.values())
        origins = {
            origin: version
            for origin, version in origins.items()
            if origin in used_origins
        }

        self.logger.debug(
            "Resolved lock with bindings",
            num_origins=len(origins),
            num_actions=len(actions),
        )

        return RegistryLock(origins=origins, actions=actions)
