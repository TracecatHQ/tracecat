"""Service layer for case duration metrics backed by case events."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.cases.durations.models import (
    CaseDurationAnchorSelection,
    CaseDurationComputation,
    CaseDurationCreate,
    CaseDurationEventAnchor,
    CaseDurationRead,
    CaseDurationUpdate,
)
from tracecat.db.schemas import (
    Case,
    CaseEvent,
)
from tracecat.db.schemas import (
    CaseDurationDefinition as CaseDurationDefinitionDB,
)
from tracecat.service import BaseWorkspaceService
from tracecat.types.auth import Role
from tracecat.types.exceptions import (
    TracecatNotFoundError,
    TracecatValidationError,
)


class CaseDurationService(BaseWorkspaceService):
    """Manage case duration definitions stored in the database."""

    service_name = "case_durations"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)

    async def list_definitions(self) -> list[CaseDurationRead]:
        """Return all duration definitions configured for the workspace."""

        stmt = (
            select(CaseDurationDefinitionDB)
            .where(CaseDurationDefinitionDB.owner_id == self.workspace_id)
            .order_by(col(CaseDurationDefinitionDB.created_at).asc())
        )
        result = await self.session.exec(stmt)
        return [self._to_read_model(row) for row in result.all()]

    async def get_definition(self, duration_id: uuid.UUID) -> CaseDurationRead:
        entity = await self._get_definition_entity(duration_id)
        return self._to_read_model(entity)

    async def create_definition(self, params: CaseDurationCreate) -> CaseDurationRead:
        await self._ensure_unique_name(params.name)

        entity = CaseDurationDefinitionDB(
            owner_id=self.workspace_id,
            name=params.name,
            description=params.description,
            **self._anchor_attributes(params.start_anchor, "start"),
            **self._anchor_attributes(params.end_anchor, "end"),
        )
        self.session.add(entity)
        await self.session.commit()
        await self.session.refresh(entity)
        return self._to_read_model(entity)

    async def update_definition(
        self, duration_id: uuid.UUID, params: CaseDurationUpdate
    ) -> CaseDurationRead:
        entity = await self._get_definition_entity(duration_id)
        updates = params.model_dump(exclude_unset=True)
        if not updates:
            return self._to_read_model(entity)

        if (new_name := updates.get("name")) is not None:
            await self._ensure_unique_name(new_name, exclude_id=entity.id)
            entity.name = new_name

        if "description" in updates:
            entity.description = updates["description"]

        if (start_anchor := updates.get("start_anchor")) is not None:
            self._apply_anchor(entity, start_anchor, "start")

        if (end_anchor := updates.get("end_anchor")) is not None:
            self._apply_anchor(entity, end_anchor, "end")

        self.session.add(entity)
        await self.session.commit()
        await self.session.refresh(entity)
        return self._to_read_model(entity)

    async def delete_definition(self, duration_id: uuid.UUID) -> None:
        entity = await self._get_definition_entity(duration_id)
        await self.session.delete(entity)
        await self.session.commit()

    async def compute_for_case(
        self, case: Case | uuid.UUID
    ) -> list[CaseDurationComputation]:
        """Compute all configured durations for a case using its events."""

        case_obj = await self._resolve_case(case)
        definitions = await self.list_definitions()
        if not definitions:
            return []

        events = await self._list_case_events(case_obj)
        results: list[CaseDurationComputation] = []
        for definition in definitions:
            start_match = self._find_matching_event(events, definition.start_anchor)
            end_match = self._find_matching_event(
                events,
                definition.end_anchor,
                earliest_after=start_match[1] if start_match else None,
            )

            started_at = start_match[1] if start_match else None
            ended_at = end_match[1] if end_match else None
            if started_at and ended_at and ended_at < started_at:
                end_match = None
                ended_at = None
            duration = ended_at - started_at if started_at and ended_at else None

            results.append(
                CaseDurationComputation(
                    duration_id=definition.id,
                    name=definition.name,
                    description=definition.description,
                    start_event_id=start_match[0].id if start_match else None,
                    end_event_id=end_match[0].id if end_match else None,
                    started_at=started_at,
                    ended_at=ended_at,
                    duration=duration,
                )
            )

        return results

    async def _get_definition_entity(
        self, duration_id: uuid.UUID
    ) -> CaseDurationDefinitionDB:
        stmt = select(CaseDurationDefinitionDB).where(
            CaseDurationDefinitionDB.id == duration_id,
            CaseDurationDefinitionDB.owner_id == self.workspace_id,
        )
        result = await self.session.exec(stmt)
        entity = result.first()
        if entity is None:
            raise TracecatNotFoundError(
                f"Case duration {duration_id} not found in this workspace"
            )
        return entity

    async def _ensure_unique_name(
        self, name: str, *, exclude_id: uuid.UUID | None = None
    ) -> None:
        stmt = select(CaseDurationDefinitionDB.id).where(
            CaseDurationDefinitionDB.owner_id == self.workspace_id,
            CaseDurationDefinitionDB.name == name,
        )
        if exclude_id is not None:
            stmt = stmt.where(CaseDurationDefinitionDB.id != exclude_id)
        result = await self.session.exec(stmt)
        if result.first() is not None:
            raise TracecatValidationError(f"A duration named '{name}' already exists")

    def _to_read_model(self, entity: CaseDurationDefinitionDB) -> CaseDurationRead:
        return CaseDurationRead(
            id=entity.id,
            name=entity.name,
            description=entity.description,
            start_anchor=self._anchor_from_entity(entity, "start"),
            end_anchor=self._anchor_from_entity(entity, "end"),
        )

    def _anchor_from_entity(
        self, entity: CaseDurationDefinitionDB, prefix: Literal["start", "end"]
    ) -> CaseDurationEventAnchor:
        return CaseDurationEventAnchor(
            event_type=getattr(entity, f"{prefix}_event_type"),
            timestamp_path=getattr(entity, f"{prefix}_timestamp_path"),
            field_filters=dict(getattr(entity, f"{prefix}_field_filters") or {}),
            selection=getattr(entity, f"{prefix}_selection"),
        )

    def _anchor_attributes(
        self, anchor: CaseDurationEventAnchor, prefix: Literal["start", "end"]
    ) -> dict[str, Any]:
        filters = {
            key: self._json_compatible(value)
            for key, value in anchor.field_filters.items()
        }
        return {
            f"{prefix}_event_type": anchor.event_type,
            f"{prefix}_timestamp_path": anchor.timestamp_path,
            f"{prefix}_field_filters": filters,
            f"{prefix}_selection": anchor.selection,
        }

    def _apply_anchor(
        self,
        entity: CaseDurationDefinitionDB,
        anchor: CaseDurationEventAnchor,
        prefix: Literal["start", "end"],
    ) -> None:
        for attr, value in self._anchor_attributes(anchor, prefix).items():
            setattr(entity, attr, value)

    async def _resolve_case(self, case: Case | uuid.UUID) -> Case:
        if isinstance(case, Case):
            if case.owner_id != self.workspace_id:
                raise TracecatNotFoundError(
                    "Case does not belong to the active workspace"
                )
            return case

        stmt = select(Case).where(
            Case.id == case,
            Case.owner_id == self.workspace_id,
        )
        result = await self.session.exec(stmt)
        resolved = result.first()
        if resolved is None:
            raise TracecatNotFoundError(f"Case {case} not found in this workspace")
        return resolved

    async def _list_case_events(self, case: Case) -> list[CaseEvent]:
        stmt = (
            select(CaseEvent)
            .where(
                CaseEvent.case_id == case.id,
                CaseEvent.owner_id == self.workspace_id,
            )
            .order_by(col(CaseEvent.created_at).asc())
        )
        result = await self.session.exec(stmt)
        return list(result.all())

    def _find_matching_event(
        self,
        events: Sequence[CaseEvent],
        anchor: CaseDurationEventAnchor,
        *,
        earliest_after: datetime | None = None,
    ) -> tuple[CaseEvent, datetime] | None:
        candidates: list[tuple[CaseEvent, datetime]] = []
        for event in events:
            if event.type != anchor.event_type:
                continue
            if not self._matches_filters(event, anchor.field_filters):
                continue
            timestamp = self._extract_timestamp(event, anchor)
            if timestamp is None:
                continue
            if earliest_after and timestamp < earliest_after:
                continue
            candidates.append((event, timestamp))

        if not candidates:
            return None
        candidates.sort(key=lambda item: item[1])
        if anchor.selection is CaseDurationAnchorSelection.LAST:
            return candidates[-1]
        return candidates[0]

    def _matches_filters(self, event: CaseEvent, filters: dict[str, Any]) -> bool:
        for path, expected in filters.items():
            actual = self._resolve_field(event, path)
            if isinstance(actual, Enum):
                actual = actual.value
            if isinstance(expected, Enum):
                expected = expected.value
            if actual != expected:
                return False
        return True

    def _extract_timestamp(
        self, event: CaseEvent, anchor: CaseDurationEventAnchor
    ) -> datetime | None:
        value = self._resolve_field(event, anchor.timestamp_path)
        return self._coerce_datetime(value)

    def _resolve_field(self, event: CaseEvent, path: str) -> Any:
        value: Any = event
        for part in path.split("."):
            if isinstance(value, dict):
                value = value.get(part)
            else:
                value = getattr(value, part, None)
            if value is None:
                return None
        return value

    def _coerce_datetime(self, value: Any) -> datetime | None:
        if isinstance(value, datetime):
            if value.tzinfo is None:
                return value.replace(tzinfo=UTC)
            return value.astimezone(UTC)
        if isinstance(value, str):
            text = value
            if text.endswith("Z"):
                text = text[:-1] + "+00:00"
            try:
                parsed = datetime.fromisoformat(text)
            except ValueError:
                return None
            if parsed.tzinfo is None:
                return parsed.replace(tzinfo=UTC)
            return parsed.astimezone(UTC)
        return None

    def _json_compatible(self, value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, dict):
            return {key: self._json_compatible(item) for key, item in value.items()}
        if isinstance(value, list | tuple | set):
            return [self._json_compatible(item) for item in value]
        return value
