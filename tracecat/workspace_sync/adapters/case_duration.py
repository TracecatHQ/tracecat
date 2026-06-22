"""Case duration definition resource adapter."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import cast

from pydantic import BaseModel
from sqlalchemy import select

from tracecat.db.models import CaseDurationDefinition
from tracecat.service import BaseWorkspaceService
from tracecat.workspace_sync.adapters.base import (
    ImportedResource,
    ProjectedResource,
    ResourceProjection,
    SingleYamlAdapter,
    unique_source_id,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    CASE_DURATION_ROOT,
    CaseDurationAnchorSpec,
    CaseDurationResourceSpec,
)


class CaseDurationAdapter(SingleYamlAdapter):
    """Sync adapter for case duration definitions and their start/end anchors."""

    resource_type = SyncResourceType.CASE_DURATION
    spec_attr = "case_durations"
    model = CaseDurationResourceSpec
    root = CASE_DURATION_ROOT

    async def project(self, ctx: BaseWorkspaceService) -> ResourceProjection:
        """Project case duration definitions, with their anchors, into specs."""
        stmt = (
            select(CaseDurationDefinition)
            .where(CaseDurationDefinition.workspace_id == ctx.workspace_id)
            .order_by(
                CaseDurationDefinition.name.asc(), CaseDurationDefinition.id.asc()
            )
        )
        durations = list((await ctx.session.execute(stmt)).scalars().all())
        source_ids_by_local_id = await self.source_ids_by_local_id(ctx)
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        reserved: set[str] = set(source_ids_by_local_id.values())
        for duration in durations:
            source_id = source_ids_by_local_id.get(duration.id)
            if source_id is None:
                source_id = unique_source_id(duration.name, reserved=reserved)
            reserved.add(source_id)
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
        ctx: BaseWorkspaceService,
        specs: Mapping[str, BaseModel],
    ) -> list[ImportedResource]:
        """Reconcile duration specs, creating or updating each definition."""
        durations = cast(Mapping[str, CaseDurationResourceSpec], specs)
        imported: list[ImportedResource] = []
        for source_id, spec in sorted(durations.items()):
            duration = await self._duration_for_import(
                ctx,
                source_id=source_id,
                spec=spec,
            )
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
                duration = CaseDurationDefinition(
                    workspace_id=ctx.workspace_id,
                    **attrs,
                )
            else:
                for key, value in attrs.items():
                    setattr(duration, key, value)
            ctx.session.add(duration)
            await ctx.session.flush()
            imported.append(self.imported_resource(source_id, duration.id))
        return imported

    async def _duration_for_import(
        self,
        ctx: BaseWorkspaceService,
        *,
        source_id: str,
        spec: CaseDurationResourceSpec,
    ) -> CaseDurationDefinition | None:
        """Resolve the existing duration a spec maps to, by source id then name.

        When matched by source id, verifies ``spec``'s name is still free before
        reusing the row. Returns ``None`` when no existing duration matches.
        """
        duration = await self._duration_by_source_id(ctx, source_id=source_id)
        if duration is not None:
            await self._ensure_name_available(
                ctx,
                source_id=source_id,
                name=spec.name,
                duration_id=duration.id,
            )
            return duration

        return await ctx.session.scalar(
            select(CaseDurationDefinition).where(
                CaseDurationDefinition.workspace_id == ctx.workspace_id,
                CaseDurationDefinition.name == spec.name,
            )
        )

    async def _duration_by_source_id(
        self,
        ctx: BaseWorkspaceService,
        *,
        source_id: str,
    ) -> CaseDurationDefinition | None:
        """Load the duration mapped to ``source_id`` via the sync mapping, if any."""
        local_id = await self.local_id_for_source_id(ctx, source_id)
        if local_id is None:
            return None

        return await ctx.session.scalar(
            select(CaseDurationDefinition).where(
                CaseDurationDefinition.workspace_id == ctx.workspace_id,
                CaseDurationDefinition.id == local_id,
            )
        )

    async def _ensure_name_available(
        self,
        ctx: BaseWorkspaceService,
        *,
        source_id: str,
        name: str,
        duration_id: uuid.UUID,
    ) -> None:
        """Raise if another duration already owns ``name`` in this workspace."""
        conflict_id = await ctx.session.scalar(
            select(CaseDurationDefinition.id).where(
                CaseDurationDefinition.workspace_id == ctx.workspace_id,
                CaseDurationDefinition.name == name,
                CaseDurationDefinition.id != duration_id,
            )
        )
        if conflict_id is None:
            return

        raise ValueError(
            f"Case duration sync source id {source_id!r} cannot use name {name!r} "
            "because another duration already uses that name."
        )
