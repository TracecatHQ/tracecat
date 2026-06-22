"""Table resource adapter (schema plus optional JSONL rows)."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any, cast

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from tracecat.db.models import Table, TableColumn
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import CursorPaginationParams
from tracecat.service import BaseWorkspaceService
from tracecat.sync import PullDiagnostic
from tracecat.tables.schemas import (
    TableColumnCreate,
    TableColumnUpdate,
    TableCreate,
    TableRowInsert,
    TableUpdate,
)
from tracecat.tables.service import BaseTablesService
from tracecat.workspace_sync.adapters.base import (
    CompoundYamlAdapter,
    ImportedResource,
    ProjectedResource,
    ResourceProjection,
    path_parts,
    sql_type,
    unique_source_id,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    TABLE_ROOT,
    TableResourceSpec,
    WorkspaceManifestResources,
    WorkspaceSpec,
)

TABLE_FILENAME = "table.yml"
"""Primary file name inside each table's directory."""


class TableAdapter(CompoundYamlAdapter):
    """Adapter for tables, syncing a column schema plus optional JSONL rows.

    The schema lives in ``table.yml`` and rows are serialized as a companion
    JSONL file (one JSON object per line) alongside it.
    """

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

    def _rows_source_path(self, source_id: str, rows_path: str) -> str:
        """Return the repository path of a table's companion JSONL rows file."""
        return f"{self.root}/{source_id}/{rows_path}"

    def extra_path_from_path(
        self,
        path: str,
        roots: WorkspaceManifestResources,
    ) -> tuple[str, str] | None:
        """Map a path under a table's directory to ``(source_id, relative_path)``."""
        parts = path_parts(path)
        root_parts = path_parts(roots.tables)
        if len(parts) < len(root_parts) + 2:
            return None
        if parts[: len(root_parts)] != root_parts:
            return None
        source_id = parts[len(root_parts)]
        relpath = "/".join(parts[len(root_parts) + 1 :])
        if not source_id or not relpath:
            return None
        return source_id, relpath

    def serialize_extra_files(
        self,
        source_id: str,
        spec: BaseModel,
    ) -> dict[str, str]:
        """Serialize a table's rows into a JSONL companion file, if it has any."""
        table = cast(TableResourceSpec, spec)
        if not table.rows or not table.rows_path:
            return {}
        return {
            self._rows_source_path(source_id, table.rows_path): "".join(
                json.dumps(row, sort_keys=True) + "\n" for row in table.rows
            )
        }

    def attach_extra_files(
        self,
        specs: dict[str, BaseModel],
        extra_files: Mapping[tuple[str, str], str],
        diagnostics: list[PullDiagnostic],
    ) -> dict[str, BaseModel]:
        """Parse each table's JSONL companion file and fold its rows into the spec."""
        updated: dict[str, BaseModel] = {}
        for source_id, base_spec in specs.items():
            spec = cast(TableResourceSpec, base_spec)
            rows: list[dict[str, Any]] = []
            if spec.rows_path and (
                content := extra_files.get((source_id, spec.rows_path))
            ):
                rows = self._parse_rows(
                    source_id,
                    spec,
                    content,
                    diagnostics=diagnostics,
                )
            updated[source_id] = spec.model_copy(update={"rows": rows})
        return updated

    def _parse_rows(
        self,
        source_id: str,
        spec: TableResourceSpec,
        content: str,
        *,
        diagnostics: list[PullDiagnostic],
    ) -> list[dict[str, Any]]:
        """Parse JSONL ``content`` into row dicts, recording per-line diagnostics.

        Blank lines are skipped; lines that fail to decode or are not JSON
        objects are dropped and reported via ``diagnostics``.
        """
        rows: list[dict[str, Any]] = []
        rows_path = spec.rows_path or ""
        for line_number, line in enumerate(content.splitlines(), start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=self._rows_source_path(source_id, rows_path),
                        workflow_title=spec.name,
                        error_type="parse",
                        message=f"Invalid table JSONL row at line {line_number}: {e}",
                        details={
                            "table": spec.name,
                            "line_number": line_number,
                            "error": str(e),
                        },
                    )
                )
                continue
            if not isinstance(row, dict):
                diagnostics.append(
                    PullDiagnostic(
                        workflow_path=self._rows_source_path(source_id, rows_path),
                        workflow_title=spec.name,
                        error_type="validation",
                        message=f"Table row at line {line_number} is not an object",
                        details={"table": spec.name, "line_number": line_number},
                    )
                )
                continue
            rows.append(row)
        return rows

    async def project(self, ctx: BaseWorkspaceService) -> ResourceProjection:
        """Project workspace tables into specs, including columns and all rows."""
        stmt = (
            select(Table)
            .where(Table.workspace_id == ctx.workspace_id)
            .options(selectinload(Table.columns))
            .order_by(Table.name.asc(), Table.id.asc())
        )
        tables = list((await ctx.session.execute(stmt)).scalars().all())
        table_service = BaseTablesService(session=ctx.session, role=ctx.role)
        source_ids_by_local_id = await self.source_ids_by_local_id(ctx)
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        reserved: set[str] = set(source_ids_by_local_id.values())
        for table in tables:
            source_id = source_ids_by_local_id.get(table.id)
            if source_id is None:
                source_id = unique_source_id(table.name, reserved=reserved)
            reserved.add(source_id)
            unique_columns = set(await table_service.get_index(table))
            columns: list[dict[str, Any]] = []
            for column in sorted(table.columns, key=lambda item: item.name):
                column_spec: dict[str, Any] = {
                    "name": column.name,
                    "type": column.type.lower(),
                }
                if not column.nullable:
                    column_spec["nullable"] = False
                if column.default is not None:
                    column_spec["default"] = column.default
                if column.options:
                    column_spec["options"] = column.options
                if column.name in unique_columns:
                    column_spec["unique"] = True
                columns.append(column_spec)
            rows = await self._project_rows(ctx, table, table_service=table_service)
            specs[source_id] = TableResourceSpec(
                id=source_id,
                name=table.name,
                columns=columns,
                rows_path="rows.jsonl",
                rows=rows,
            )
            resources.append(self.projected_resource(source_id, table.id))
        return ResourceProjection(specs=specs, resources=resources)

    async def _project_rows(
        self,
        ctx: BaseWorkspaceService,
        table: Table,
        *,
        table_service: BaseTablesService,
    ) -> list[dict[str, Any]]:
        """Page through a table's rows and return them in a deterministic order.

        Drops the ``id``, ``created_at``, and ``updated_at`` columns, coerces
        values to JSON-compatible data, and sorts the result so exports are
        stable across runs.
        """
        cursor: str | None = None
        rows: list[dict[str, Any]] = []
        while True:
            page = await table_service.list_rows(
                table,
                CursorPaginationParams(limit=200, cursor=cursor),
                order_by="id",
                sort="asc",
            )
            for row in page.items:
                rows.append(
                    {
                        key: _jsonable(value)
                        for key, value in row.items()
                        if key not in {"id", "created_at", "updated_at"}
                    }
                )
            if not page.next_cursor:
                break
            cursor = page.next_cursor
        return sorted(rows, key=lambda row: repr(sorted(row.items())))

    async def import_specs(
        self,
        ctx: BaseWorkspaceService,
        workspace_spec: WorkspaceSpec,
    ) -> list[ImportedResource]:
        """Reconcile table specs into the database: tables, columns, unique index, rows.

        Creates or renames each table, syncs its columns, reconciles its single
        optional unique column, then inserts (or upserts, when a unique column
        exists) its rows.
        """
        tables = workspace_spec.tables
        imported: list[ImportedResource] = []
        table_service = BaseTablesService(session=ctx.session, role=ctx.role)
        for source_id, spec in sorted(tables.items()):
            unique_columns = [
                str(column["name"]) for column in spec.columns if column.get("unique")
            ]
            if len(unique_columns) > 1:
                raise ValueError(
                    "Table sync supports at most one unique column per table: "
                    f"{spec.name} requested {', '.join(unique_columns)}"
                )
            table = await self._table_by_source_id(ctx, source_id)
            if table is not None:
                if table.name != spec.name:
                    table = await table_service.update_table(
                        table,
                        TableUpdate(name=spec.name),
                    )
                await ctx.session.refresh(table, ["columns"])
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
                    await ctx.session.refresh(table, ["columns"])
                else:
                    await ctx.session.refresh(table, ["columns"])

            existing_columns = {column.name: column for column in table.columns}
            for column in spec.columns:
                column_name = str(column["name"])
                existing_column = existing_columns.get(column_name)
                if existing_column is None:
                    await table_service.create_column(
                        table,
                        _column_create_from_spec(column),
                    )
                    continue
                if update := _column_update_from_spec(existing_column, column):
                    await ctx.session.refresh(existing_column, ["table"])
                    await table_service.update_column(existing_column, update)
            await ctx.session.refresh(table, ["columns"])

            await _reconcile_unique_column(
                table,
                unique_columns=unique_columns,
                table_service=table_service,
            )

            for row in spec.rows:
                await table_service.insert_row(
                    table,
                    TableRowInsert(data=row, upsert=bool(unique_columns)),
                )
            await ctx.session.flush()
            imported.append(self.imported_resource(source_id, table.id))
        return imported

    async def _table_by_source_id(
        self,
        ctx: BaseWorkspaceService,
        source_id: str,
    ) -> Table | None:
        """Load the mapped :class:`Table` (with columns) for ``source_id``, if any."""
        local_id = await self.local_id_for_source_id(ctx, source_id)
        if local_id is None:
            return None

        stmt = (
            select(Table)
            .where(
                Table.workspace_id == ctx.workspace_id,
                Table.id == local_id,
            )
            .options(selectinload(Table.columns))
        )
        return (await ctx.session.execute(stmt)).scalar_one_or_none()


def _column_create_from_spec(column: Mapping[str, Any]) -> TableColumnCreate:
    """Build a :class:`TableColumnCreate` from a spec column mapping."""
    return TableColumnCreate(
        name=str(column["name"]),
        type=sql_type(column["type"]),
        nullable=bool(column.get("nullable", True)),
        default=column.get("default"),
        options=cast(list[str] | None, column.get("options")),
    )


def _column_update_from_spec(
    existing: TableColumn,
    column: Mapping[str, Any],
) -> TableColumnUpdate | None:
    """Diff an existing column against its spec, or ``None`` if already in sync.

    Compares type, nullability, default, and options, returning a
    :class:`TableColumnUpdate` carrying only the fields that differ.
    """
    updates: dict[str, Any] = {}
    desired_type = sql_type(column["type"])
    if existing.type != desired_type.value:
        updates["type"] = desired_type

    desired_nullable = bool(column.get("nullable", True))
    if existing.nullable != desired_nullable:
        updates["nullable"] = desired_nullable

    desired_default = column.get("default")
    if existing.default != desired_default:
        updates["default"] = desired_default

    desired_options = cast(list[str] | None, column.get("options"))
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


def _jsonable(value: Any) -> Any:
    """Recursively coerce a row value into JSON-compatible data.

    Renders UUIDs and date/datetime values as strings and recurses through
    dicts and non-string sequences; other values pass through unchanged.
    """
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, Sequence) and not isinstance(value, str | bytes | bytearray):
        return [_jsonable(item) for item in value]
    return value
