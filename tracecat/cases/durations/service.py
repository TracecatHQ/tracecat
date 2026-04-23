"""Service layer for case duration metrics backed by case events."""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Sequence
from datetime import datetime
from enum import Enum
from typing import Any, Literal

from slugify import slugify
from sqlalchemy import bindparam, column, false, func, literal, or_, select
from sqlalchemy import case as sql_case
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.sql.elements import ColumnElement

from tracecat.auth.types import Role
from tracecat.cases.durations.schemas import (
    CaseDurationAnchorSelection,
    CaseDurationComputation,
    CaseDurationCreate,
    CaseDurationDefinitionCreate,
    CaseDurationDefinitionRead,
    CaseDurationDefinitionUpdate,
    CaseDurationEventAnchor,
    CaseDurationEventFilters,
    CaseDurationMetric,
    CaseDurationRead,
    CaseDurationUpdate,
)
from tracecat.cases.enums import CaseEventType
from tracecat.concurrency import cooperative_every
from tracecat.db.models import Case, CaseDuration, CaseEvent
from tracecat.db.models import CaseDurationDefinition as CaseDurationDefinitionDB
from tracecat.exceptions import (
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.service import BaseWorkspaceService, requires_entitlement
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
        raw_filters = dict(getattr(entity, f"{prefix}_field_filters") or {})
        event_type = getattr(entity, f"{prefix}_event_type")
        selection = getattr(entity, f"{prefix}_selection")
        filters, has_unsupported_filters = self._filters_from_storage(
            event_type, raw_filters
        )
        if has_unsupported_filters:
            anchor = CaseDurationEventAnchor.model_construct(
                event_type=event_type,
                filters=filters,
                selection=selection,
            )
        else:
            try:
                anchor = CaseDurationEventAnchor(
                    event_type=event_type,
                    filters=filters,
                    selection=selection,
                )
            except ValueError:
                has_unsupported_filters = True
                anchor = CaseDurationEventAnchor.model_construct(
                    event_type=event_type,
                    filters=filters,
                    selection=selection,
                )
        anchor._has_unsupported_filters = has_unsupported_filters
        return anchor

    def _anchor_attributes(
        self, anchor: CaseDurationEventAnchor, prefix: Literal["start", "end"]
    ) -> dict[str, Any]:
        filters = self._filters_to_storage(anchor.filters)
        return {
            f"{prefix}_event_type": anchor.event_type,
            f"{prefix}_timestamp_path": "created_at",
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

    def _filters_to_storage(self, filters: CaseDurationEventFilters) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        if filters.new_values:
            payload["new_values"] = self._json_compatible(filters.new_values)
        if filters.tag_refs:
            payload["tag_refs"] = self._json_compatible(filters.tag_refs)
        if filters.field_ids:
            payload["field_ids"] = self._json_compatible(filters.field_ids)
        if filters.dropdown_definition_id is not None:
            payload["dropdown_definition_id"] = self._json_compatible(
                filters.dropdown_definition_id
            )
        if filters.dropdown_option_ids:
            payload["dropdown_option_ids"] = self._json_compatible(
                filters.dropdown_option_ids
            )
        return payload

    def _filters_from_storage(
        self, event_type: CaseEventType, raw_filters: dict[str, Any]
    ) -> tuple[CaseDurationEventFilters, bool]:
        if not raw_filters:
            return CaseDurationEventFilters(), False

        if self._storage_filters_are_typed(raw_filters):
            return CaseDurationEventFilters.model_validate(raw_filters), False

        filters = self._known_legacy_filters_to_typed(event_type, raw_filters)
        if filters is not None:
            return filters, False
        return CaseDurationEventFilters(), True

    def _storage_filters_are_typed(self, raw_filters: dict[str, Any]) -> bool:
        typed_keys = {
            "new_values",
            "tag_refs",
            "field_ids",
            "dropdown_definition_id",
            "dropdown_option_ids",
        }
        return all(key in typed_keys for key in raw_filters)

    def _known_legacy_filters_to_typed(
        self, event_type: CaseEventType, raw_filters: dict[str, Any]
    ) -> CaseDurationEventFilters | None:
        if event_type in {
            CaseEventType.PRIORITY_CHANGED,
            CaseEventType.SEVERITY_CHANGED,
            CaseEventType.STATUS_CHANGED,
        }:
            if set(raw_filters) == {"data.new"}:
                values = self._normalize_string_filter_values(raw_filters["data.new"])
                return CaseDurationEventFilters(new_values=values) if values else None
            return None

        if event_type in {CaseEventType.TAG_ADDED, CaseEventType.TAG_REMOVED}:
            if set(raw_filters) == {"data.tag_ref"}:
                values = self._normalize_string_filter_values(
                    raw_filters["data.tag_ref"]
                )
                return CaseDurationEventFilters(tag_refs=values) if values else None
            return None

        if event_type is CaseEventType.FIELDS_CHANGED:
            if set(raw_filters) == {"data.changes.field"}:
                values = self._normalize_string_filter_values(
                    raw_filters["data.changes.field"]
                )
                return CaseDurationEventFilters(field_ids=values) if values else None
            return None

        if event_type is CaseEventType.DROPDOWN_VALUE_CHANGED:
            if set(raw_filters) == {"data.definition_id", "data.new_option_id"}:
                definition_id = raw_filters["data.definition_id"]
                if not isinstance(definition_id, str):
                    return None
                option_ids = self._normalize_string_filter_values(
                    raw_filters["data.new_option_id"]
                )
                if not option_ids:
                    return None
                return CaseDurationEventFilters(
                    dropdown_definition_id=definition_id,
                    dropdown_option_ids=option_ids,
                )
        return None

    def _normalize_string_filter_values(self, value: Any) -> list[str]:
        if isinstance(value, Enum):
            return [str(value.value)]
        if isinstance(value, str):
            return [value]
        if isinstance(value, list | tuple | set):
            return [
                str(item.value) if isinstance(item, Enum) else item
                for item in value
                if isinstance(item, str | Enum)
            ]
        return []


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

        return await self._compute_durations_from_db(case_obj, definitions)

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

        return await asyncio.to_thread(
            self._compute_durations_by_case,
            events_by_case,
            definitions,
        )

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
        start_match_cache: dict[tuple[Any, ...], tuple[CaseEvent, datetime] | None] = {}
        end_match_cache: dict[
            tuple[tuple[Any, ...], datetime | None], tuple[CaseEvent, datetime] | None
        ] = {}
        for definition in definitions:
            start_anchor_key = self._anchor_cache_key(definition.start_anchor)
            if start_anchor_key not in start_match_cache:
                start_match_cache[start_anchor_key] = self._find_matching_event(
                    events, definition.start_anchor
                )
            start_match = start_match_cache[start_anchor_key]

            earliest_after = start_match[1] if start_match else None
            end_anchor_key = (
                self._anchor_cache_key(definition.end_anchor),
                earliest_after,
            )
            if end_anchor_key not in end_match_cache:
                end_match_cache[end_anchor_key] = self._find_matching_event(
                    events,
                    definition.end_anchor,
                    earliest_after=earliest_after,
                )
            end_match = end_match_cache[end_anchor_key]

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

    def _compute_durations_by_case(
        self,
        events_by_case: dict[uuid.UUID, list[CaseEvent]],
        definitions: Sequence[CaseDurationDefinitionRead],
    ) -> dict[uuid.UUID, list[CaseDurationComputation]]:
        return {
            case_id: self._compute_durations_from_events(events, definitions)
            for case_id, events in events_by_case.items()
        }

    async def _compute_durations_from_db(
        self,
        case: Case,
        definitions: Sequence[CaseDurationDefinitionRead],
    ) -> list[CaseDurationComputation]:
        start_match_cache: dict[tuple[Any, ...], tuple[CaseEvent, datetime] | None] = {}
        end_match_cache: dict[
            tuple[tuple[Any, ...], datetime | None], tuple[CaseEvent, datetime] | None
        ] = {}
        results: list[CaseDurationComputation] = []

        # Most iterations await on DB I/O, but periodic checkpoints keep
        # cache-hit-heavy definition sets from monopolizing the event loop.
        async for definition in cooperative_every(definitions, every=64):
            start_anchor_key = self._sql_anchor_cache_key(definition.start_anchor)
            if start_anchor_key not in start_match_cache:
                start_match_cache[
                    start_anchor_key
                ] = await self._find_matching_event_db(
                    case.id,
                    definition.start_anchor,
                )
            start_match = start_match_cache[start_anchor_key]

            earliest_after = start_match[1] if start_match else None
            end_anchor_base_key = self._sql_anchor_cache_key(definition.end_anchor)
            end_anchor_key = (end_anchor_base_key, earliest_after)
            if end_anchor_key not in end_match_cache:
                end_match_cache[end_anchor_key] = await self._find_matching_event_db(
                    case.id,
                    definition.end_anchor,
                    earliest_after=earliest_after,
                )
            end_match = end_match_cache[end_anchor_key]

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

    def _find_matching_event(
        self,
        events: Sequence[CaseEvent],
        anchor: CaseDurationEventAnchor,
        *,
        earliest_after: datetime | None = None,
    ) -> tuple[CaseEvent, datetime] | None:
        best_match: tuple[CaseEvent, datetime] | None = None
        for event in events:
            # A transition to `closed` is emitted as `case_closed` (and reopening
            # as `case_reopened`), so exact matching on `status_changed` would
            # otherwise miss those transitions. Expand the match here so duration
            # definitions filtered on `status_changed` still see close/reopen.
            if anchor.event_type is CaseEventType.STATUS_CHANGED:
                if event.type not in (
                    CaseEventType.STATUS_CHANGED,
                    CaseEventType.CASE_CLOSED,
                    CaseEventType.CASE_REOPENED,
                ):
                    continue
            elif event.type != anchor.event_type:
                continue
            if not self._matches_anchor_filters(event, anchor):
                continue
            timestamp = self._extract_timestamp(event, anchor)
            if timestamp is None:
                continue
            if earliest_after and timestamp < earliest_after:
                continue
            if best_match is None:
                best_match = (event, timestamp)
                continue
            if anchor.selection is CaseDurationAnchorSelection.LAST:
                if timestamp >= best_match[1]:
                    best_match = (event, timestamp)
            elif timestamp < best_match[1]:
                best_match = (event, timestamp)

        return best_match

    async def _find_matching_event_db(
        self,
        case_id: uuid.UUID,
        anchor: CaseDurationEventAnchor,
        *,
        earliest_after: datetime | None = None,
    ) -> tuple[CaseEvent, datetime] | None:
        conditions: list[ColumnElement[bool]] = [
            CaseEvent.workspace_id == self.workspace_id,
            CaseEvent.case_id == case_id,
        ]

        if anchor.event_type is CaseEventType.STATUS_CHANGED:
            conditions.append(
                CaseEvent.type.in_(
                    (
                        CaseEventType.STATUS_CHANGED,
                        CaseEventType.CASE_CLOSED,
                        CaseEventType.CASE_REOPENED,
                    )
                )
            )
        else:
            conditions.append(CaseEvent.type == anchor.event_type)

        if earliest_after is not None:
            conditions.append(CaseEvent.created_at >= earliest_after)

        conditions.extend(self._build_sql_filter_conditions(anchor))

        order_by = (
            (CaseEvent.created_at.desc(), CaseEvent.surrogate_id.desc())
            if anchor.selection is CaseDurationAnchorSelection.LAST
            else (CaseEvent.created_at.asc(), CaseEvent.surrogate_id.asc())
        )
        stmt = select(CaseEvent).where(*conditions).order_by(*order_by).limit(1)
        result = await self.session.execute(stmt)
        if (event := result.scalars().first()) is None:
            return None
        return event, event.created_at

    def _sql_anchor_cache_key(self, anchor: CaseDurationEventAnchor) -> tuple[Any, ...]:
        return self._anchor_cache_key(anchor)

    def _build_sql_filter_conditions(
        self, anchor: CaseDurationEventAnchor
    ) -> list[ColumnElement[bool]]:
        filters = anchor.filters
        conditions: list[ColumnElement[bool]] = []
        if anchor._has_unsupported_filters:
            conditions.append(false())
            return conditions
        if filters.new_values:
            conditions.append(
                self._build_jsonb_list_filter(CaseEvent.data["new"], filters.new_values)
            )
        if filters.tag_refs:
            conditions.append(
                self._build_jsonb_list_filter(
                    CaseEvent.data["tag_ref"], filters.tag_refs
                )
            )
        if filters.field_ids:
            conditions.append(self._build_jsonb_change_field_filter(filters.field_ids))
        if filters.dropdown_definition_id is not None:
            conditions.append(
                CaseEvent.data["definition_id"]
                == self._jsonb_bindparam(filters.dropdown_definition_id)
            )
        if filters.dropdown_option_ids:
            conditions.append(
                self._build_jsonb_list_filter(
                    CaseEvent.data["new_option_id"], filters.dropdown_option_ids
                )
            )
        return conditions

    def _build_jsonb_list_filter(
        self, value_expr: ColumnElement[Any], expected: list[Any]
    ) -> ColumnElement[bool]:
        if not expected:
            return false()

        scalar_expected = [
            self._jsonb_bindparam(item) for item in expected if item is not None
        ]
        scalar_matches = value_expr.in_(scalar_expected) if scalar_expected else false()

        array_value = sql_case(
            (func.jsonb_typeof(value_expr) == "array", value_expr),
            else_=literal([], type_=JSONB),
        )
        elem = (
            func.jsonb_array_elements(array_value)
            .table_valued(column("value", JSONB))
            .alias("filter_elem")
        )
        array_matches = (
            select(literal(True))
            .select_from(elem)
            .where(elem.c.value.in_([self._jsonb_bindparam(item) for item in expected]))
            .correlate(CaseEvent)
            .exists()
        )
        return or_(scalar_matches, array_matches)

    def _build_jsonb_change_field_filter(
        self, field_ids: list[str]
    ) -> ColumnElement[bool]:
        if not field_ids:
            return false()

        changes_value = sql_case(
            (
                func.jsonb_typeof(CaseEvent.data["changes"]) == "array",
                CaseEvent.data["changes"],
            ),
            else_=literal([], type_=JSONB),
        )
        elem = (
            func.jsonb_array_elements(changes_value)
            .table_valued(column("value", JSONB))
            .alias("change_elem")
        )
        return (
            select(literal(True))
            .select_from(elem)
            .where(
                elem.c.value["field"].in_(
                    [self._jsonb_bindparam(field_id) for field_id in field_ids]
                )
            )
            .correlate(CaseEvent)
            .exists()
        )

    def _jsonb_bindparam(self, value: Any) -> ColumnElement[Any]:
        return bindparam(None, value, type_=JSONB)

    def _anchor_cache_key(self, anchor: CaseDurationEventAnchor) -> tuple[Any, ...]:
        filter_items = self._freeze_filter_value(
            anchor.filters.model_dump(exclude_defaults=True)
        )
        return (
            anchor.event_type,
            anchor.selection,
            filter_items,
            anchor._has_unsupported_filters,
        )

    def _freeze_filter_value(self, value: Any) -> Any:
        if isinstance(value, Enum):
            return value.value
        if isinstance(value, list | tuple | set):
            return tuple(self._freeze_filter_value(item) for item in value)
        if isinstance(value, dict):
            return tuple(
                sorted(
                    (key, self._freeze_filter_value(item))
                    for key, item in value.items()
                )
            )
        return value

    def _matches_anchor_filters(
        self, event: CaseEvent, anchor: CaseDurationEventAnchor
    ) -> bool:
        if anchor._has_unsupported_filters:
            return False

        filters = anchor.filters
        data = event.data or {}
        if filters.new_values and data.get("new") not in filters.new_values:
            return False
        if filters.tag_refs and data.get("tag_ref") not in filters.tag_refs:
            return False
        if filters.field_ids and not self._event_changed_any_field(
            event, filters.field_ids
        ):
            return False
        if (
            filters.dropdown_definition_id is not None
            and data.get("definition_id") != filters.dropdown_definition_id
        ):
            return False
        if (
            filters.dropdown_option_ids
            and data.get("new_option_id") not in filters.dropdown_option_ids
        ):
            return False
        return True

    def _event_changed_any_field(self, event: CaseEvent, field_ids: list[str]) -> bool:
        changes = (event.data or {}).get("changes")
        if not isinstance(changes, list):
            return False
        return any(
            isinstance(change, dict) and change.get("field") in field_ids
            for change in changes
        )

    def _extract_timestamp(
        self, event: CaseEvent, anchor: CaseDurationEventAnchor
    ) -> datetime | None:
        del anchor
        return event.created_at

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
