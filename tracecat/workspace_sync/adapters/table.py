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

from tracecat.db.models import Table, WorkspaceSyncResourceMapping
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import CursorPaginationParams
from tracecat.service import BaseWorkspaceService
from tracecat.sync import PullDiagnostic
from tracecat.tables.schemas import TableColumnCreate, TableCreate, TableRowInsert
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
from tracecat.workspace_sync.enums import SyncResourceType, VcsProvider
from tracecat.workspace_sync.schemas import (
    TABLE_ROOT,
    TableResourceSpec,
    WorkspaceManifestResources,
)

TABLE_FILENAME = "table.yml"


class TableAdapter(CompoundYamlAdapter):
    resource_type = SyncResourceType.TABLE
    spec_attr = "tables"
    model = TableResourceSpec
    root = TABLE_ROOT
    filename = TABLE_FILENAME

    def _rows_source_path(self, source_id: str, rows_path: str) -> str:
        return f"{self.root}/{source_id}/{rows_path}"

    def extra_path_from_path(
        self,
        path: str,
        roots: WorkspaceManifestResources,
    ) -> tuple[str, str] | None:
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
        stmt = (
            select(Table)
            .where(Table.workspace_id == ctx.workspace_id)
            .options(selectinload(Table.columns))
            .order_by(Table.name.asc(), Table.id.asc())
        )
        tables = list((await ctx.session.execute(stmt)).scalars().all())
        table_service = BaseTablesService(session=ctx.session, role=ctx.role)
        source_ids_by_local_id = await self._source_ids_by_local_id(ctx)
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

    async def _source_ids_by_local_id(
        self,
        ctx: BaseWorkspaceService,
    ) -> dict[uuid.UUID, str]:
        stmt = select(
            WorkspaceSyncResourceMapping.local_id,
            WorkspaceSyncResourceMapping.source_id,
        ).where(
            WorkspaceSyncResourceMapping.workspace_id == ctx.workspace_id,
            WorkspaceSyncResourceMapping.provider == VcsProvider.GITHUB.value,
            WorkspaceSyncResourceMapping.resource_type == self.resource_type.value,
        )
        return dict((await ctx.session.execute(stmt)).tuples().all())

    async def _project_rows(
        self,
        ctx: BaseWorkspaceService,
        table: Table,
        *,
        table_service: BaseTablesService,
    ) -> list[dict[str, Any]]:
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
        specs: Mapping[str, BaseModel],
    ) -> list[ImportedResource]:
        tables = cast(Mapping[str, TableResourceSpec], specs)
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
            try:
                table = await table_service.get_table_by_name(spec.name)
            except TracecatNotFoundError:
                table = await table_service.create_table(
                    TableCreate(
                        name=spec.name,
                        columns=[
                            TableColumnCreate(
                                name=str(column["name"]),
                                type=sql_type(column["type"]),
                                nullable=bool(column.get("nullable", True)),
                                default=column.get("default"),
                                options=column.get("options"),
                            )
                            for column in spec.columns
                        ],
                    )
                )
                await ctx.session.refresh(table, ["columns"])
            else:
                await ctx.session.refresh(table, ["columns"])
                existing_columns = {column.name for column in table.columns}
                for column in spec.columns:
                    column_name = str(column["name"])
                    if column_name in existing_columns:
                        continue
                    await table_service.create_column(
                        table,
                        TableColumnCreate(
                            name=column_name,
                            type=sql_type(column["type"]),
                            nullable=bool(column.get("nullable", True)),
                            default=column.get("default"),
                            options=column.get("options"),
                        ),
                    )
                await ctx.session.refresh(table, ["columns"])

            for column in spec.columns:
                if not column.get("unique"):
                    continue
                try:
                    if not await table_service.get_index(table):
                        await table_service.create_unique_index(
                            table, str(column["name"])
                        )
                except ValueError:
                    pass

            for row in spec.rows:
                await table_service.insert_row(
                    table, TableRowInsert(data=row, upsert=True)
                )
            await ctx.session.flush()
            imported.append(self.imported_resource(source_id, table.id))
        return imported


def _jsonable(value: Any) -> Any:
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
