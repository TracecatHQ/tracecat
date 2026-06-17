"""Case duration definition resource adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy import select

from tracecat.cases.durations.schemas import CaseDurationAnchorSelection
from tracecat.cases.enums import CaseEventType
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
from tracecat.workspace_sync.schemas import CASE_DURATION_ROOT, CaseDurationResourceSpec


class CaseDurationAdapter(SingleYamlAdapter):
    resource_type = SyncResourceType.CASE_DURATION
    spec_attr = "case_durations"
    model = CaseDurationResourceSpec
    root = CASE_DURATION_ROOT

    async def project(self, ctx: BaseWorkspaceService) -> ResourceProjection:
        stmt = (
            select(CaseDurationDefinition)
            .where(CaseDurationDefinition.workspace_id == ctx.workspace_id)
            .order_by(
                CaseDurationDefinition.name.asc(), CaseDurationDefinition.id.asc()
            )
        )
        durations = list((await ctx.session.execute(stmt)).scalars().all())
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        reserved: set[str] = set()
        for duration in durations:
            source_id = unique_source_id(duration.name, reserved=reserved)
            reserved.add(source_id)
            specs[source_id] = CaseDurationResourceSpec.model_validate(
                {
                    "id": source_id,
                    "name": duration.name,
                    "description": duration.description,
                    "start": {
                        "event": duration.start_event_type.value,
                        "selection": duration.start_selection.value,
                        "timestamp_path": duration.start_timestamp_path,
                        "field_filters": duration.start_field_filters,
                    },
                    "end": {
                        "event": duration.end_event_type.value,
                        "selection": duration.end_selection.value,
                        "timestamp_path": duration.end_timestamp_path,
                        "field_filters": duration.end_field_filters,
                    },
                }
            )
            resources.append(self.projected_resource(source_id, duration.id))
        return ResourceProjection(specs=specs, resources=resources)

    async def import_specs(
        self,
        ctx: BaseWorkspaceService,
        specs: Mapping[str, BaseModel],
    ) -> list[ImportedResource]:
        durations = cast(Mapping[str, CaseDurationResourceSpec], specs)
        imported: list[ImportedResource] = []
        for source_id, spec in sorted(durations.items()):
            duration = await ctx.session.scalar(
                select(CaseDurationDefinition).where(
                    CaseDurationDefinition.workspace_id == ctx.workspace_id,
                    CaseDurationDefinition.name == spec.name,
                )
            )
            start = _duration_anchor(spec, "start")
            end = _duration_anchor(spec, "end")
            attrs = {
                "name": spec.name,
                "description": getattr(spec, "description", None),
                "start_event_type": start["event_type"],
                "start_selection": start["selection"],
                "start_timestamp_path": start["timestamp_path"],
                "start_field_filters": start["field_filters"],
                "end_event_type": end["event_type"],
                "end_selection": end["selection"],
                "end_timestamp_path": end["timestamp_path"],
                "end_field_filters": end["field_filters"],
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


def _duration_anchor(spec: CaseDurationResourceSpec, key: str) -> dict[str, Any]:
    data = getattr(spec, key, None)
    if not isinstance(data, dict):
        data = {}
    return {
        "event_type": CaseEventType(data.get("event", "case_created")),
        "selection": CaseDurationAnchorSelection(data.get("selection", "first")),
        "timestamp_path": data.get("timestamp_path", "created_at"),
        "field_filters": data.get("field_filters", {}),
    }
