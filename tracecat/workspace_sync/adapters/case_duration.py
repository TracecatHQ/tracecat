"""Case duration definition resource adapter."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping

from pydantic import BaseModel
from sqlalchemy import select

from tracecat.db.models import CaseDurationDefinition
from tracecat.service import BaseWorkspaceService
from tracecat.tiers.enums import Entitlement
from tracecat.workspace_sync.adapters.base import (
    FlatManifestAdapter,
    ImportedResource,
    ProjectedResource,
    ResourceProjection,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    CASE_DURATION_ROOT,
    CaseDurationAnchorSpec,
    CaseDurationResourceSpec,
    WorkspaceSpec,
)


class CaseDurationAdapter(FlatManifestAdapter):
    """Sync adapter for case duration definitions and their start/end anchors."""

    resource_type = SyncResourceType.CASE_DURATION
    spec_attr = "case_durations"
    model = CaseDurationResourceSpec
    read_scope = "case:read"
    update_scope = "case:update"
    required_entitlements = frozenset({Entitlement.CASE_ADDONS})
    root = CASE_DURATION_ROOT

    async def project(
        self, workspace_service: BaseWorkspaceService
    ) -> ResourceProjection:
        """Project case duration definitions, with their anchors, into specs."""
        stmt = (
            select(CaseDurationDefinition)
            .where(
                CaseDurationDefinition.workspace_id == workspace_service.workspace_id
            )
            .order_by(
                CaseDurationDefinition.name.asc(), CaseDurationDefinition.id.asc()
            )
        )
        durations = list(
            (await workspace_service.session.execute(stmt)).scalars().all()
        )
        assigner = await self.source_id_assigner(workspace_service)
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        for duration in durations:
            source_id = assigner.assign(duration.id, duration.name)
            # Each duration carries both its start and end anchors as nested specs.
            specs[source_id] = CaseDurationResourceSpec(
                id=source_id,
                name=duration.name,
                description=duration.description,
                start=CaseDurationAnchorSpec(
                    event=duration.start_event_type,
                    selection=duration.start_selection,
                    timestamp_path=duration.start_timestamp_path,
                    field_filters=duration.start_field_filters,
                ),
                end=CaseDurationAnchorSpec(
                    event=duration.end_event_type,
                    selection=duration.end_selection,
                    timestamp_path=duration.end_timestamp_path,
                    field_filters=duration.end_field_filters,
                ),
            )
            resources.append(self.projected_resource(source_id, duration.id))
        return ResourceProjection(specs=specs, resources=resources)

    async def import_specs(
        self,
        workspace_service: BaseWorkspaceService,
        workspace_spec: WorkspaceSpec,
    ) -> list[ImportedResource]:
        """Reconcile duration specs, creating or updating each definition."""
        durations = workspace_spec.case_durations
        target_names_by_source_id = {
            source_id: spec.name for source_id, spec in durations.items()
        }
        duplicate_names = sorted(_duplicates(target_names_by_source_id.values()))
        if duplicate_names:
            raise ValueError(
                "Case duration sync specs must have unique names: "
                + ", ".join(repr(name) for name in duplicate_names)
            )
        mapped_durations = {
            source_id: duration
            for source_id in sorted(durations)
            if (
                duration := await self._duration_by_source_id(
                    workspace_service,
                    source_id=source_id,
                )
            )
            is not None
        }
        source_ids_by_duration_id = {
            duration.id: source_id for source_id, duration in mapped_durations.items()
        }
        for source_id, duration in mapped_durations.items():
            await self._ensure_name_available(
                workspace_service,
                source_id=source_id,
                name=target_names_by_source_id[source_id],
                duration_id=duration.id,
                source_ids_by_duration_id=source_ids_by_duration_id,
                target_names_by_source_id=target_names_by_source_id,
            )
        await self._release_changing_mapped_durations(
            workspace_service,
            durations_by_source_id=mapped_durations,
            target_names_by_source_id=target_names_by_source_id,
        )
        imported: list[ImportedResource] = []
        # Sort for deterministic ordering so import results are reproducible.
        for source_id, spec in sorted(durations.items()):
            # Find the row this spec maps to, or None if it should be created.
            duration = await self._duration_for_import(
                workspace_service,
                source_id=source_id,
                spec=spec,
                mapped_duration=mapped_durations.get(source_id),
                source_ids_by_duration_id=source_ids_by_duration_id,
                target_names_by_source_id=target_names_by_source_id,
            )
            # Build the column values once; reused by both the create and update paths.
            attrs = {
                "name": spec.name,
                "description": spec.description,
                "start_event_type": spec.start.event,
                "start_selection": spec.start.selection,
                "start_timestamp_path": spec.start.timestamp_path,
                "start_field_filters": spec.start.field_filters,
                "end_event_type": spec.end.event,
                "end_selection": spec.end.selection,
                "end_timestamp_path": spec.end.timestamp_path,
                "end_field_filters": spec.end.field_filters,
            }
            if duration is None:
                # No existing match: construct a new definition in this workspace.
                duration = CaseDurationDefinition(
                    workspace_id=workspace_service.workspace_id,
                    **attrs,
                )
            else:
                # Existing match: overwrite each field in place to reconcile it.
                for key, value in attrs.items():
                    setattr(duration, key, value)
            workspace_service.session.add(duration)
            # Flush per duration so duration.id is populated before we record it.
            await workspace_service.session.flush()
            imported.append(self.imported_resource(source_id, duration.id))
        return imported

    async def _duration_for_import(
        self,
        workspace_service: BaseWorkspaceService,
        *,
        source_id: str,
        spec: CaseDurationResourceSpec,
        mapped_duration: CaseDurationDefinition | None = None,
        source_ids_by_duration_id: Mapping[uuid.UUID, str] | None = None,
        target_names_by_source_id: Mapping[str, str] | None = None,
    ) -> CaseDurationDefinition | None:
        """Resolve the existing duration a spec maps to, by source id then name.

        When matched by source id, verifies ``spec``'s name is still free before
        reusing the row. Returns ``None`` when no existing duration matches.
        """
        # Prefer the sync-mapping match: it identifies the row even if renamed.
        duration = mapped_duration or await self._duration_by_source_id(
            workspace_service,
            source_id=source_id,
        )
        if duration is not None:
            # Guard against a name collision with a different row before reusing it.
            await self._ensure_name_available(
                workspace_service,
                source_id=source_id,
                name=spec.name,
                duration_id=duration.id,
                source_ids_by_duration_id=source_ids_by_duration_id,
                target_names_by_source_id=target_names_by_source_id,
            )
            return duration

        # No mapping yet: fall back to adopting any existing row with the same name.
        return await workspace_service.session.scalar(
            select(CaseDurationDefinition).where(
                CaseDurationDefinition.workspace_id == workspace_service.workspace_id,
                CaseDurationDefinition.name == spec.name,
            )
        )

    async def _duration_by_source_id(
        self,
        workspace_service: BaseWorkspaceService,
        *,
        source_id: str,
    ) -> CaseDurationDefinition | None:
        """Load the duration mapped to ``source_id`` via the sync mapping, if any."""
        return await self._row_by_source_id(
            workspace_service, source_id=source_id, model=CaseDurationDefinition
        )

    async def _ensure_name_available(
        self,
        workspace_service: BaseWorkspaceService,
        *,
        source_id: str,
        name: str,
        duration_id: uuid.UUID,
        source_ids_by_duration_id: Mapping[uuid.UUID, str] | None = None,
        target_names_by_source_id: Mapping[str, str] | None = None,
    ) -> None:
        """Raise if another duration already owns ``name`` in this workspace."""
        # Look for any other row holding this name (excluding the one we're updating).
        conflict_id = await workspace_service.session.scalar(
            select(CaseDurationDefinition.id).where(
                CaseDurationDefinition.workspace_id == workspace_service.workspace_id,
                CaseDurationDefinition.name == name,
                CaseDurationDefinition.id != duration_id,
            )
        )
        if conflict_id is None:
            # Name is free for this row; nothing to enforce.
            return

        if source_ids_by_duration_id and target_names_by_source_id:
            conflict_source_id = source_ids_by_duration_id.get(conflict_id)
            if (
                conflict_source_id is not None
                and target_names_by_source_id[conflict_source_id] != name
            ):
                return

        raise ValueError(
            f"Case duration sync source id {source_id!r} cannot use name {name!r} "
            "because another duration already uses that name."
        )

    async def _release_changing_mapped_durations(
        self,
        workspace_service: BaseWorkspaceService,
        *,
        durations_by_source_id: Mapping[str, CaseDurationDefinition],
        target_names_by_source_id: Mapping[str, str],
    ) -> None:
        """Park mapped durations whose names are changing under temporary names."""
        if not durations_by_source_id:
            return
        reserved_names = set(
            (
                await workspace_service.session.scalars(
                    select(CaseDurationDefinition.name).where(
                        CaseDurationDefinition.workspace_id
                        == workspace_service.workspace_id
                    )
                )
            ).all()
        ) | set(target_names_by_source_id.values())
        changed = False
        for source_id, duration in durations_by_source_id.items():
            if duration.name == target_names_by_source_id[source_id]:
                continue
            duration.name = _unique_temporary_duration_name(duration, reserved_names)
            workspace_service.session.add(duration)
            changed = True
        if changed:
            await workspace_service.session.flush()


def _duplicates(values: Iterable[str]) -> set[str]:
    """Return duplicate string values from an iterable."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        seen.add(value)
    return duplicates


def _unique_temporary_duration_name(
    duration: CaseDurationDefinition,
    reserved_names: set[str],
) -> str:
    """Return a temporary duration name that does not collide with reserved names."""
    base = f"__tracecat_sync_tmp_{duration.id.hex}"
    candidate = base
    suffix = 1
    while candidate in reserved_names:
        suffix += 1
        candidate = f"{base}_{suffix}"
    reserved_names.add(candidate)
    return candidate
