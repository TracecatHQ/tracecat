from __future__ import annotations

import uuid
from collections.abc import Sequence
from typing import Any

from sqlalchemy.orm import selectinload
from sqlmodel import and_, col, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.db.schemas import Entity, EntityField, EntityRecord
from tracecat.entities.enums import FieldType
from tracecat.entities.models import coerce_default_value
from tracecat.records.model import RecordCreate, RecordRead, RecordUpdate
from tracecat.service import BaseWorkspaceService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatNotFoundError
from tracecat.types.pagination import (
    BaseCursorPaginator,
    CursorPaginatedResponse,
    CursorPaginationParams,
)


class RecordService(BaseWorkspaceService):
    """Service for managing records (instances) of an entity within a workspace."""

    service_name = "records"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)

    async def _get_active_fields(self, entity: Entity) -> Sequence[EntityField]:
        if entity.owner_id != self.workspace_id:
            raise TracecatNotFoundError("Entity not found")

        stmt = (
            select(EntityField)
            .where(EntityField.entity_id == entity.id, EntityField.is_active)
            .options(selectinload(EntityField.options))  # type: ignore[arg-type]
        )
        result = await self.session.exec(stmt)
        return result.all()

    def _validate_and_coerce(
        self, data: dict[str, Any], fields: Sequence[EntityField]
    ) -> dict[str, Any]:
        """Validate and coerce record payload using entity field definitions.

        - Unknown keys are rejected
        - Values are coerced according to FieldType
        - SELECT and MULTI_SELECT are validated against available option keys
        - JSON must be dict or list
        """
        by_key: dict[str, EntityField] = {f.key: f for f in fields}
        normalized: dict[str, Any] = {}

        for key, value in data.items():
            field = by_key.get(key)
            if field is None:
                raise ValueError(f"Unknown field key: '{key}'")

            if value is None:
                normalized[key] = None
                continue

            if field.type in (FieldType.SELECT, FieldType.MULTI_SELECT):
                option_keys = {opt.key for opt in field.options}
                if field.type == FieldType.SELECT:
                    coerced = str(value)
                    if coerced not in option_keys:
                        raise ValueError(
                            f"Invalid value for '{key}': '{coerced}' not in options"
                        )
                    normalized[key] = coerced
                else:
                    if not isinstance(value, list):
                        raise ValueError(
                            f"Invalid value for '{key}': MULTI_SELECT requires list"
                        )
                    coerced_list = [str(v) for v in value]
                    invalid = [v for v in coerced_list if v not in option_keys]
                    if invalid:
                        raise ValueError(
                            f"Invalid values for '{key}': {', '.join(invalid)} not in options"
                        )
                    normalized[key] = coerced_list
                continue

            # For other types, reuse default value coercion logic
            normalized[key] = coerce_default_value(field.type, value)

        return normalized

    async def list_entity_records(
        self, entity: Entity, params: CursorPaginationParams
    ) -> CursorPaginatedResponse[RecordRead]:
        if entity.owner_id != self.workspace_id:
            raise TracecatNotFoundError("Entity not found")

        paginator = BaseCursorPaginator(self.session)
        total_estimate = await paginator.get_table_row_estimate("entity_record")

        stmt = (
            select(EntityRecord)
            .where(
                EntityRecord.owner_id == self.workspace_id,
                EntityRecord.entity_id == entity.id,
            )
            .order_by(col(EntityRecord.created_at).desc(), col(EntityRecord.id).desc())
        )

        if params.cursor:
            cursor_data = paginator.decode_cursor(params.cursor)
            cursor_time = cursor_data.created_at
            cursor_id = uuid.UUID(cursor_data.id)

            if params.reverse:
                stmt = stmt.where(
                    or_(
                        col(EntityRecord.created_at) > cursor_time,
                        and_(
                            col(EntityRecord.created_at) == cursor_time,
                            col(EntityRecord.id) > cursor_id,
                        ),
                    )
                ).order_by(
                    col(EntityRecord.created_at).asc(), col(EntityRecord.id).asc()
                )
            else:
                stmt = stmt.where(
                    or_(
                        col(EntityRecord.created_at) < cursor_time,
                        and_(
                            col(EntityRecord.created_at) == cursor_time,
                            col(EntityRecord.id) < cursor_id,
                        ),
                    )
                )

        stmt = stmt.limit(params.limit + 1)
        result = await self.session.exec(stmt)
        all_items = result.all()

        has_more = len(all_items) > params.limit
        page_items = all_items[: params.limit] if has_more else all_items

        # When reverse, presentation should remain consistent (newest first)
        if params.cursor and params.reverse:
            page_items = list(reversed(page_items))

        next_cursor = None
        prev_cursor = None
        has_previous = params.cursor is not None

        if page_items:
            first_item = page_items[0]
            last_item = page_items[-1]

            if params.reverse:
                # Swap next/prev when reversing
                if has_more:
                    prev_cursor = paginator.encode_cursor(
                        last_item.created_at, last_item.id
                    )
                if params.cursor:
                    next_cursor = paginator.encode_cursor(
                        first_item.created_at, first_item.id
                    )
            else:
                if has_more:
                    next_cursor = paginator.encode_cursor(
                        last_item.created_at, last_item.id
                    )
                if params.cursor:
                    prev_cursor = paginator.encode_cursor(
                        first_item.created_at, first_item.id
                    )

        items = [
            RecordRead(
                id=r.id,
                entity_id=r.entity_id,
                data=r.data,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in page_items
        ]

        return CursorPaginatedResponse(
            items=items,  # type: ignore[arg-type]
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=has_more,
            has_previous=has_previous,
            total_estimate=total_estimate,
        )

    async def list_records(
        self, params: CursorPaginationParams, *, entity_id: uuid.UUID | None = None
    ) -> CursorPaginatedResponse[RecordRead]:
        paginator = BaseCursorPaginator(self.session)
        total_estimate = await paginator.get_table_row_estimate("entity_record")

        stmt = (
            select(EntityRecord)
            .where(EntityRecord.owner_id == self.workspace_id)
            .order_by(col(EntityRecord.created_at).desc(), col(EntityRecord.id).desc())
        )
        if entity_id is not None:
            stmt = stmt.where(EntityRecord.entity_id == entity_id)

        if params.cursor:
            cursor_data = paginator.decode_cursor(params.cursor)
            cursor_time = cursor_data.created_at
            cursor_id = uuid.UUID(cursor_data.id)

            if params.reverse:
                stmt = stmt.where(
                    or_(
                        col(EntityRecord.created_at) > cursor_time,
                        and_(
                            col(EntityRecord.created_at) == cursor_time,
                            col(EntityRecord.id) > cursor_id,
                        ),
                    )
                ).order_by(
                    col(EntityRecord.created_at).asc(), col(EntityRecord.id).asc()
                )
            else:
                stmt = stmt.where(
                    or_(
                        col(EntityRecord.created_at) < cursor_time,
                        and_(
                            col(EntityRecord.created_at) == cursor_time,
                            col(EntityRecord.id) < cursor_id,
                        ),
                    )
                )

        stmt = stmt.limit(params.limit + 1)
        result = await self.session.exec(stmt)
        all_items = result.all()

        has_more = len(all_items) > params.limit
        page_items = all_items[: params.limit] if has_more else all_items

        if params.cursor and params.reverse:
            page_items = list(reversed(page_items))

        next_cursor = None
        prev_cursor = None
        has_previous = params.cursor is not None

        if page_items:
            first_item = page_items[0]
            last_item = page_items[-1]

            if params.reverse:
                if has_more:
                    prev_cursor = paginator.encode_cursor(
                        last_item.created_at, last_item.id
                    )
                if params.cursor:
                    next_cursor = paginator.encode_cursor(
                        first_item.created_at, first_item.id
                    )
            else:
                if has_more:
                    next_cursor = paginator.encode_cursor(
                        last_item.created_at, last_item.id
                    )
                if params.cursor:
                    prev_cursor = paginator.encode_cursor(
                        first_item.created_at, first_item.id
                    )

        items = [
            RecordRead(
                id=r.id,
                entity_id=r.entity_id,
                data=r.data,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in page_items
        ]

        return CursorPaginatedResponse(
            items=items,  # type: ignore[arg-type]
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=has_more,
            has_previous=has_previous,
            total_estimate=total_estimate,
        )

    async def get_record(self, entity: Entity, record_id: uuid.UUID) -> EntityRecord:
        if entity.owner_id != self.workspace_id:
            raise TracecatNotFoundError("Entity not found")
        stmt = select(EntityRecord).where(
            EntityRecord.owner_id == self.workspace_id,
            EntityRecord.entity_id == entity.id,
            EntityRecord.id == record_id,
        )
        result = await self.session.exec(stmt)
        record = result.first()
        if record is None:
            raise TracecatNotFoundError("Record not found")
        return record

    async def get_record_by_id(self, record_id: uuid.UUID) -> EntityRecord:
        """Get a record in the current workspace by id."""
        stmt = select(EntityRecord).where(
            EntityRecord.owner_id == self.workspace_id, EntityRecord.id == record_id
        )
        result = await self.session.exec(stmt)
        record = result.first()
        if record is None:
            raise TracecatNotFoundError("Record not found")
        return record

    async def create_record(self, entity: Entity, params: RecordCreate) -> EntityRecord:
        if entity.owner_id != self.workspace_id:
            raise TracecatNotFoundError("Entity not found")

        fields = await self._get_active_fields(entity)
        normalized = self._validate_and_coerce(params.data, fields)

        record = EntityRecord(
            owner_id=self.workspace_id,
            entity_id=entity.id,
            data=normalized,
        )
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def update_record(
        self, record: EntityRecord, params: RecordUpdate
    ) -> EntityRecord:
        if record.owner_id != self.workspace_id:
            raise TracecatNotFoundError("Record not found")

        # Fetch entity to validate against active fields
        stmt = select(Entity).where(Entity.id == record.entity_id)
        entity = (await self.session.exec(stmt)).one()

        fields = await self._get_active_fields(entity)
        normalized = self._validate_and_coerce(params.data, fields)

        # Merge into existing payload
        next_data = dict(record.data or {})
        next_data.update(normalized)
        record.data = next_data

        await self.session.commit()
        await self.session.refresh(record)
        return record

    async def delete_record(self, record: EntityRecord) -> None:
        if record.owner_id != self.workspace_id:
            raise TracecatNotFoundError("Record not found")
        await self.session.delete(record)
        await self.session.commit()
