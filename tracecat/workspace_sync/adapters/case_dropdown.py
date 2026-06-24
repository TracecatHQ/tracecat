"""Case dropdown resource adapter."""

from __future__ import annotations

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
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    CASE_DROPDOWN_ROOT,
    CaseDropdownResourceSpec,
    WorkspaceSpec,
)


class CaseDropdownAdapter(SingleYamlAdapter):
    """Sync adapter for case dropdown definitions and their options."""

    resource_type = SyncResourceType.CASE_DROPDOWN
    spec_attr = "case_dropdowns"
    model = CaseDropdownResourceSpec
    root = CASE_DROPDOWN_ROOT

    async def project(
        self, workspace_service: BaseWorkspaceService
    ) -> ResourceProjection:
        """Project case dropdown definitions, with their options, into specs."""
        # Eager-load options so each definition serializes in one pass; order by
        # ref/id for stable output.
        stmt = (
            select(CaseDropdownDefinition)
            .where(
                CaseDropdownDefinition.workspace_id == workspace_service.workspace_id
            )
            .options(selectinload(CaseDropdownDefinition.options))
            .order_by(CaseDropdownDefinition.ref.asc(), CaseDropdownDefinition.id.asc())
        )
        dropdowns = list(
            (await workspace_service.session.execute(stmt)).scalars().all()
        )
        assigner = await self.source_id_assigner(workspace_service)
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        for dropdown in dropdowns:
            source_id = assigner.assign(dropdown.id, dropdown.ref)
            specs[source_id] = CaseDropdownResourceSpec(
                id=source_id,
                name=dropdown.name,
                # Emit options sorted by (position, ref); drop None fields so the
                # YAML stays sparse and only carries values that were actually set.
                options=[
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
                is_ordered=dropdown.is_ordered,
                icon_name=dropdown.icon_name,
                position=dropdown.position,
                required_on_closure=dropdown.required_on_closure,
            )
            resources.append(self.projected_resource(source_id, dropdown.id))
        return ResourceProjection(specs=specs, resources=resources)

    async def import_specs(
        self,
        workspace_service: BaseWorkspaceService,
        workspace_spec: WorkspaceSpec,
    ) -> list[ImportedResource]:
        """Reconcile dropdown specs, syncing each definition's options in place."""
        dropdowns = workspace_spec.case_dropdowns
        imported: list[ImportedResource] = []
        source_ids = set(dropdowns)
        # Resolve which incoming source ids already map to local definitions.
        mapped_local_ids_by_source_id = await self.local_ids_by_source_id(
            workspace_service,
            source_ids,
        )
        mapped_local_ids = set(mapped_local_ids_by_source_id.values())
        # Load candidates by ref or by previously mapped local id.
        conditions = [CaseDropdownDefinition.ref.in_(source_ids)]
        if mapped_local_ids:
            conditions.append(CaseDropdownDefinition.id.in_(mapped_local_ids))
        existing_dropdowns = list(
            (
                await workspace_service.session.scalars(
                    select(CaseDropdownDefinition)
                    .where(
                        CaseDropdownDefinition.workspace_id
                        == workspace_service.workspace_id,
                        sa.or_(*conditions),
                    )
                    .options(selectinload(CaseDropdownDefinition.options))
                )
            ).all()
        )
        dropdowns_by_id = {dropdown.id: dropdown for dropdown in existing_dropdowns}
        # Mapped source id -> definition, derived from the sync mapping.
        dropdowns_by_source_id = {
            source_id: dropdown
            for source_id, local_id in mapped_local_ids_by_source_id.items()
            if (dropdown := dropdowns_by_id.get(local_id)) is not None
        }
        dropdowns_by_ref = {dropdown.ref: dropdown for dropdown in existing_dropdowns}
        for source_id, spec in sorted(dropdowns.items()):
            # Match the existing definition by mapping first, then by ref.
            dropdown = dropdowns_by_source_id.get(source_id) or dropdowns_by_ref.get(
                source_id
            )
            if dropdown is None:
                # New definition: insert and flush so options can reference its id.
                dropdown = CaseDropdownDefinition(
                    workspace_id=workspace_service.workspace_id,
                    name=spec.name,
                    ref=source_id,
                    is_ordered=spec.is_ordered,
                    icon_name=spec.icon_name,
                    position=spec.position,
                    required_on_closure=spec.required_on_closure,
                )
                workspace_service.session.add(dropdown)
                await workspace_service.session.flush()
                existing_options = {}
            else:
                # Existing definition: overwrite scalar fields and index its
                # current options by ref so we can update in place below.
                dropdown.name = spec.name
                dropdown.ref = source_id
                dropdown.is_ordered = spec.is_ordered
                dropdown.icon_name = spec.icon_name
                dropdown.position = spec.position
                dropdown.required_on_closure = spec.required_on_closure
                existing_options = {option.ref: option for option in dropdown.options}

            # Reconcile options against the spec, tracking which refs survive.
            desired_refs = set()
            for position, option_spec in enumerate(spec.options):
                # Ref key falls back to label, then list position, when omitted.
                ref = str(
                    option_spec.get("ref") or option_spec.get("label") or position
                )
                desired_refs.add(ref)
                option = existing_options.get(ref)
                if option is None:
                    # Ref not seen before: create a new option row for it.
                    option = CaseDropdownOption(
                        definition_id=dropdown.id,
                        ref=ref,
                        label=str(option_spec.get("label") or ref),
                    )
                # Upsert the option's display fields (also covers new options).
                option.label = str(option_spec.get("label") or ref)
                option.position = int(option_spec.get("position", position))
                option.icon_name = option_spec.get("icon_name")
                option.color = option_spec.get("color")
                workspace_service.session.add(option)
            # Drop options the spec no longer declares.
            for option in existing_options.values():
                if option.ref not in desired_refs:
                    await workspace_service.session.delete(option)
            workspace_service.session.add(dropdown)
            await workspace_service.session.flush()
            imported.append(self.imported_resource(source_id, dropdown.id))
        return imported
