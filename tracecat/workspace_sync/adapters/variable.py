"""Workspace variable resource adapter."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy import select

from tracecat.db.models import WorkspaceVariable
from tracecat.service import BaseWorkspaceService
from tracecat.workspace_sync.adapters.base import (
    EnvironmentYamlAdapter,
    ImportedResource,
    ProjectedResource,
    ResourceDependencyRefs,
    ResourceProjection,
    unique_environment_source_id,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    VARIABLE_ROOT,
    VariableResourceSpec,
    WorkspaceSpec,
)


class VariableAdapter(EnvironmentYamlAdapter):
    """Adapter for environment-scoped workspace variables.

    Each variable lives at ``<root>/<environment>/<name>.yml``. Only metadata
    (key names, description, tags) is synced; variable values are not tracked in
    Git.
    """

    resource_type = SyncResourceType.VARIABLE
    """The sync resource type this adapter handles."""
    spec_attr = "variables"
    """Attribute on ``WorkspaceSpec``/``WorkspaceManifestResources`` for variables."""
    model = VariableResourceSpec
    """Pydantic spec model variables serialize to and from."""
    root = VARIABLE_ROOT
    """Top-level repository directory for variables."""

    async def project(
        self, workspace_service: BaseWorkspaceService
    ) -> ResourceProjection:
        """Project workspace variables into specs, recording key names but no values."""
        stmt = self._projection_stmt(workspace_service)
        variables = list(
            (await workspace_service.session.execute(stmt)).scalars().all()
        )
        return await self._projection_from_variables(workspace_service, variables)

    async def project_dependency_refs(
        self,
        workspace_service: BaseWorkspaceService,
        refs: ResourceDependencyRefs,
    ) -> ResourceProjection:
        """Project variables selected directly or referenced by name."""
        if refs.select_all:
            return await self.project(workspace_service)
        if (
            not refs.local_ids
            and not refs.source_ids
            and not refs.names
            and not refs.environment_names
        ):
            return ResourceProjection(specs={}, resources=[])

        local_ids = set(refs.local_ids)
        if refs.source_ids:
            local_ids.update(
                (
                    await self.local_ids_by_source_id(
                        workspace_service,
                        refs.source_ids,
                    )
                ).values()
            )
        predicates = []
        if local_ids:
            predicates.append(WorkspaceVariable.id.in_(local_ids))
        if refs.names:
            predicates.append(WorkspaceVariable.name.in_(refs.names))
        for environment, name in sorted(refs.environment_names):
            predicates.append(
                sa.and_(
                    WorkspaceVariable.environment == environment,
                    WorkspaceVariable.name == name,
                )
            )
        if not predicates:
            return ResourceProjection(specs={}, resources=[])
        stmt = self._projection_stmt(workspace_service).where(sa.or_(*predicates))
        variables = list(
            (await workspace_service.session.execute(stmt)).scalars().all()
        )
        return await self._projection_from_variables(workspace_service, variables)

    def _projection_stmt(self, workspace_service: BaseWorkspaceService) -> sa.Select:
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
        workspace_service: BaseWorkspaceService,
        variables: list[WorkspaceVariable],
    ) -> ResourceProjection:
        """Build sync specs from variable rows."""
        source_ids_by_local_id = await self.source_ids_by_local_id(workspace_service)
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        reserved: set[str] = set(source_ids_by_local_id.values())
        for variable in variables:
            source_id = source_ids_by_local_id.get(variable.id)
            if source_id is None:
                source_id = unique_environment_source_id(
                    variable.environment,
                    variable.name,
                    reserved=reserved,
                )
            reserved.add(source_id)
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
        workspace_service: BaseWorkspaceService,
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
        target_keys_by_source_id = _target_keys_by_source_id(variables)
        _ensure_unique_targets(target_keys_by_source_id)
        mapped_variables = {
            source_id: variable
            for source_id in sorted(variables)
            if (
                variable := await self._variable_by_source_id(
                    workspace_service,
                    source_id=source_id,
                )
            )
            is not None
        }
        source_ids_by_variable_id = {
            variable.id: source_id for source_id, variable in mapped_variables.items()
        }
        for source_id, variable in mapped_variables.items():
            spec = variables[source_id]
            await self._ensure_name_environment_available(
                workspace_service,
                source_id=source_id,
                name=spec.name,
                environment=spec.environment,
                variable_id=variable.id,
                source_ids_by_variable_id=source_ids_by_variable_id,
                target_keys_by_source_id=target_keys_by_source_id,
            )
        await self._release_changing_mapped_variables(
            workspace_service,
            variables_by_source_id=mapped_variables,
            target_keys_by_source_id=target_keys_by_source_id,
        )

        for source_id, spec in sorted(variables.items()):
            variable = await self._variable_for_import(
                workspace_service,
                source_id=source_id,
                spec=spec,
                mapped_variable=mapped_variables.get(source_id),
            )
            values = _values_from_spec(
                spec, existing=variable.values if variable else {}
            )
            if variable is None:
                variable = WorkspaceVariable(
                    workspace_id=workspace_service.workspace_id,
                    name=spec.name,
                    environment=spec.environment,
                    values=values,
                    description=spec.description,
                    tags=dict.fromkeys(spec.tags, "") if spec.tags else None,
                )
            else:
                variable.name = spec.name
                variable.environment = spec.environment
                variable.values = values
                variable.description = spec.description
                variable.tags = dict.fromkeys(spec.tags, "") if spec.tags else None
            workspace_service.session.add(variable)
            await workspace_service.session.flush()
            imported.append(self.imported_resource(source_id, variable.id))
        return imported

    async def _variable_for_import(
        self,
        workspace_service: BaseWorkspaceService,
        *,
        source_id: str,
        spec: VariableResourceSpec,
        mapped_variable: WorkspaceVariable | None = None,
    ) -> WorkspaceVariable | None:
        """Resolve the variable a spec should update, by mapping then by name/environment.

        Prefers the already-mapped variable, then falls back to one matching the
        spec's ``name`` and ``environment``; returns ``None`` when neither exists.
        """
        variable = mapped_variable or await self._variable_by_source_id(
            workspace_service,
            source_id=source_id,
        )
        if variable is not None:
            return variable

        return await workspace_service.session.scalar(
            select(WorkspaceVariable).where(
                WorkspaceVariable.workspace_id == workspace_service.workspace_id,
                WorkspaceVariable.name == spec.name,
                WorkspaceVariable.environment == spec.environment,
            )
        )

    async def _variable_by_source_id(
        self,
        workspace_service: BaseWorkspaceService,
        *,
        source_id: str,
    ) -> WorkspaceVariable | None:
        """Load the mapped :class:`WorkspaceVariable` for ``source_id``, if any."""
        local_id = await self.local_id_for_source_id(workspace_service, source_id)
        if local_id is None:
            return None

        return await workspace_service.session.scalar(
            select(WorkspaceVariable).where(
                WorkspaceVariable.workspace_id == workspace_service.workspace_id,
                WorkspaceVariable.id == local_id,
            )
        )

    async def _ensure_name_environment_available(
        self,
        workspace_service: BaseWorkspaceService,
        *,
        source_id: str,
        name: str,
        environment: str,
        variable_id: uuid.UUID,
        source_ids_by_variable_id: Mapping[uuid.UUID, str] | None = None,
        target_keys_by_source_id: Mapping[str, tuple[str, str]] | None = None,
    ) -> None:
        """Raise if another variable blocks claiming ``environment``/``name``.

        A conflict is tolerated when the blocking variable is itself in this sync
        batch and is moving away from ``(environment, name)``, since it will be
        released later; any other collision raises :class:`ValueError`.
        """
        conflict_id = await workspace_service.session.scalar(
            select(WorkspaceVariable.id).where(
                WorkspaceVariable.workspace_id == workspace_service.workspace_id,
                WorkspaceVariable.name == name,
                WorkspaceVariable.environment == environment,
                WorkspaceVariable.id != variable_id,
            )
        )
        if conflict_id is None:
            return

        if source_ids_by_variable_id and target_keys_by_source_id:
            conflict_source_id = source_ids_by_variable_id.get(conflict_id)
            if conflict_source_id is not None and target_keys_by_source_id[
                conflict_source_id
            ] != (environment, name):
                return

        raise ValueError(
            f"Variable sync source id {source_id!r} cannot use "
            f"{environment!r}/{name!r} because another variable already uses it."
        )

    async def _release_changing_mapped_variables(
        self,
        workspace_service: BaseWorkspaceService,
        *,
        variables_by_source_id: Mapping[str, WorkspaceVariable],
        target_keys_by_source_id: Mapping[str, tuple[str, str]],
    ) -> None:
        """Park variables whose identity is changing under temporary names.

        Renames each mapped variable whose ``(environment, name)`` differs from
        its target to a unique placeholder, freeing the original name so another
        variable in the batch can claim it without colliding.
        """
        changed = False
        reserved_names_by_environment = await _reserved_names_by_environment(
            workspace_service
        )
        for source_id, variable in variables_by_source_id.items():
            if (variable.environment, variable.name) == target_keys_by_source_id[
                source_id
            ]:
                continue
            reserved_names = reserved_names_by_environment.setdefault(
                variable.environment,
                set(),
            )
            reserved_names.discard(variable.name)
            variable.name = _unique_temporary_variable_name(variable, reserved_names)
            reserved_names.add(variable.name)
            workspace_service.session.add(variable)
            changed = True
        if changed:
            await workspace_service.session.flush()


def _values_from_spec(
    spec: VariableResourceSpec,
    *,
    existing: dict[str, Any] | None,
) -> dict[str, Any]:
    """Build a variable's stored values from its spec and current values.

    An explicit ``value`` overrides everything; otherwise declared ``keys`` are
    seeded from existing values (defaulting to ``""``). A spec with no keys
    leaves the existing values untouched.
    """
    if spec.value is not None:
        return spec.value if isinstance(spec.value, dict) else {"value": spec.value}
    existing_values = existing or {}
    if spec.keys is None:
        return dict(existing_values)
    return {key: existing_values.get(key, "") for key in spec.keys}


def _target_keys_by_source_id(
    variables: Mapping[str, VariableResourceSpec],
) -> dict[str, tuple[str, str]]:
    """Map each ``source_id`` to its target ``(environment, name)`` pair."""
    return {
        source_id: (spec.environment, spec.name)
        for source_id, spec in variables.items()
    }


def _ensure_unique_targets(
    target_keys_by_source_id: Mapping[str, tuple[str, str]],
) -> None:
    """Raise if two source ids target the same ``(environment, name)`` pair."""
    source_id_by_target: dict[tuple[str, str], str] = {}
    for source_id, target_key in sorted(target_keys_by_source_id.items()):
        if other_source_id := source_id_by_target.get(target_key):
            environment, name = target_key
            raise ValueError(
                "Variable sync specs must have unique environment/name targets: "
                f"{environment!r}/{name!r} is used by {other_source_id!r} "
                f"and {source_id!r}."
            )
        source_id_by_target[target_key] = source_id


async def _reserved_names_by_environment(
    workspace_service: BaseWorkspaceService,
) -> dict[str, set[str]]:
    """Return the set of in-use variable names per environment for the workspace."""
    rows = (
        await workspace_service.session.execute(
            select(WorkspaceVariable.environment, WorkspaceVariable.name).where(
                WorkspaceVariable.workspace_id == workspace_service.workspace_id
            )
        )
    ).tuples()
    reserved: dict[str, set[str]] = {}
    for environment, name in rows:
        reserved.setdefault(environment, set()).add(name)
    return reserved


def _unique_temporary_variable_name(
    variable: WorkspaceVariable,
    reserved_names: set[str],
) -> str:
    """Mint a placeholder variable name not present in ``reserved_names``.

    Derives the name from the variable's id and appends ``_2``, ``_3``, ... to
    break any collision with an already-reserved name.
    """
    base = f"__tracecat_sync_tmp_{variable.id.hex}"
    candidate = base
    counter = 2
    while candidate in reserved_names:
        candidate = f"{base}_{counter}"
        counter += 1
    return candidate
