"""Case tag resource adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from pydantic import BaseModel
from sqlalchemy import select

from tracecat.db.models import CaseTag
from tracecat.service import BaseWorkspaceService
from tracecat.workspace_sync.adapters.base import (
    ImportedResource,
    ProjectedResource,
    ResourceProjection,
    SingleYamlAdapter,
    unique_source_id,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import CASE_TAG_ROOT, CaseTagResourceSpec


class CaseTagAdapter(SingleYamlAdapter):
    resource_type = SyncResourceType.CASE_TAG
    spec_attr = "case_tags"
    model = CaseTagResourceSpec
    root = CASE_TAG_ROOT

    async def project(self, ctx: BaseWorkspaceService) -> ResourceProjection:
        stmt = (
            select(CaseTag)
            .where(CaseTag.workspace_id == ctx.workspace_id)
            .order_by(CaseTag.ref.asc(), CaseTag.id.asc())
        )
        tags = list((await ctx.session.execute(stmt)).scalars().all())
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        reserved: set[str] = set()
        for tag in tags:
            source_id = unique_source_id(tag.ref, reserved=reserved)
            reserved.add(source_id)
            specs[source_id] = CaseTagResourceSpec(
                id=source_id,
                name=tag.name,
                color=tag.color,
            )
            resources.append(self.projected_resource(source_id, tag.id))
        return ResourceProjection(specs=specs, resources=resources)

    async def import_specs(
        self,
        ctx: BaseWorkspaceService,
        specs: Mapping[str, BaseModel],
    ) -> list[ImportedResource]:
        tags = cast(Mapping[str, CaseTagResourceSpec], specs)
        imported: list[ImportedResource] = []
        for source_id, spec in sorted(tags.items()):
            tag = await ctx.session.scalar(
                select(CaseTag).where(
                    CaseTag.workspace_id == ctx.workspace_id,
                    CaseTag.ref == source_id,
                )
            )
            if tag is None:
                tag = CaseTag(
                    workspace_id=ctx.workspace_id,
                    name=spec.name,
                    ref=source_id,
                    color=spec.color,
                )
            else:
                tag.name = spec.name
                tag.color = spec.color
            ctx.session.add(tag)
            await ctx.session.flush()
            imported.append(self.imported_resource(source_id, tag.id))
        return imported
