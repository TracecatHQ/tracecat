"""Entity → Table migration utilities."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import sqlalchemy as sa
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.auth.types import Role
from tracecat.authz.enums import WorkspaceRole
from tracecat.db.models import (
    CaseRecord,
    CaseTableRow,
    Entity,
    EntityField,
    Table,
)
from tracecat.entities.enums import FieldType
from tracecat.identifiers.workflow import WorkspaceUUID
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import TableColumnCreate, TableCreate
from tracecat.tables.service import BaseTablesService

from .type_conversion import field_type_to_sql_type


@dataclass(slots=True)
class MigrationResult:
    """Result of a migration operation."""

    entity_id: uuid.UUID
    table_id: uuid.UUID
    table_name: str
    columns_created: int
    rows_migrated: int
    case_links_migrated: int
    errors: list[str]


@dataclass(slots=True)
class EntityMigrationPreview:
    """Preview information for a single entity migration."""

    entity_id: uuid.UUID
    entity_key: str
    workspace_id: uuid.UUID
    field_count: int
    record_count: int


@dataclass(slots=True)
class FieldMigrationPlan:
    """Instructions for migrating field data."""

    original_key: str
    column_name: str
    sql_type: SqlType
    source_type: FieldType
    enum_options: set[str] | None


class MigrationRunner:
    """Base class defining the migration protocol."""

    name: str
    description: str

    def __init__(self) -> None:
        self._session: AsyncSession | None = None
        self._role: Role | None = None

    async def initialize(self, *, session: AsyncSession, role: Role) -> None:
        self._session = session
        self._role = role

    @property
    def session(self) -> AsyncSession:
        if self._session is None:  # pragma: no cover - defensive guard
            raise RuntimeError("Migration not initialised")
        return self._session

    @property
    def role(self) -> Role:
        if self._role is None:  # pragma: no cover - defensive guard
            raise RuntimeError("Migration not initialised")
        return self._role

    async def preview(
        self, *, workspace_id: uuid.UUID | None = None
    ) -> Sequence[EntityMigrationPreview]:
        raise NotImplementedError

    async def run(
        self, *, workspace_id: uuid.UUID | None = None
    ) -> Sequence[MigrationResult]:
        raise NotImplementedError


class EntityToTableMigration(MigrationRunner):
    """Service for migrating Entities and EntityRecords to Tables and TableRows."""

    name = "entity-to-table"
    description = "Migrate legacy entities/records to workspace tables."

    async def preview(
        self, *, workspace_id: uuid.UUID | None = None
    ) -> Sequence[EntityMigrationPreview]:
        entities = await self._load_entities(workspace_id=workspace_id)
        previews: list[EntityMigrationPreview] = []
        for entity in entities:
            previews.append(
                EntityMigrationPreview(
                    entity_id=entity.id,
                    entity_key=entity.key,
                    workspace_id=entity.owner_id,
                    field_count=len(entity.fields),
                    record_count=len(entity.records),
                )
            )
        return previews

    async def run(
        self, *, workspace_id: uuid.UUID | None = None
    ) -> Sequence[MigrationResult]:
        entities = await self._load_entities(workspace_id=workspace_id)
        results: list[MigrationResult] = []

        for entity in entities:
            # Create service with entity's owner_id
            role = self.role.model_copy(
                update={
                    "workspace_id": entity.owner_id,
                    "workspace_role": WorkspaceRole.ADMIN,
                }
            )
            service = BaseTablesService(self.session, role)
            errors: list[str] = []

            try:
                # Resolve table name (handle conflicts)
                sanitized = service._sanitize_identifier(entity.key)
                stmt = select(Table).where(
                    Table.owner_id == entity.owner_id,
                    Table.name == sanitized,
                )
                result = await self.session.execute(stmt)
                existing = result.scalar_one_or_none()
                if existing is None:
                    table_name = sanitized
                else:
                    suffix = 1
                    while True:
                        candidate_name = f"{sanitized}_{suffix}"
                        stmt = select(Table).where(
                            Table.owner_id == entity.owner_id,
                            Table.name == candidate_name,
                        )
                        result = await self.session.execute(stmt)
                        if result.scalar_one_or_none() is None:
                            table_name = candidate_name
                            break
                        suffix += 1

                columns, plans = self._convert_fields_to_columns(entity.fields, errors)
                table = await service.create_table(
                    TableCreate(name=table_name, columns=columns)
                )

                # Materialize field plans (sanitize column names)
                materialised_plans: dict[str, FieldMigrationPlan] = {}
                for plan in plans:
                    sanitized_col = service._sanitize_identifier(plan.original_key)
                    materialised_plans[plan.original_key] = FieldMigrationPlan(
                        original_key=plan.original_key,
                        column_name=sanitized_col,
                        sql_type=plan.sql_type,
                        source_type=plan.source_type,
                        enum_options=plan.enum_options,
                    )

                rows_migrated = await self._migrate_records(
                    entity=entity,
                    table=table,
                    service=service,
                    plans=materialised_plans,
                    errors=errors,
                )
                case_links = await self._migrate_case_links(entity=entity, table=table)

                results.append(
                    MigrationResult(
                        entity_id=entity.id,
                        table_id=table.id,
                        table_name=table.name,
                        columns_created=len(columns),
                        rows_migrated=rows_migrated,
                        case_links_migrated=case_links,
                        errors=errors,
                    )
                )
            except Exception as exc:
                await self.session.rollback()
                errors.append(f"Failed to migrate entity {entity.key}: {exc}")
                results.append(
                    MigrationResult(
                        entity_id=entity.id,
                        table_id=uuid.UUID(int=0),
                        table_name=entity.key,
                        columns_created=0,
                        rows_migrated=0,
                        case_links_migrated=0,
                        errors=errors,
                    )
                )
            else:
                await self.session.commit()

        return results

    async def _load_entities(
        self, *, workspace_id: uuid.UUID | None
    ) -> Sequence[Entity]:
        stmt = (
            select(Entity)
            .options(
                selectinload(Entity.fields).selectinload(EntityField.options),
                selectinload(Entity.records),
            )
            .where(Entity.is_active.is_(True))
        )
        if workspace_id is not None:
            stmt = stmt.where(Entity.owner_id == workspace_id)

        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    @staticmethod
    def _convert_fields_to_columns(
        fields: Iterable[EntityField], errors: list[str]
    ) -> tuple[list[TableColumnCreate], list[FieldMigrationPlan]]:
        columns: list[TableColumnCreate] = []
        plans: list[FieldMigrationPlan] = []
        for field in fields:
            if not field.is_active:
                continue
            try:
                sql_type = field_type_to_sql_type(field.type)
                default_payload: Any | None = field.default_value
                enum_options: set[str] | None = None

                if sql_type is SqlType.ENUM:
                    # Build enum metadata
                    if not field.options:
                        raise ValueError("Enum fields require at least one option")
                    enum_values = [opt.key for opt in field.options]
                    default_payload = {"enum_values": enum_values}
                    if (
                        isinstance(field.default_value, str)
                        and field.default_value.strip()
                    ):
                        candidate = field.default_value.strip()
                        if candidate not in enum_values:
                            errors.append(
                                f"Default value '{candidate}' for field '{field.key}' not found in options"
                            )
                        else:
                            default_payload["default"] = candidate
                    enum_options = {opt.key for opt in field.options}
                elif field.type is FieldType.MULTI_SELECT:
                    enum_options = {opt.key for opt in field.options}

                column = TableColumnCreate(
                    name=field.key,
                    type=sql_type,
                    nullable=True,
                    default=default_payload,
                )
                columns.append(column)
                plans.append(
                    FieldMigrationPlan(
                        original_key=field.key,
                        column_name=field.key,
                        sql_type=sql_type,
                        source_type=field.type,
                        enum_options=enum_options,
                    )
                )
            except Exception as exc:
                errors.append(f"Failed to convert field {field.key}: {exc}")
        return columns, plans

    async def _migrate_records(
        self,
        *,
        entity: Entity,
        table: Table,
        service: BaseTablesService,
        plans: Mapping[str, FieldMigrationPlan],
        errors: list[str],
    ) -> int:
        if not entity.records:
            return 0

        schema_name = service._get_schema_name(WorkspaceUUID.new(entity.owner_id))
        table_name = service._sanitize_identifier(table.name)

        metadata = sa.MetaData(schema=schema_name)
        dynamic_table = sa.Table(
            table_name,
            metadata,
            sa.Column("id", sa.UUID, primary_key=True),
            sa.Column("created_at", sa.TIMESTAMP(timezone=True), nullable=False),
            sa.Column("updated_at", sa.TIMESTAMP(timezone=True), nullable=False),
            sa.Column("data", sa.dialects.postgresql.JSONB, nullable=False),
            schema=schema_name,
        )

        conn = await self.session.connection()

        chunk_size = 1000
        total_inserted = 0

        for index in range(0, len(entity.records), chunk_size):
            chunk = entity.records[index : index + chunk_size]
            values = []
            for record in chunk:
                # Coerce record payload
                raw_data = record.data
                coerced_data: dict[str, Any] = {}
                if isinstance(raw_data, Mapping):
                    for original_key, value in raw_data.items():
                        plan = plans.get(original_key)
                        if plan is None:
                            coerced_data[original_key] = value
                            continue

                        # Coerce value based on plan
                        if value is None:
                            coerced_data[plan.column_name] = None
                        elif plan.sql_type is SqlType.ENUM:
                            coerced = str(value)
                            if plan.enum_options and coerced not in plan.enum_options:
                                errors.append(
                                    f"Record {record.id} in entity '{entity.key}' uses '{coerced}' "
                                    f"which is not a valid option for '{plan.original_key}'"
                                )
                            coerced_data[plan.column_name] = coerced
                        elif plan.source_type is FieldType.MULTI_SELECT:
                            if isinstance(value, list):
                                coerced_list = [str(item) for item in value]
                            elif isinstance(value, str):
                                coerced_list = [value]
                            else:
                                errors.append(
                                    f"Record {record.id} in entity '{entity.key}' has invalid multi-select "
                                    f"value for '{plan.original_key}'"
                                )
                                coerced_list = []

                            if plan.enum_options:
                                invalid = [
                                    item
                                    for item in coerced_list
                                    if item not in plan.enum_options
                                ]
                                if invalid:
                                    errors.append(
                                        f"Record {record.id} in entity '{entity.key}' has values {invalid} "
                                        f"not present in options for '{plan.original_key}'"
                                    )
                            coerced_data[plan.column_name] = coerced_list
                        else:
                            coerced_data[plan.column_name] = value

                values.append(
                    {
                        "id": record.id,
                        "created_at": record.created_at,
                        "updated_at": record.updated_at,
                        "data": coerced_data,
                    }
                )

            stmt = sa.insert(dynamic_table).values(values)
            result = await conn.execute(stmt)
            total_inserted += result.rowcount

        await self.session.flush()
        return total_inserted

    async def _migrate_case_links(self, *, entity: Entity, table: Table) -> int:
        stmt = select(CaseRecord).where(CaseRecord.entity_id == entity.id)
        result = await self.session.execute(stmt)
        links = list(result.scalars().all())

        if not links:
            return 0

        created = 0
        for link in links:
            case_table_row = CaseTableRow(
                case_id=link.case_id,
                table_id=table.id,
                row_id=link.record_id,
            )
            self.session.add(case_table_row)
            created += 1

        await self.session.flush()
        return created
