"""Table resource adapter (schema only, never runtime rows)."""

from __future__ import annotations

from typing import Any

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracecat.db.models import Table, TableColumn
from tracecat.exceptions import TracecatNotFoundError
from tracecat.service import BaseWorkspaceService
from tracecat.tables.schemas import (
    TableColumnCreate,
    TableColumnUpdate,
    TableCreate,
    TableUpdate,
)
from tracecat.tables.service import BaseTablesService
from tracecat.workspace_sync.adapters.base import (
    CompoundYamlAdapter,
    ImportedResource,
    ProjectedResource,
    ResourceDependencyRefs,
    ResourceProjection,
    sql_type,
    unique_source_id,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    TABLE_ROOT,
    TableColumnSpec,
    TableResourceSpec,
    WorkspaceSpec,
)

TABLE_FILENAME = "table.yml"
"""Primary file name inside each table's directory."""


class TableAdapter(CompoundYamlAdapter):
    """Adapter for tables, syncing table metadata and schema only."""

    resource_type = SyncResourceType.TABLE
    """The sync resource type this adapter handles."""
    spec_attr = "tables"
    """Attribute on ``WorkspaceSpec``/``WorkspaceManifestResources`` for tables."""
    model = TableResourceSpec
    """Pydantic spec model tables serialize to and from."""
    root = TABLE_ROOT
    """Top-level repository directory for tables."""
    filename = TABLE_FILENAME
    """Primary file name inside each table's directory."""

    async def project(
        self, workspace_service: BaseWorkspaceService
    ) -> ResourceProjection:
        """Project workspace table metadata and column schema into specs."""
        stmt = self._projection_stmt(workspace_service)
        tables = list((await workspace_service.session.execute(stmt)).scalars().all())
        return await self._projection_from_tables(workspace_service, tables)

    async def project_dependency_refs(
        self,
        workspace_service: BaseWorkspaceService,
        refs: ResourceDependencyRefs,
    ) -> ResourceProjection:
        """Project tables selected directly or referenced by table name."""
        if refs.select_all:
            return await self.project(workspace_service)
        if not refs.local_ids and not refs.source_ids and not refs.names:
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
        stmt = self._projection_stmt(workspace_service)
        if local_ids and refs.names:
            stmt = stmt.where(
                sa.or_(Table.id.in_(local_ids), Table.name.in_(refs.names))
            )
        elif local_ids:
            stmt = stmt.where(Table.id.in_(local_ids))
        else:
            stmt = stmt.where(Table.name.in_(refs.names))
        tables = list((await workspace_service.session.execute(stmt)).scalars().all())
        return await self._projection_from_tables(workspace_service, tables)

    def _projection_stmt(
        self, workspace_service: BaseWorkspaceService
    ) -> sa.Select[tuple[Table]]:
        """Build the base eager-loaded table projection query."""
        return (
            select(Table)
            .where(Table.workspace_id == workspace_service.workspace_id)
            .options(selectinload(Table.columns))
            .order_by(Table.name.asc(), Table.id.asc())
        )

    async def _projection_from_tables(
        self,
        workspace_service: BaseWorkspaceService,
        tables: list[Table],
    ) -> ResourceProjection:
        """Build sync specs from eager-loaded table rows."""
        table_service = BaseTablesService(
            session=workspace_service.session, role=workspace_service.role
        )
        source_ids_by_local_id = await self.source_ids_by_local_id(workspace_service)
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        reserved: set[str] = set(source_ids_by_local_id.values())
        for table in tables:
            source_id = source_ids_by_local_id.get(table.id)
            if source_id is None:
                source_id = unique_source_id(table.name, reserved=reserved)
            reserved.add(source_id)
            unique_columns = set(await table_service.get_index(table))
            columns: list[TableColumnSpec] = []
            for column in sorted(table.columns, key=lambda item: item.name):
                columns.append(
                    TableColumnSpec(
                        name=column.name,
                        type=column.type.lower(),
                        nullable=None if column.nullable else False,
                        default=column.default,
                        options=column.options or None,
                        unique=True if column.name in unique_columns else None,
                    )
                )
            specs[source_id] = TableResourceSpec(
                id=source_id,
                name=table.name,
                columns=columns,
            )
            resources.append(self.projected_resource(source_id, table.id))
        return ResourceProjection(specs=specs, resources=resources)

    async def import_specs(
        self,
        workspace_service: BaseWorkspaceService,
        workspace_spec: WorkspaceSpec,
    ) -> list[ImportedResource]:
        """Reconcile table specs into the database.

        Creates or renames each table, syncs its columns, and reconciles its
        single optional unique column. Table rows are runtime data and are not
        imported from Git.
        """
        tables = workspace_spec.tables
        imported: list[ImportedResource] = []
        table_service = BaseTablesService(
            session=workspace_service.session, role=workspace_service.role
        )
        for source_id, spec in sorted(tables.items()):
            unique_columns = [column.name for column in spec.columns if column.unique]
            if len(unique_columns) > 1:
                raise ValueError(
                    "Table sync supports at most one unique column per table: "
                    f"{spec.name} requested {', '.join(unique_columns)}"
                )
            table = await self._table_by_source_id(workspace_service, source_id)
            if table is not None:
                if table.name != spec.name:
                    table = await table_service.update_table(
                        table,
                        TableUpdate(name=spec.name),
                    )
                await workspace_service.session.refresh(table, ["columns"])
            else:
                try:
                    table = await table_service.get_table_by_name(spec.name)
                except TracecatNotFoundError:
                    table = await table_service.create_table(
                        TableCreate(
                            name=spec.name,
                            columns=[
                                _column_create_from_spec(column)
                                for column in spec.columns
                            ],
                        )
                    )
                    await workspace_service.session.refresh(table, ["columns"])
                else:
                    await workspace_service.session.refresh(table, ["columns"])

            existing_columns = {column.name: column for column in table.columns}
            for column in spec.columns:
                column_name = column.name
                existing_column = existing_columns.get(column_name)
                if existing_column is None:
                    await table_service.create_column(
                        table,
                        _column_create_from_spec(column),
                    )
                    continue
                if update := _column_update_from_spec(existing_column, column):
                    await workspace_service.session.refresh(existing_column, ["table"])
                    await table_service.update_column(existing_column, update)
            await workspace_service.session.refresh(table, ["columns"])

            await _reconcile_unique_column(
                table,
                unique_columns=unique_columns,
                table_service=table_service,
            )

            await workspace_service.session.flush()
            imported.append(self.imported_resource(source_id, table.id))
        return imported

    async def _table_by_source_id(
        self,
        workspace_service: BaseWorkspaceService,
        source_id: str,
    ) -> Table | None:
        """Load the mapped :class:`Table` (with columns) for ``source_id``, if any."""
        local_id = await self.local_id_for_source_id(workspace_service, source_id)
        if local_id is None:
            return None

        stmt = (
            select(Table)
            .where(
                Table.workspace_id == workspace_service.workspace_id,
                Table.id == local_id,
            )
            .options(selectinload(Table.columns))
        )
        return (await workspace_service.session.execute(stmt)).scalar_one_or_none()


def _column_create_from_spec(column: TableColumnSpec) -> TableColumnCreate:
    """Build a :class:`TableColumnCreate` from a spec column."""
    return TableColumnCreate(
        name=column.name,
        type=sql_type(column.type),
        nullable=column.nullable if column.nullable is not None else True,
        default=column.default,
        options=column.options,
    )


def _column_update_from_spec(
    existing: TableColumn,
    column: TableColumnSpec,
) -> TableColumnUpdate | None:
    """Diff an existing column against its spec, or ``None`` if already in sync.

    Compares type, nullability, default, and options, returning a
    :class:`TableColumnUpdate` carrying only the fields that differ.
    """
    updates: dict[str, Any] = {}
    desired_type = sql_type(column.type)
    if existing.type != desired_type.value:
        updates["type"] = desired_type

    desired_nullable = column.nullable if column.nullable is not None else True
    if existing.nullable != desired_nullable:
        updates["nullable"] = desired_nullable

    desired_default = column.default
    if existing.default != desired_default:
        updates["default"] = desired_default

    desired_options = column.options
    if existing.options != desired_options:
        updates["options"] = desired_options

    return TableColumnUpdate(**updates) if updates else None


async def _reconcile_unique_column(
    table: Table,
    *,
    unique_columns: list[str],
    table_service: BaseTablesService,
) -> None:
    """Align a table's unique index with the single desired unique column.

    Drops any current unique indexes that are not the desired column and
    creates the desired one if it is missing. ``unique_columns`` holds at most
    one entry; an empty list means no column should be unique.
    """
    desired_unique_column = unique_columns[0] if unique_columns else None
    current_unique_columns = set(await table_service.get_index(table))
    desired_unique_columns = {desired_unique_column} if desired_unique_column else set()

    for column_name in sorted(current_unique_columns - desired_unique_columns):
        try:
            await table_service.drop_unique_index(table, column_name)
        except ValueError:
            pass

    if desired_unique_column and desired_unique_column not in current_unique_columns:
        await table_service.create_unique_index(table, desired_unique_column)
