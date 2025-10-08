"""Service layer for case duration metrics backed by case events."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from enum import Enum
from typing import Any

from pydantic import TypeAdapter, ValidationError
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.cases.durations.models import (
    CaseDurationAnchorSelection,
    CaseDurationComputation,
    CaseDurationCreate,
    CaseDurationDefinition,
    CaseDurationEventAnchor,
    CaseDurationUpdate,
)
from tracecat.db.schemas import Case, CaseEvent, Workspace
from tracecat.service import BaseWorkspaceService
from tracecat.types.auth import Role
from tracecat.types.exceptions import (
    TracecatNotFoundError,
    TracecatValidationError,
)


class CaseDurationService(BaseWorkspaceService):
    """Manage case duration definitions stored in workspace settings."""

    service_name = "case_durations"

    _definitions_adapter = TypeAdapter(list[CaseDurationDefinition])

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)

    async def list_definitions(self) -> list[CaseDurationDefinition]:
        """Return all duration definitions configured for the workspace."""

        workspace = await self._get_workspace()
        raw_definitions = workspace.settings.get("case_durations") or []
        try:
            return self._definitions_adapter.validate_python(raw_definitions)
        except ValidationError as exc:
            raise TracecatValidationError(
                "Workspace case duration settings are invalid"
            ) from exc

    async def get_definition(self, duration_id: uuid.UUID) -> CaseDurationDefinition:
        for definition in await self.list_definitions():
            if definition.id == duration_id:
                return definition
        raise TracecatNotFoundError(
            f"Case duration {duration_id} not found in this workspace"
        )

    async def create_definition(
        self, params: CaseDurationCreate
    ) -> CaseDurationDefinition:
        definitions = await self.list_definitions()
        if any(defn.name == params.name for defn in definitions):
            raise TracecatValidationError(
                f"A duration named '{params.name}' already exists"
            )

        definition = params.to_definition()
        definitions.append(definition)
        await self._persist_definitions(definitions)
        return definition

    async def update_definition(
        self, duration_id: uuid.UUID, params: CaseDurationUpdate
    ) -> CaseDurationDefinition:
        definitions = await self.list_definitions()
        for index, definition in enumerate(definitions):
            if definition.id != duration_id:
                continue

            updates = params.model_dump(exclude_unset=True)
            if not updates:
                return definition

            if (
                (new_name := updates.get("name"))
                and any(
                    other.name == new_name and other.id != duration_id
                    for other in definitions
                )
            ):
                raise TracecatValidationError(
                    f"A duration named '{new_name}' already exists"
                )

            updated_definition = definition.model_copy(update=updates)
            definitions[index] = updated_definition
            await self._persist_definitions(definitions)
            return updated_definition

        raise TracecatNotFoundError(
            f"Case duration {duration_id} not found in this workspace"
        )

    async def delete_definition(self, duration_id: uuid.UUID) -> None:
        definitions = await self.list_definitions()
        for index, definition in enumerate(definitions):
            if definition.id == duration_id:
                definitions.pop(index)
                await self._persist_definitions(definitions)
                return
        raise TracecatNotFoundError(
            f"Case duration {duration_id} not found in this workspace"
        )

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
            duration = (
                ended_at - started_at if started_at and ended_at else None
            )

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

    async def _persist_definitions(
        self, definitions: Sequence[CaseDurationDefinition]
    ) -> None:
        workspace = await self._get_workspace()
        settings = dict(workspace.settings or {})
        settings["case_durations"] = [
            definition.model_dump(mode="json") for definition in definitions
        ]
        workspace.settings = settings
        self.session.add(workspace)
        await self.session.commit()
        await self.session.refresh(workspace)

    async def _get_workspace(self) -> Workspace:
        stmt = select(Workspace).where(Workspace.id == self.workspace_id)
        result = await self.session.exec(stmt)
        workspace = result.first()
        if workspace is None:
            raise TracecatNotFoundError(
                f"Workspace {self.workspace_id} could not be found"
            )
        return workspace

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
            raise TracecatNotFoundError(
                f"Case {case} not found in this workspace"
            )
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

    def _matches_filters(
        self, event: CaseEvent, filters: dict[str, Any]
    ) -> bool:
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
