"""Service for resolving and managing registry version locks."""

from __future__ import annotations

from sqlalchemy import select

from tracecat import config
from tracecat.db.models import RegistryRepository, RegistryVersion
from tracecat.exceptions import RegistryError
from tracecat.registry.lock.types import RegistryLock
from tracecat.registry.versions.schemas import RegistryVersionManifest
from tracecat.service import BaseService


class RegistryLockService(BaseService):
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

        Args:
            action_names: All action names used in the workflow

        Returns:
            RegistryLock with origins and action bindings

        Raises:
            RegistryError: If an action is not found in any registry or is ambiguous
        """
        # 1. Get latest versions for all repos with full manifest
        statement = (
            select(
                RegistryRepository.origin,
                RegistryVersion.version,
                RegistryVersion.manifest,
            )
            .join(
                RegistryVersion,
                RegistryVersion.repository_id == RegistryRepository.id,
            )
            .where(
                RegistryRepository.organization_id == config.TRACECAT__DEFAULT_ORG_ID,
                RegistryVersion.organization_id == config.TRACECAT__DEFAULT_ORG_ID,
            )
            .distinct(RegistryVersion.repository_id)
            .order_by(
                RegistryVersion.repository_id,
                RegistryVersion.created_at.desc(),
                RegistryVersion.id.desc(),
            )
        )

        result = await self.session.execute(statement)
        rows = result.all()

        # 2. Build origins dict and parse manifests
        origins: dict[str, str] = {}
        origin_manifests: dict[str, RegistryVersionManifest] = {}

        for origin, version, manifest_dict in rows:
            origin_str = str(origin)
            origins[origin_str] = str(version)
            origin_manifests[origin_str] = RegistryVersionManifest.model_validate(
                manifest_dict
            )

        # 3. Build action -> origin mapping
        actions: dict[str, str] = {}
        for action_name in action_names:
            matching_origins: list[str] = []
            for origin_str, manifest in origin_manifests.items():
                if action_name in manifest.actions:
                    matching_origins.append(origin_str)

            if len(matching_origins) == 0:
                raise RegistryError(
                    f"Action '{action_name}' not found in any registry. "
                    f"Available registries: {list(origins.keys())}"
                )
            if len(matching_origins) > 1:
                raise RegistryError(
                    f"Ambiguous action '{action_name}' found in multiple registries: "
                    f"{matching_origins}. Please specify the registry explicitly."
                )
            actions[action_name] = matching_origins[0]

        self.logger.debug(
            "Resolved lock with bindings",
            num_origins=len(origins),
            num_actions=len(actions),
        )

        return RegistryLock(origins=origins, actions=actions)
