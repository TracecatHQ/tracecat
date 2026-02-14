"""Service layer for case duration metrics backed by case events."""

from __future__ import annotations

import uuid
from collections.abc import Sequence
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from slugify import slugify
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.durations.schemas import (
    CaseDurationAnchorSelection,
    CaseDurationComputation,
    CaseDurationCreate,
    CaseDurationDefinitionCreate,
    CaseDurationDefinitionRead,
    CaseDurationDefinitionUpdate,
    CaseDurationEventAnchor,
    CaseDurationMetric,
    CaseDurationRead,
    CaseDurationUpdate,
)
from tracecat.db.models import Case, CaseDuration, CaseEvent
from tracecat.db.models import CaseDurationDefinition as CaseDurationDefinitionDB
from tracecat.exceptions import (
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.service import BaseWorkspaceService, requires_entitlement
from tracecat.tables.common import coerce_to_utc_datetime
from tracecat.tiers.enums import Entitlement


class CaseDurationDefinitionService(BaseWorkspaceService):
    """Manage case duration definitions stored in the database."""

    service_name = "case_duration_definitions"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def list_definitions(self) -> list[CaseDurationDefinitionRead]:
        """Return all duration definitions configured for the workspace."""

        stmt = (
            select(CaseDurationDefinitionDB)
            .where(CaseDurationDefinitionDB.workspace_id == self.workspace_id)
            .order_by(CaseDurationDefinitionDB.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return [self._to_read_model(row) for row in result.scalars().all()]

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def get_definition(
        self, duration_id: uuid.UUID
    ) -> CaseDurationDefinitionRead:
        """Retrieve a single case duration definition."""

        entity = await self._get_definition_entity(duration_id)
        return self._to_read_model(entity)

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def create_definition(
        self, params: CaseDurationDefinitionCreate
    ) -> CaseDurationDefinitionRead:
        """Create a new case duration definition."""

        await self._ensure_unique_name(params.name)

        entity = CaseDurationDefinitionDB(
            workspace_id=self.workspace_id,
            name=params.name,
            description=params.description,
            **self._anchor_attributes(params.start_anchor, "start"),
            **self._anchor_attributes(params.end_anchor, "end"),
        )
        self.session.add(entity)
        await self.session.commit()
        await self.session.refresh(entity)
        return self._to_read_model(entity)

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def update_definition(
        self, duration_id: uuid.UUID, params: CaseDurationDefinitionUpdate
    ) -> CaseDurationDefinitionRead:
        """Update an existing case duration definition."""

        entity = await self._get_definition_entity(duration_id)
        updates = params.model_dump(exclude_unset=True)
        if not updates:
            return self._to_read_model(entity)

        set_fields = params.model_fields_set

        if (new_name := updates.get("name")) is not None:
            await self._ensure_unique_name(new_name, exclude_id=entity.id)
            entity.name = new_name

        if "description" in updates:
            entity.description = updates["description"]

        if "start_anchor" in set_fields:
            start_anchor = params.start_anchor
            if start_anchor is None:
                raise TracecatValidationError(
                    "Start anchor cannot be null when updating a duration definition."
                )
            self._apply_anchor(entity, start_anchor, "start")

        if "end_anchor" in set_fields:
            end_anchor = params.end_anchor
            if end_anchor is None:
                raise TracecatValidationError(
                    "End anchor cannot be null when updating a duration definition."
                )
            self._apply_anchor(entity, end_anchor, "end")

        self.session.add(entity)
        await self.session.commit()
        await self.session.refresh(entity)
        return self._to_read_model(entity)

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def delete_definition(self, duration_id: uuid.UUID) -> None:
        """Delete a case duration definition."""

        entity = await self._get_definition_entity(duration_id)
        await self.session.delete(entity)
        await self.session.commit()

    async def _get_definition_entity(
        self, duration_id: uuid.UUID
    ) -> CaseDurationDefinitionDB:
        stmt = select(CaseDurationDefinitionDB).where(
            CaseDurationDefinitionDB.id == duration_id,
            CaseDurationDefinitionDB.workspace_id == self.workspace_id,
        )
        result = await self.session.execute(stmt)
        entity = result.scalars().first()
        if entity is None:
            raise TracecatNotFoundError(
                f"Case duration definition {duration_id} not found in this workspace"
            )
        return entity

    async def _ensure_unique_name(
        self, name: str, *, exclude_id: uuid.UUID | None = None
    ) -> None:
        stmt = select(CaseDurationDefinitionDB.id).where(
            CaseDurationDefinitionDB.workspace_id == self.workspace_id,
            CaseDurationDefinitionDB.name == name,
        )
        if exclude_id is not None:
            stmt = stmt.where(CaseDurationDefinitionDB.id != exclude_id)
        result = await self.session.execute(stmt)
        if result.scalars().first() is not None:
            raise TracecatValidationError(f"A duration named '{name}' already exists")

    def _to_read_model(
        self, entity: CaseDurationDefinitionDB
    ) -> CaseDurationDefinitionRead:
        return CaseDurationDefinitionRead(
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

    def _json_compatible(self, value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, dict):
            return {key: self._json_compatible(item) for key, item in value.items()}
        if isinstance(value, list | tuple | set):
            return [self._json_compatible(item) for item in value]
        return value


class CaseDurationService(BaseWorkspaceService):
    """Manage persisted case durations and compute anchored metrics."""

    service_name = "case_durations"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)
        self.definitions = CaseDurationDefinitionService(
            session=self.session,
            role=self.role,
        )

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def list_durations(self, case: Case | uuid.UUID) -> list[CaseDurationRead]:
        """List persisted case durations for a case."""

        case_obj = await self._resolve_case(case)
        stmt = (
            select(CaseDuration)
            .where(
                CaseDuration.workspace_id == self.workspace_id,
                CaseDuration.case_id == case_obj.id,
            )
            .order_by(CaseDuration.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return [self._to_read_model(row) for row in result.scalars().all()]

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def get_duration(
        self, case: Case | uuid.UUID, duration_id: uuid.UUID
    ) -> CaseDurationRead:
        """Retrieve a persisted case duration."""

        case_obj = await self._resolve_case(case)
        entity = await self._get_case_duration_entity(duration_id, case_obj.id)
        return self._to_read_model(entity)

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def create_duration(
        self, case: Case | uuid.UUID, params: CaseDurationCreate
    ) -> CaseDurationRead:
        """Create a persisted case duration record."""

        case_obj = await self._resolve_case(case)
        await self.definitions.get_definition(params.definition_id)
        await self._ensure_unique_case_duration(case_obj.id, params.definition_id)

        entity = CaseDuration(
            workspace_id=self.workspace_id,
            case_id=case_obj.id,
            definition_id=params.definition_id,
            start_event_id=params.start_event_id,
            end_event_id=params.end_event_id,
            started_at=params.started_at,
            ended_at=params.ended_at,
            duration=params.duration,
        )
        self.session.add(entity)
        await self.session.commit()
        await self.session.refresh(entity)
        return self._to_read_model(entity)

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def update_duration(
        self, case: Case | uuid.UUID, duration_id: uuid.UUID, params: CaseDurationUpdate
    ) -> CaseDurationRead:
        """Update a persisted case duration record."""

        case_obj = await self._resolve_case(case)
        entity = await self._get_case_duration_entity(duration_id, case_obj.id)
        updates = params.model_dump(exclude_unset=True)
        if not updates:
            return self._to_read_model(entity)

        if (definition_id := updates.get("definition_id")) is not None:
            await self.definitions.get_definition(definition_id)
            await self._ensure_unique_case_duration(
                case_obj.id, definition_id, exclude_id=entity.id
            )
            entity.definition_id = definition_id

        for field in (
            "start_event_id",
            "end_event_id",
            "started_at",
            "ended_at",
            "duration",
        ):
            if field in updates:
                setattr(entity, field, updates[field])

        self.session.add(entity)
        await self.session.commit()
        await self.session.refresh(entity)
        return self._to_read_model(entity)

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def delete_duration(
        self, case: Case | uuid.UUID, duration_id: uuid.UUID
    ) -> None:
        """Delete a persisted case duration record."""

        case_obj = await self._resolve_case(case)
        entity = await self._get_case_duration_entity(duration_id, case_obj.id)
        await self.session.delete(entity)
        await self.session.commit()

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def compute_duration(
        self, case: Case | uuid.UUID
    ) -> list[CaseDurationComputation]:
        """Compute all configured durations for a case using its events."""

        case_obj = await self._resolve_case(case)
        definitions = await self.definitions.list_definitions()
        if not definitions:
            return []

        events = await self._list_case_events(case_obj)
        return self._compute_durations_from_events(events, definitions)

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def compute_durations(
        self, cases: Sequence[Case]
    ) -> dict[uuid.UUID, list[CaseDurationComputation]]:
        """Compute durations for multiple cases efficiently.

        Fetches definitions once and all events in a single query,
        then computes durations for each case.

        Args:
            cases: Sequence of Case objects (must belong to workspace).

        Returns:
            Dict mapping case_id to list of computed durations.
        """
        if not cases:
            return {}

        definitions = await self.definitions.list_definitions()
        if not definitions:
            return {case.id: [] for case in cases}

        # Fetch all events for all cases in one query
        case_ids = [case.id for case in cases]
        stmt = (
            select(CaseEvent)
            .where(
                CaseEvent.workspace_id == self.workspace_id,
                CaseEvent.case_id.in_(case_ids),
            )
            .order_by(CaseEvent.case_id.asc(), CaseEvent.created_at.asc())
        )
        result = await self.session.execute(stmt)
        all_events = list(result.scalars().all())

        # Group events by case_id
        events_by_case: dict[uuid.UUID, list[CaseEvent]] = {
            case_id: [] for case_id in case_ids
        }
        for event in all_events:
            events_by_case[event.case_id].append(event)

        # Compute durations for each case
        return {
            case_id: self._compute_durations_from_events(events, definitions)
            for case_id, events in events_by_case.items()
        }

    @requires_entitlement(Entitlement.CASE_ADDONS)
    async def compute_time_series(
        self, cases: Sequence[Case]
    ) -> list[CaseDurationMetric]:
        """Compute durations as metrics optimized for time-series platforms.

        Returns flat, OTEL-aligned Gauge metrics suitable for direct ingestion
        into Grafana, Elasticsearch, Splunk, and other observability platforms.

        Each metric contains:
        - timestamp: When the duration was measured (ended_at)
        - metric_name: "case_duration_seconds" (includes unit)
        - value: Duration in seconds
        - duration_type: The type of duration (e.g., "ttr", "tta")
        - Case dimensions: priority, severity, status (for groupby)
        - Case identifiers: case_id, case_short_id (for drill-down)

        Args:
            cases: Sequence of Case objects (must belong to workspace).

        Returns:
            Flat list of duration metrics, one per completed (case, duration) pair.
        """
        if not cases:
            return []

        durations_by_case = await self.compute_durations(cases)
        return self._format_time_series(cases, durations_by_case)

    def _format_time_series(
        self,
        cases: Sequence[Case],
        durations_by_case: dict[uuid.UUID, list[CaseDurationComputation]],
    ) -> list[CaseDurationMetric]:
        """Format computed durations as time-series metrics.

        Pure formatting function that converts internal models to OTEL-aligned
        metric format for time-series platforms.
        """
        metrics: list[CaseDurationMetric] = []

        for case in cases:
            durations = durations_by_case.get(case.id, [])
            for computation in durations:
                # Skip incomplete durations (no end time or duration)
                if computation.duration is None or computation.ended_at is None:
                    continue

                metrics.append(
                    CaseDurationMetric(
                        # Timestamp is when duration was measured (end point)
                        timestamp=computation.ended_at,
                        # Metric identity
                        metric_name="case_duration_seconds",
                        value=computation.duration.total_seconds(),
                        # Duration identification
                        duration_name=computation.name,
                        duration_slug=slugify(computation.name, separator="_"),
                        # Case dimensions (low cardinality)
                        case_priority=case.priority.value,
                        case_severity=case.severity.value,
                        case_status=case.status.value,
                        # Case identifiers (high cardinality, for drill-down)
                        case_id=str(case.id),
                        case_short_id=case.short_id,
                    )
                )
        return metrics

    def _compute_durations_from_events(
        self,
        events: Sequence[CaseEvent],
        definitions: Sequence[CaseDurationDefinitionRead],
    ) -> list[CaseDurationComputation]:
        """Pure computation of durations from events and definitions."""
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

    async def sync_case_durations(
        self, case: Case | uuid.UUID
    ) -> list[CaseDurationComputation]:
        """Persist computed duration values for a case and return calculations."""
        if not await self.has_entitlement(Entitlement.CASE_ADDONS):
            return []
        case_obj = await self._resolve_case(case)
        computations = await self.compute_duration(case_obj)

        stmt = select(CaseDuration).where(
            CaseDuration.workspace_id == self.workspace_id,
            CaseDuration.case_id == case_obj.id,
        )
        existing_result = await self.session.execute(stmt)
        existing_by_definition = {
            entity.definition_id: entity for entity in existing_result.scalars().all()
        }

        seen_definitions: set[uuid.UUID] = set()
        for computation in computations:
            seen_definitions.add(computation.duration_id)
            entity = existing_by_definition.get(computation.duration_id)
            if entity is None:
                entity = CaseDuration(
                    workspace_id=self.workspace_id,
                    case_id=case_obj.id,
                    definition_id=computation.duration_id,
                )

            entity.start_event_id = computation.start_event_id
            entity.end_event_id = computation.end_event_id
            entity.started_at = computation.started_at
            entity.ended_at = computation.ended_at
            entity.duration = computation.duration
            self.session.add(entity)

        for definition_id, entity in existing_by_definition.items():
            if definition_id not in seen_definitions:
                await self.session.delete(entity)

        await self.session.flush()
        return computations

    async def _ensure_unique_case_duration(
        self,
        case_id: uuid.UUID,
        definition_id: uuid.UUID,
        *,
        exclude_id: uuid.UUID | None = None,
    ) -> None:
        stmt = select(CaseDuration.id).where(
            CaseDuration.workspace_id == self.workspace_id,
            CaseDuration.case_id == case_id,
            CaseDuration.definition_id == definition_id,
        )
        if exclude_id is not None:
            stmt = stmt.where(CaseDuration.id != exclude_id)
        result = await self.session.execute(stmt)
        if result.scalars().first() is not None:
            raise TracecatValidationError(
                "A duration for this definition already exists on the case"
            )

    async def _get_case_duration_entity(
        self, duration_id: uuid.UUID, case_id: uuid.UUID
    ) -> CaseDuration:
        stmt = select(CaseDuration).where(
            CaseDuration.id == duration_id,
            CaseDuration.workspace_id == self.workspace_id,
            CaseDuration.case_id == case_id,
        )
        result = await self.session.execute(stmt)
        entity = result.scalars().first()
        if entity is None:
            raise TracecatNotFoundError(
                f"Case duration {duration_id} not found for this case"
            )
        return entity

    async def _resolve_case(self, case: Case | uuid.UUID) -> Case:
        if isinstance(case, Case):
            if case.workspace_id != self.workspace_id:
                raise TracecatNotFoundError(
                    "Case does not belong to the active workspace"
                )
            return case

        stmt = select(Case).where(
            Case.id == case,
            Case.workspace_id == self.workspace_id,
        )
        result = await self.session.execute(stmt)
        resolved = result.scalars().first()
        if resolved is None:
            raise TracecatNotFoundError(f"Case {case} not found in this workspace")
        return resolved

    async def _list_case_events(self, case: Case) -> list[CaseEvent]:
        stmt = (
            select(CaseEvent)
            .where(
                CaseEvent.case_id == case.id,
                CaseEvent.workspace_id == self.workspace_id,
            )
            .order_by(CaseEvent.created_at.asc())
        )
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

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
            actual_normalized = self._normalize_filter_value(actual)
            expected_normalized = self._normalize_filter_value(expected)
            if isinstance(expected_normalized, list):
                if actual_normalized is None:
                    return False
                if isinstance(actual_normalized, list):
                    if not any(
                        item in expected_normalized for item in actual_normalized
                    ):
                        return False
                elif actual_normalized not in expected_normalized:
                    return False
            elif actual_normalized != expected_normalized:
                return False
        return True

    def _normalize_filter_value(self, value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, list | tuple | set):
            return [self._normalize_filter_value(item) for item in value]
        return value

    def _extract_timestamp(
        self, event: CaseEvent, anchor: CaseDurationEventAnchor
    ) -> datetime | None:
        value = self._resolve_field(event, anchor.timestamp_path)
        try:
            return coerce_to_utc_datetime(value)
        except (TypeError, ValueError, OverflowError):
            return None

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

    def _to_read_model(self, entity: CaseDuration) -> CaseDurationRead:
        return CaseDurationRead(
            id=entity.id,
            case_id=entity.case_id,
            definition_id=entity.definition_id,
            start_event_id=entity.start_event_id,
            end_event_id=entity.end_event_id,
            started_at=entity.started_at,
            ended_at=entity.ended_at,
            duration=entity.duration,
        )
