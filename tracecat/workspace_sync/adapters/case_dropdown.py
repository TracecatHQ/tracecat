"""Case dropdown resource adapter."""

from __future__ import annotations

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracecat.db.models import CaseDropdownDefinition, CaseDropdownOption
from tracecat.tiers.enums import Entitlement
from tracecat.workspace_sync.adapters.base import (
    FlatManifestAdapter,
    ImportedResource,
    NameSwapPlan,
    ProjectedResource,
    ResourceProjection,
    SyncMappingService,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    CASE_DROPDOWN_ROOT,
    CaseDropdownResourceSpec,
    WorkspaceSpec,
)


class CaseDropdownAdapter(FlatManifestAdapter):
    """Sync adapter for case dropdown definitions and their options."""

    resource_type = SyncResourceType.CASE_DROPDOWN
    spec_attr = "case_dropdowns"
    model = CaseDropdownResourceSpec
    read_scope = "case:read"
    create_scope = "case:create"
    update_scope = "case:update"
    required_entitlements = frozenset({Entitlement.CASE_ADDONS})
    root = CASE_DROPDOWN_ROOT

    async def project(
        self, workspace_service: SyncMappingService
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
            with self.projection_error_context(
                source_id=source_id,
                display_name=dropdown.name,
                local_id=dropdown.id,
            ):
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
        workspace_service: SyncMappingService,
        workspace_spec: WorkspaceSpec,
    ) -> list[ImportedResource]:
        """Reconcile dropdown specs, syncing each definition's options in place."""
        dropdowns = workspace_spec.case_dropdowns
        # A dropdown's ref is its source id, so a pull that swaps two refs would
        # transiently collide on the (ref, workspace_id) unique constraint. Park
        # mapped definitions whose ref is changing under temporary refs first.
        swap = await self.plan_name_swap(
            workspace_service,
            targets={source_id: source_id for source_id in dropdowns},
            model=CaseDropdownDefinition,
            name_column=CaseDropdownDefinition.ref,
            noun="ref",
            kind_label="Case dropdown",
            owner_label="dropdown",
            options=(selectinload(CaseDropdownDefinition.options),),
        )
        imported: list[ImportedResource] = []
        for source_id, spec in sorted(dropdowns.items()):
            # Match the existing definition by mapping first, then by ref.
            dropdown = await self._dropdown_for_import(
                workspace_service,
                source_id=source_id,
                swap=swap,
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

    async def _dropdown_for_import(
        self,
        workspace_service: SyncMappingService,
        *,
        source_id: str,
        swap: NameSwapPlan[CaseDropdownDefinition],
    ) -> CaseDropdownDefinition | None:
        """Resolve the existing dropdown a spec maps to, by source id then ref.

        A dropdown's target ref is its source id, so a mapped row is reused only
        after confirming that ref is still free (the swap plan tolerates another
        mapped row that is itself vacating the ref).

        Args:
            workspace_service: Workspace-scoped sync service for this import.
            source_id: Git-owned source id, also the dropdown's target ref.
            swap: Name-swap plan holding the mapped rows and availability check.

        Returns:
            The existing dropdown to reconcile in place, or `None` when a new
            definition must be created.
        """
        # Prefer the sync-mapping match: it identifies the row even if its ref
        # diverged from the source id (e.g. a local rename).
        dropdown = swap.mapped_by_source_id.get(source_id) or (
            await self._dropdown_by_source_id(
                workspace_service,
                source_id=source_id,
            )
        )
        if dropdown is not None:
            await swap.ensure_available(
                workspace_service,
                source_id=source_id,
                name=source_id,
                row_id=dropdown.id,
            )
            return dropdown

        # No mapping yet: adopt any existing dropdown already at this ref.
        return await workspace_service.session.scalar(
            select(CaseDropdownDefinition)
            .where(
                CaseDropdownDefinition.workspace_id == workspace_service.workspace_id,
                CaseDropdownDefinition.ref == source_id,
            )
            .options(selectinload(CaseDropdownDefinition.options))
        )

    async def _dropdown_by_source_id(
        self,
        workspace_service: SyncMappingService,
        *,
        source_id: str,
    ) -> CaseDropdownDefinition | None:
        """Load the dropdown mapped to `source_id` via the sync mapping.

        Args:
            workspace_service: Workspace-scoped sync service for this import.
            source_id: Git-owned source id to resolve through the sync mapping.

        Returns:
            The mapped dropdown with its options eager-loaded, or `None` when
            `source_id` is not mapped.
        """
        return await self._row_by_source_id(
            workspace_service,
            source_id=source_id,
            model=CaseDropdownDefinition,
            options=(selectinload(CaseDropdownDefinition.options),),
        )
