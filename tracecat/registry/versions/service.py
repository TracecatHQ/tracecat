"""Service for managing registry versions."""

from __future__ import annotations

from collections.abc import Sequence
from typing import ClassVar
from uuid import UUID

from pydantic_core import to_jsonable_python
from sqlalchemy import cast, select
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tracecat.auth.types import PlatformRole, Role
from tracecat.db.models import (
    PlatformRegistryIndex,
    PlatformRegistryVersion,
    RegistryIndex,
    RegistryVersion,
    WorkflowDefinition,
    Workspace,
)
from tracecat.registry.versions.schemas import (
    ActionChange,
    ActionInterfaceChange,
    RegistryIndexCreate,
    RegistryVersionCreate,
    RegistryVersionManifest,
    VersionDiff,
)
from tracecat.service import BaseOrgService, BaseService


class RegistryVersionsService(BaseOrgService):
    """Service for managing immutable registry versions."""

    service_name: ClassVar[str] = "registry_versions"

    def __init__(
        self,
        session: AsyncSession,
        role: Role | PlatformRole | None = None,  # Protocol compatibility
    ):
        # Note: PlatformRole will fail BaseOrgService validation since it lacks organization_id
        if isinstance(role, PlatformRole):
            raise ValueError(
                "RegistryVersionsService requires org-scoped Role, not PlatformRole"
            )
        super().__init__(session, role)

    async def list_versions(
        self,
        repository_id: UUID | None = None,
    ) -> Sequence[RegistryVersion]:
        """List all registry versions, optionally filtered by repository."""
        statement = (
            select(RegistryVersion)
            .where(RegistryVersion.organization_id == self.organization_id)
            .order_by(RegistryVersion.created_at.desc())
        )
        if repository_id:
            statement = statement.where(RegistryVersion.repository_id == repository_id)
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_version(self, version_id: UUID) -> RegistryVersion | None:
        """Get a registry version by ID."""
        statement = (
            select(RegistryVersion)
            .options(selectinload(RegistryVersion.index_entries))
            .where(RegistryVersion.id == version_id)
            .where(RegistryVersion.organization_id == self.organization_id)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_version_by_repo_and_version(
        self,
        repository_id: UUID,
        version: str,
    ) -> RegistryVersion | None:
        """Get a specific version of a repository."""
        statement = (
            select(RegistryVersion)
            .options(selectinload(RegistryVersion.index_entries))
            .where(
                RegistryVersion.organization_id == self.organization_id,
                RegistryVersion.repository_id == repository_id,
                RegistryVersion.version == version,
            )
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def create_version(
        self,
        params: RegistryVersionCreate,
        *,
        commit: bool = True,
    ) -> RegistryVersion:
        """Create a new registry version with its manifest.

        This creates an immutable snapshot of the registry at a point in time.
        """
        version = RegistryVersion(
            organization_id=self.organization_id,
            repository_id=params.repository_id,
            version=params.version,
            commit_sha=params.commit_sha,
            manifest=to_jsonable_python(params.manifest),
            tarball_uri=params.tarball_uri,
        )
        self.session.add(version)
        if commit:
            await self.session.commit()
            await self.session.refresh(version)
        else:
            await self.session.flush()
        return version

    async def delete_version(
        self,
        version: RegistryVersion,
        *,
        commit: bool = True,
    ) -> None:
        """Delete a registry version.

        Note: This also deletes all associated RegistryIndex entries
        due to CASCADE delete.
        """
        await self.session.delete(version)
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()

    async def populate_index_from_manifest(
        self,
        version: RegistryVersion,
        *,
        commit: bool = True,
    ) -> list[RegistryIndex]:
        """Populate RegistryIndex entries from a version's manifest.

        This creates index entries for fast action lookups in the UI
        and workflow validation.
        """
        manifest = RegistryVersionManifest.model_validate(version.manifest)
        index_entries: list[RegistryIndex] = []

        for _action_name, action_def in manifest.actions.items():
            index_entry = RegistryIndex(
                organization_id=version.organization_id,
                registry_version_id=version.id,
                namespace=action_def.namespace,
                name=action_def.name,
                action_type=action_def.action_type,
                description=action_def.description,
                default_title=action_def.default_title,
                display_group=action_def.display_group,
                doc_url=action_def.doc_url,
                author=action_def.author,
                deprecated=action_def.deprecated,
                secrets=to_jsonable_python(action_def.secrets)
                if action_def.secrets
                else None,
                interface=dict(action_def.interface),
                options=action_def.options.model_dump() if action_def.options else {},
            )
            self.session.add(index_entry)
            index_entries.append(index_entry)

        if commit:
            await self.session.commit()
        else:
            await self.session.flush()

        self.logger.info(
            "Populated registry index",
            version_id=str(version.id),
            num_entries=len(index_entries),
        )
        return index_entries

    async def get_index_entries(
        self,
        version_id: UUID,
    ) -> Sequence[RegistryIndex]:
        """Get all index entries for a version."""
        statement = select(RegistryIndex).where(
            RegistryIndex.organization_id == self.organization_id,
            RegistryIndex.registry_version_id == version_id,
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def create_index_entry(
        self,
        params: RegistryIndexCreate,
        *,
        commit: bool = True,
    ) -> RegistryIndex:
        """Create a single registry index entry."""
        index_entry = RegistryIndex(
            organization_id=self.organization_id,
            registry_version_id=params.registry_version_id,
            namespace=params.namespace,
            name=params.name,
            action_type=params.action_type,
            description=params.description,
            default_title=params.default_title,
            display_group=params.display_group,
            doc_url=params.doc_url,
            author=params.author,
            deprecated=params.deprecated,
            secrets=params.secrets,
            interface=params.interface,
            options=params.options,
        )
        self.session.add(index_entry)
        if commit:
            await self.session.commit()
            await self.session.refresh(index_entry)
        else:
            await self.session.flush()
        return index_entry

    async def get_previous_version(
        self,
        repository_id: UUID,
        current_version_id: UUID,
    ) -> RegistryVersion | None:
        """Get the most recent version before the current one.

        Useful for quick rollback UX - returns the version to rollback to.
        """
        # Get the current version's created_at for comparison
        current_stmt = select(RegistryVersion.created_at).where(
            RegistryVersion.id == current_version_id,
            RegistryVersion.organization_id == self.organization_id,
        )
        result = await self.session.execute(current_stmt)
        current_created_at = result.scalar_one_or_none()
        if current_created_at is None:
            return None

        # Find the most recent version before the current one
        stmt = (
            select(RegistryVersion)
            .where(
                RegistryVersion.repository_id == repository_id,
                RegistryVersion.organization_id == self.organization_id,
                RegistryVersion.created_at < current_created_at,
            )
            .order_by(RegistryVersion.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def compare_versions(
        self,
        base_version: RegistryVersion,
        compare_version: RegistryVersion,
    ) -> VersionDiff:
        """Compare two versions and return a diff of changes.

        Args:
            base_version: The base version (typically older)
            compare_version: The version to compare against (typically newer)

        Returns:
            VersionDiff containing added, removed, and modified actions
        """
        base_manifest = RegistryVersionManifest.model_validate(base_version.manifest)
        compare_manifest = RegistryVersionManifest.model_validate(
            compare_version.manifest
        )

        base_actions = set(base_manifest.actions.keys())
        compare_actions = set(compare_manifest.actions.keys())

        # Find added and removed actions
        actions_added = sorted(compare_actions - base_actions)
        actions_removed = sorted(base_actions - compare_actions)

        # Find modified actions (actions present in both)
        common_actions = base_actions & compare_actions
        actions_modified: list[ActionChange] = []

        for action_name in sorted(common_actions):
            base_action = base_manifest.actions[action_name]
            compare_action = compare_manifest.actions[action_name]

            interface_changes: list[ActionInterfaceChange] = []
            description_changed = base_action.description != compare_action.description

            # Compare expects
            base_expects = base_action.interface["expects"]
            compare_expects = compare_action.interface["expects"]
            if base_expects != compare_expects:
                interface_changes.append(
                    ActionInterfaceChange(
                        field="expects",
                        change_type="modified",
                        old_value=base_expects,
                        new_value=compare_expects,
                    )
                )

            # Compare returns
            base_returns = base_action.interface["returns"]
            compare_returns = compare_action.interface["returns"]
            if base_returns != compare_returns:
                interface_changes.append(
                    ActionInterfaceChange(
                        field="returns",
                        change_type="modified",
                        old_value=base_returns,
                        new_value=compare_returns,
                    )
                )

            if interface_changes or description_changed:
                actions_modified.append(
                    ActionChange(
                        action_name=action_name,
                        change_type="modified",
                        interface_changes=interface_changes,
                        description_changed=description_changed,
                    )
                )

        total_changes = (
            len(actions_added) + len(actions_removed) + len(actions_modified)
        )

        return VersionDiff(
            base_version_id=base_version.id,
            base_version=base_version.version,
            compare_version_id=compare_version.id,
            compare_version=compare_version.version,
            actions_added=actions_added,
            actions_removed=actions_removed,
            actions_modified=actions_modified,
            total_changes=total_changes,
        )

    async def get_workflow_definitions_using_version(
        self,
        origin: str,
        version_string: str,
    ) -> Sequence[WorkflowDefinition]:
        """Find all published workflow definitions that reference this registry version.

        Args:
            origin: The repository origin (e.g., 'tracecat_registry' or 'git+ssh://...')
            version_string: The version string to search for

        Returns:
            List of WorkflowDefinition objects that reference this version
        """
        # Use JSONB containment to find definitions with this origin/version in registry_lock
        # registry_lock structure: {"origins": {"origin": "version_string"}, "actions": {...}}
        # Join with Workspace to filter by organization_id since WorkflowDefinition uses workspace_id
        stmt = (
            select(WorkflowDefinition)
            .join(Workspace, WorkflowDefinition.workspace_id == Workspace.id)
            .where(
                Workspace.organization_id == self.organization_id,
                WorkflowDefinition.registry_lock.op("@>")(
                    cast({"origins": {origin: version_string}}, JSONB)
                ),
            )
        )
        result = await self.session.execute(stmt)
        return result.scalars().all()


class PlatformRegistryVersionsService(BaseService):
    """Service for managing platform-owned registry versions.

    Note: role parameter is accepted but not used - platform services don't
    require org context. This is for protocol compatibility with VersionsServiceProtocol.
    """

    service_name: ClassVar[str] = "platform_registry_versions"

    def __init__(
        self,
        session: AsyncSession,
        role: Role | PlatformRole | None = None,  # noqa: ARG002 - unused but required for protocol
    ):
        super().__init__(session)

    async def list_versions(
        self,
        repository_id: UUID | None = None,
    ) -> Sequence[PlatformRegistryVersion]:
        """List all platform registry versions, optionally filtered by repository."""
        statement = select(PlatformRegistryVersion).order_by(
            PlatformRegistryVersion.created_at.desc()
        )
        if repository_id:
            statement = statement.where(
                PlatformRegistryVersion.repository_id == repository_id
            )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_version(self, version_id: UUID) -> PlatformRegistryVersion | None:
        """Get a platform registry version by ID."""
        statement = (
            select(PlatformRegistryVersion)
            .options(selectinload(PlatformRegistryVersion.index_entries))
            .where(PlatformRegistryVersion.id == version_id)
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def get_version_by_repo_and_version(
        self,
        repository_id: UUID,
        version: str,
    ) -> PlatformRegistryVersion | None:
        """Get a specific platform version of a repository."""
        statement = (
            select(PlatformRegistryVersion)
            .options(selectinload(PlatformRegistryVersion.index_entries))
            .where(
                PlatformRegistryVersion.repository_id == repository_id,
                PlatformRegistryVersion.version == version,
            )
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

    async def create_version(
        self,
        params: RegistryVersionCreate,
        *,
        commit: bool = True,
    ) -> PlatformRegistryVersion:
        """Create a new platform registry version with its manifest."""
        version = PlatformRegistryVersion(
            repository_id=params.repository_id,
            version=params.version,
            commit_sha=params.commit_sha,
            manifest=to_jsonable_python(params.manifest),
            tarball_uri=params.tarball_uri,
        )
        self.session.add(version)
        if commit:
            await self.session.commit()
            await self.session.refresh(version)
        else:
            await self.session.flush()
        return version

    async def delete_version(
        self,
        version: PlatformRegistryVersion,
        *,
        commit: bool = True,
    ) -> None:
        """Delete a platform registry version."""
        await self.session.delete(version)
        if commit:
            await self.session.commit()
        else:
            await self.session.flush()

    async def populate_index_from_manifest(
        self,
        version: PlatformRegistryVersion,
        *,
        commit: bool = True,
    ) -> list[PlatformRegistryIndex]:
        """Populate platform registry index entries from a version's manifest."""
        manifest = RegistryVersionManifest.model_validate(version.manifest)
        index_entries: list[PlatformRegistryIndex] = []

        for _action_name, action_def in manifest.actions.items():
            index_entry = PlatformRegistryIndex(
                registry_version_id=version.id,
                namespace=action_def.namespace,
                name=action_def.name,
                action_type=action_def.action_type,
                description=action_def.description,
                default_title=action_def.default_title,
                display_group=action_def.display_group,
                doc_url=action_def.doc_url,
                author=action_def.author,
                deprecated=action_def.deprecated,
                secrets=to_jsonable_python(action_def.secrets)
                if action_def.secrets
                else None,
                interface=dict(action_def.interface),
                options=action_def.options.model_dump() if action_def.options else {},
            )
            self.session.add(index_entry)
            index_entries.append(index_entry)

        if commit:
            await self.session.commit()
        else:
            await self.session.flush()

        self.logger.info(
            "Populated platform registry index",
            version_id=str(version.id),
            num_entries=len(index_entries),
        )
        return index_entries

    async def get_index_entries(
        self,
        version_id: UUID,
    ) -> Sequence[PlatformRegistryIndex]:
        """Get all platform index entries for a version."""
        statement = select(PlatformRegistryIndex).where(
            PlatformRegistryIndex.registry_version_id == version_id
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def create_index_entry(
        self,
        params: RegistryIndexCreate,
        *,
        commit: bool = True,
    ) -> PlatformRegistryIndex:
        """Create a single platform registry index entry."""
        index_entry = PlatformRegistryIndex(
            registry_version_id=params.registry_version_id,
            namespace=params.namespace,
            name=params.name,
            action_type=params.action_type,
            description=params.description,
            default_title=params.default_title,
            display_group=params.display_group,
            doc_url=params.doc_url,
            author=params.author,
            deprecated=params.deprecated,
            secrets=params.secrets,
            interface=params.interface,
            options=params.options,
        )
        self.session.add(index_entry)
        if commit:
            await self.session.commit()
            await self.session.refresh(index_entry)
        else:
            await self.session.flush()
        return index_entry
