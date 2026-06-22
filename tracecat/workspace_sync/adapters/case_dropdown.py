"""Case dropdown resource adapter."""

from __future__ import annotations

from collections.abc import Mapping
from typing import cast

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracecat.db.models import CaseDropdownDefinition, CaseDropdownOption
from tracecat.service import BaseWorkspaceService
from tracecat.workspace_sync.adapters.base import (
    ImportedResource,
    ProjectedResource,
    ResourceProjection,
    SingleYamlAdapter,
    unique_source_id,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import CASE_DROPDOWN_ROOT, CaseDropdownResourceSpec


class CaseDropdownAdapter(SingleYamlAdapter):
    resource_type = SyncResourceType.CASE_DROPDOWN
    spec_attr = "case_dropdowns"
    model = CaseDropdownResourceSpec
    root = CASE_DROPDOWN_ROOT

    async def project(self, ctx: BaseWorkspaceService) -> ResourceProjection:
        stmt = (
            select(CaseDropdownDefinition)
            .where(CaseDropdownDefinition.workspace_id == ctx.workspace_id)
            .options(selectinload(CaseDropdownDefinition.options))
            .order_by(CaseDropdownDefinition.ref.asc(), CaseDropdownDefinition.id.asc())
        )
        dropdowns = list((await ctx.session.execute(stmt)).scalars().all())
        source_ids_by_local_id = await self.source_ids_by_local_id(ctx)
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        reserved: set[str] = set(source_ids_by_local_id.values())
        for dropdown in dropdowns:
            source_id = source_ids_by_local_id.get(dropdown.id)
            if source_id is None:
                source_id = unique_source_id(dropdown.ref, reserved=reserved)
            reserved.add(source_id)
            specs[source_id] = CaseDropdownResourceSpec.model_validate(
                {
                    "id": source_id,
                    "name": dropdown.name,
                    "options": [
                        {
                            key: value
                            for key, value in {
                                "ref": option.ref,
                                "label": option.label,
                                "position": option.position,
                                "icon_name": option.icon_name,
                                "color": option.color,
                            }.items()
                            if value is not None
                        }
                        for option in sorted(
                            dropdown.options,
                            key=lambda item: (item.position, item.ref),
                        )
                    ],
                    "is_ordered": dropdown.is_ordered,
                    "icon_name": dropdown.icon_name,
                    "position": dropdown.position,
                    "required_on_closure": dropdown.required_on_closure,
                }
            )
            resources.append(self.projected_resource(source_id, dropdown.id))
        return ResourceProjection(specs=specs, resources=resources)

    async def import_specs(
        self,
        ctx: BaseWorkspaceService,
        specs: Mapping[str, BaseModel],
    ) -> list[ImportedResource]:
        dropdowns = cast(Mapping[str, CaseDropdownResourceSpec], specs)
        imported: list[ImportedResource] = []
        source_ids = set(dropdowns)
        mapped_local_ids_by_source_id = await self.local_ids_by_source_id(
            ctx,
            source_ids,
        )
        mapped_local_ids = set(mapped_local_ids_by_source_id.values())
        conditions = [CaseDropdownDefinition.ref.in_(source_ids)]
        if mapped_local_ids:
            conditions.append(CaseDropdownDefinition.id.in_(mapped_local_ids))
        existing_dropdowns = list(
            (
                await ctx.session.scalars(
                    select(CaseDropdownDefinition)
                    .where(
                        CaseDropdownDefinition.workspace_id == ctx.workspace_id,
                        sa.or_(*conditions),
                    )
                    .options(selectinload(CaseDropdownDefinition.options))
                )
            ).all()
        )
        dropdowns_by_id = {dropdown.id: dropdown for dropdown in existing_dropdowns}
        dropdowns_by_source_id = {
            source_id: dropdown
            for source_id, local_id in mapped_local_ids_by_source_id.items()
            if (dropdown := dropdowns_by_id.get(local_id)) is not None
        }
        dropdowns_by_ref = {dropdown.ref: dropdown for dropdown in existing_dropdowns}
        for source_id, spec in sorted(dropdowns.items()):
            dropdown = dropdowns_by_source_id.get(source_id) or dropdowns_by_ref.get(
                source_id
            )
            if dropdown is None:
                dropdown = CaseDropdownDefinition(
                    workspace_id=ctx.workspace_id,
                    name=spec.name,
                    ref=source_id,
                    is_ordered=spec.is_ordered,
                    icon_name=spec.icon_name,
                    position=spec.position,
                    required_on_closure=spec.required_on_closure,
                )
                ctx.session.add(dropdown)
                await ctx.session.flush()
                existing_options = {}
            else:
                dropdown.name = spec.name
                dropdown.ref = source_id
                dropdown.is_ordered = spec.is_ordered
                dropdown.icon_name = spec.icon_name
                dropdown.position = spec.position
                dropdown.required_on_closure = spec.required_on_closure
                existing_options = {option.ref: option for option in dropdown.options}

            desired_refs = set()
            for position, option_spec in enumerate(spec.options):
                ref = str(
                    option_spec.get("ref") or option_spec.get("label") or position
                )
                desired_refs.add(ref)
                option = existing_options.get(ref)
                if option is None:
                    option = CaseDropdownOption(
                        definition_id=dropdown.id,
                        ref=ref,
                        label=str(option_spec.get("label") or ref),
                    )
                option.label = str(option_spec.get("label") or ref)
                option.position = int(option_spec.get("position", position))
                option.icon_name = option_spec.get("icon_name")
                option.color = option_spec.get("color")
                ctx.session.add(option)
            for option in existing_options.values():
                if option.ref not in desired_refs:
                    await ctx.session.delete(option)
            ctx.session.add(dropdown)
            await ctx.session.flush()
            imported.append(self.imported_resource(source_id, dropdown.id))
        return imported
