"""Registry sync service for v2 versioned registry flow."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar, override

from sqlalchemy import func, select

from tracecat.auth.types import Role
from tracecat.db.models import (
    PlatformRegistryRepository,
    PlatformRegistryVersion,
    RegistryRepository,
    RegistryVersion,
)
from tracecat.exceptions import RegistryError, TracecatAuthorizationError
from tracecat.registry.constants import DEFAULT_REGISTRY_ORIGIN
from tracecat.registry.sync.base_service import BaseRegistrySyncService, BaseSyncResult
from tracecat.registry.versions.schemas import RegistryVersionManifest
from tracecat.registry.versions.service import RegistryVersionsService


class RegistrySyncError(RegistryError):
    """Raised when registry sync fails."""


class RegistryActionShadowsBuiltinError(RegistrySyncError):
    """Raised when a custom registry defines actions that already exist builtin.

    Every action name must resolve to exactly one registry, so a custom
    registry cannot redefine a platform action under the same namespace.name.
    """

    def __init__(self, origin: str, shadowed_actions: list[str]):
        message = (
            f"Registry {origin} defines {len(shadowed_actions)} action(s) that "
            f"already exist in the builtin registry: {shadowed_actions}. Rename "
            "them under a distinct namespace (for example tools.<vendor>_custom) "
            "or remove them, then sync again."
        )
        super().__init__(
            message,
            detail={"origin": origin, "shadowed_actions": shadowed_actions},
        )
        self.origin = origin
        self.shadowed_actions = shadowed_actions


@dataclass
class SyncResult(BaseSyncResult[RegistryVersion]):
    """Result of a registry sync operation."""


class RegistrySyncService(BaseRegistrySyncService[RegistryRepository, RegistryVersion]):
    """Service for orchestrating registry sync operations.

    Requires organization context (role must be non-None).
    """

    service_name: ClassVar[str] = "registry_sync"

    @override
    @classmethod
    def _versions_service_cls(cls) -> type[RegistryVersionsService]:
        return RegistryVersionsService

    @override
    @classmethod
    def _result_cls(cls) -> type[SyncResult]:
        return SyncResult

    @override
    @classmethod
    def _sync_error_cls(cls) -> type[Exception]:
        return RegistrySyncError

    @override
    async def _reject_shadowed_platform_actions(
        self, *, origin: str, manifest: RegistryVersionManifest
    ) -> None:
        if origin == DEFAULT_REGISTRY_ORIGIN or not manifest.actions:
            return

        platform_action_names = (
            select(
                func.jsonb_object_keys(
                    PlatformRegistryVersion.manifest["actions"]
                ).label("action_name")
            )
            .join(
                PlatformRegistryRepository,
                PlatformRegistryRepository.current_version_id
                == PlatformRegistryVersion.id,
            )
            .subquery()
        )
        statement = (
            select(platform_action_names.c.action_name)
            .where(
                platform_action_names.c.action_name.in_(list(manifest.actions.keys()))
            )
            .order_by(platform_action_names.c.action_name)
        )
        result = await self.session.execute(statement)
        shadowed_actions = [str(name) for name in result.scalars().all()]
        if shadowed_actions:
            raise RegistryActionShadowsBuiltinError(origin, shadowed_actions)

    @override
    def _get_storage_namespace(self) -> str:
        """Get storage namespace from the organization context."""
        if self.role is None or not isinstance(self.role, Role):
            raise TracecatAuthorizationError(
                "RegistrySyncService requires organization context"
            )
        if self.role.organization_id is None:
            raise TracecatAuthorizationError(
                "RegistrySyncService requires organization_id in role"
            )
        return str(self.role.organization_id)
