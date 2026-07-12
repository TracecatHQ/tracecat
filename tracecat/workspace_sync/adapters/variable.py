"""Workspace variable resource adapter."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy import select

from tracecat.db.models import WorkspaceVariable
from tracecat.workspace_sync.adapters.base import (
    EnvironmentScopedManifestAdapter,
    ImportedResource,
    NameSwapPlan,
    ProjectedResource,
    ResourceDependencyRefs,
    ResourceProjection,
    SyncMappingService,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    VARIABLE_ROOT,
    VariableResourceSpec,
    WorkspaceSpec,
)


class VariableAdapter(EnvironmentScopedManifestAdapter):
    """Adapter for environment-scoped workspace variables.

    Each variable lives at ``<root>/<environment>/<name>.yml``. Only metadata
    (key names, description, tags) is synced; variable values are not tracked in
    Git.
    """

    resource_type = SyncResourceType.VARIABLE
    """The sync resource type this adapter handles."""
    read_scope = "variable:read"
    create_scope = "variable:create"
    update_scope = "variable:update"
    spec_attr = "variables"
    """Attribute on ``WorkspaceSpec``/``WorkspaceManifestResources`` for variables."""
    model = VariableResourceSpec
    """Pydantic spec model variables serialize to and from."""
    root = VARIABLE_ROOT
    """Top-level repository directory for variables."""
    import_identity_attrs = ("environment", "name")
    import_identity_noun = "target"

    async def project(
        self, workspace_service: SyncMappingService
    ) -> ResourceProjection:
        """Project workspace variables into specs, recording key names but no values."""
        stmt = self._projection_stmt(workspace_service)
        variables = list(
            (await workspace_service.session.execute(stmt)).scalars().all()
        )
        return await self._projection_from_variables(workspace_service, variables)

    async def project_dependency_refs(
        self,
        workspace_service: SyncMappingService,
        refs: ResourceDependencyRefs,
    ) -> ResourceProjection:
        """Project variables selected directly or referenced by name."""
        # "Select all" short-circuits straight to a full projection.
        if refs.select_all:
            return await self.project(workspace_service)
        # No selectors of any kind means nothing to project.
        if not refs.local_ids and not refs.source_ids and not refs.names:
            return ResourceProjection(specs={}, resources=[])

        local_ids = set(refs.local_ids)
        # Resolve any source ids to their mapped local ids and fold them in.
        if refs.source_ids:
            local_ids.update(
                (
                    await self.local_ids_by_source_id(
                        workspace_service,
                        refs.source_ids,
                    )
                ).values()
            )
        # Build an OR of predicates so a variable matched by any selector
        # (local id or bare name) is included.
        predicates: list[sa.ColumnElement[bool]] = []
        if local_ids:
            predicates.append(WorkspaceVariable.id.in_(local_ids))
        if refs.names:
            predicates.append(WorkspaceVariable.name.in_(refs.names))
        # Selectors were present but resolved to nothing (e.g. unmapped source
        # ids), so there is no row to project.
        if not predicates:
            return ResourceProjection(specs={}, resources=[])
        stmt = self._projection_stmt(workspace_service).where(sa.or_(*predicates))
        variables = list(
            (await workspace_service.session.execute(stmt)).scalars().all()
        )
        return await self._projection_from_variables(workspace_service, variables)

    def _projection_stmt(
        self, workspace_service: SyncMappingService
    ) -> sa.Select[tuple[WorkspaceVariable]]:
        """Build the base variable projection query."""
        return (
            select(WorkspaceVariable)
            .where(WorkspaceVariable.workspace_id == workspace_service.workspace_id)
            .order_by(
                WorkspaceVariable.environment.asc(),
                WorkspaceVariable.name.asc(),
                WorkspaceVariable.id.asc(),
            )
        )

    async def _projection_from_variables(
        self,
        workspace_service: SyncMappingService,
        variables: list[WorkspaceVariable],
    ) -> ResourceProjection:
        """Build sync specs from variable rows."""
        assigner = await self.source_id_assigner(workspace_service)
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        for variable in variables:
            source_id = assigner.assign_environment(
                variable.id, variable.environment, variable.name
            )
            with self.projection_error_context(
                source_id=source_id,
                display_name=variable.name,
                local_id=variable.id,
            ):
                # Only key NAMES and tag NAMES are projected (sorted for stable
                # diffs); the actual values are deliberately never written to Git.
                specs[source_id] = VariableResourceSpec(
                    id=source_id,
                    name=variable.name,
                    environment=variable.environment,
                    keys=sorted((variable.values or {}).keys()),
                    description=variable.description,
                    tags=sorted((variable.tags or {}).keys()),
                )
                resources.append(self.projected_resource(source_id, variable.id))
        return ResourceProjection(specs=specs, resources=resources)

    async def import_specs(
        self,
        workspace_service: SyncMappingService,
        workspace_spec: WorkspaceSpec,
    ) -> list[ImportedResource]:
        """Reconcile variable specs into the database, preserving existing values.

        Validates that target ``environment``/``name`` pairs are unique, frees up
        any conflicting names by temporarily renaming variables whose identity is
        changing, then creates or updates each variable. Existing values are kept
        for declared keys; values for keys no longer declared are dropped.
        """
        variables = workspace_spec.variables
        imported: list[ImportedResource] = []
        # Variables are unique per (environment, name): reject duplicate targets,
        # then park identity-changing rows under temporary names so an in-batch
        # swap doesn't trip the unique constraint mid-flush.
        swap = await self.plan_name_swap(
            workspace_service,
            targets={source_id: spec.name for source_id, spec in variables.items()},
            target_scopes={
                source_id: spec.environment for source_id, spec in variables.items()
            },
            model=WorkspaceVariable,
            name_column=WorkspaceVariable.name,
            scope_column=WorkspaceVariable.environment,
            noun="name",
            kind_label="Variable",
            owner_label="variable",
        )
        # Upsert each variable in sorted order so name reuses land predictably
        # now that conflicting names have been parked.
        for source_id, spec in sorted(variables.items()):
            variable = await self._variable_for_import(
                workspace_service,
                source_id=source_id,
                spec=spec,
                swap=swap,
            )
            # Preserve existing values for still-declared keys; values are never
            # carried in from Git, only the key names are.
            values = _values_from_spec(
                spec, existing=variable.values if variable else {}
            )
            if variable is None:
                # No existing row: create one. Tags store key names mapped to
                # empty strings, mirroring how values are key-only.
                variable = WorkspaceVariable(
                    workspace_id=workspace_service.workspace_id,
                    name=spec.name,
                    environment=spec.environment,
                    values=values,
                    description=spec.description,
                    tags=dict.fromkeys(spec.tags, "") if spec.tags else None,
                )
            else:
                # Existing row: overwrite synced metadata in place.
                variable.name = spec.name
                variable.environment = spec.environment
                variable.values = values
                variable.description = spec.description
                variable.tags = dict.fromkeys(spec.tags, "") if spec.tags else None
            workspace_service.session.add(variable)
            # Flush per-variable so the next iteration's name claim sees this
            # row's final (environment, name) and the temp parking is resolved.
            await workspace_service.session.flush()
            imported.append(self.imported_resource(source_id, variable.id))
        return imported

    async def _variable_for_import(
        self,
        workspace_service: SyncMappingService,
        *,
        source_id: str,
        spec: VariableResourceSpec,
        swap: NameSwapPlan[WorkspaceVariable],
    ) -> WorkspaceVariable | None:
        """Resolve the variable a spec should update, by mapping then by name/environment.

        Prefers the already-mapped variable, then falls back to one matching the
        spec's ``name`` and ``environment``; returns ``None`` when neither exists.
        """
        # Prefer the variable already mapped to this source id (resolved by the
        # swap plan), otherwise resolve it by source id.
        variable = swap.mapped_by_source_id.get(source_id) or (
            await self._variable_by_source_id(
                workspace_service,
                source_id=source_id,
            )
        )
        if variable is not None:
            return variable

        # Fall back to an unmapped row already sitting at the target identity, so
        # we adopt it instead of creating a duplicate that violates uniqueness.
        return await workspace_service.session.scalar(
            select(WorkspaceVariable).where(
                WorkspaceVariable.workspace_id == workspace_service.workspace_id,
                WorkspaceVariable.name == spec.name,
                WorkspaceVariable.environment == spec.environment,
            )
        )

    async def _variable_by_source_id(
        self,
        workspace_service: SyncMappingService,
        *,
        source_id: str,
    ) -> WorkspaceVariable | None:
        """Load the mapped :class:`WorkspaceVariable` for ``source_id``, if any."""
        return await self._row_by_source_id(
            workspace_service, source_id=source_id, model=WorkspaceVariable
        )


def _values_from_spec(
    spec: VariableResourceSpec,
    *,
    existing: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a variable's stored values from its spec and current values.

    Declared ``keys`` are seeded from existing values (defaulting to ``""``). A
    spec with no keys leaves the existing values untouched.
    """
    existing_values = existing or {}
    # No declared keys means the spec doesn't track value structure, so leave
    # the current values untouched (copied defensively).
    if spec.keys is None:
        return dict(existing_values)
    # Seed each declared key from its existing value, defaulting to "" for keys
    # not yet present; keys dropped from the spec fall away here.
    return {key: existing_values.get(key, "") for key in spec.keys}
