"""Canonical case query preparation, cursor execution, and count semantics."""

from __future__ import annotations

import re
import uuid
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import sqlalchemy as sa
from sqlalchemy import cast, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import load_only, raiseload, selectinload
from sqlalchemy.sql.base import ExecutableOption
from sqlalchemy.sql.elements import ColumnElement

from tracecat.auth.schemas import UserRead
from tracecat.auth.types import Role
from tracecat.cases.dropdowns.schemas import CaseDropdownValueRead
from tracecat.cases.durations.schemas import CaseDurationRead
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus, CaseTaskStatus
from tracecat.cases.hierarchy import TOP_LEVEL, CaseHierarchyService
from tracecat.cases.query import (
    CUSTOM_FIELD_PREFIX,
    CaseFieldResolver,
    case_payload_projection,
)
from tracecat.cases.rows.hydration import CaseRowHydrator
from tracecat.cases.schemas import (
    CaseReadMinimal,
    CaseSearchAggregateRead,
    CaseStatusGroupCounts,
)
from tracecat.cases.search_adapters import (
    iter_case_filter_conditions,
    legacy_case_filters,
)
from tracecat.cases.search_metadata import validate_case_filter_metadata
from tracecat.cases.search_schemas import CaseQuery, CaseSearchRequest
from tracecat.cases.sorting import case_relationship_sort_key
from tracecat.cases.tags.schemas import CaseTagRead
from tracecat.cases.tags.search import resolve_case_tag_filters
from tracecat.db.models import Case, CaseDropdownValue, CaseTableRow, CaseTag, CaseTask
from tracecat.exceptions import TracecatValidationError
from tracecat.pagination import CursorPaginatedResponse, PageParams, paginate
from tracecat.query.compiler import compile_filter
from tracecat.query.filters import Filter
from tracecat.service import BaseWorkspaceService

if TYPE_CHECKING:
    from tracecat.cases.service import CaseFieldsService

# Semantic sort order for enum-backed case fields.
CASE_PRIORITY_SORT_ORDER = tuple(priority.value for priority in CasePriority)
CASE_SEVERITY_SORT_ORDER = tuple(severity.value for severity in CaseSeverity)
CASE_STATUS_SORT_ORDER = tuple(status.value for status in CaseStatus)
MAX_CASE_NUMBER = 2**31 - 1
SHORT_ID_PATTERN = re.compile(r"^(?:CASE-)?(\d{1,10})$", re.IGNORECASE)


def parse_case_short_id(value: str) -> int | None:
    """Return the case number for a short ID like ``CASE-0042`` or ``42``."""
    if match := SHORT_ID_PATTERN.match(value.strip()):
        return int(match.group(1))
    return None


def _enum_sort_expr(column: Any, ordered_values: Sequence[str]) -> ColumnElement[int]:
    """Build a SQL expression that sorts enum/text values by semantic order."""
    return sa.case(
        *[
            (column == enum_value, index)
            for index, enum_value in enumerate(ordered_values)
        ],
        else_=len(ordered_values),
    )


def case_identity_predicates(query: CaseQuery) -> list[ColumnElement[bool]]:
    """Build literal text and exact-ID predicates independent of boolean filters."""
    filters: list[ColumnElement[bool]] = []
    if query.case_id is not None:
        filters.append(Case.id == query.case_id)
    if query.search_term:
        if len(query.search_term) > 1000:
            raise ValueError("Search term cannot exceed 1000 characters")
        if "\x00" in query.search_term:
            raise ValueError("Search term cannot contain null bytes")

        case_number_text = cast(Case.case_number, sa.String)
        short_id_expr = func.concat(
            "CASE-",
            func.lpad(
                case_number_text,
                func.greatest(4, func.length(case_number_text)),
                "0",
            ),
        )
        search_conditions: list[Any] = [
            Case.summary.icontains(query.search_term, autoescape=True, escape="\\"),
            Case.description.icontains(query.search_term, autoescape=True, escape="\\"),
            short_id_expr.icontains(query.search_term, autoescape=True, escape="\\"),
        ]
        case_number = parse_case_short_id(query.search_term)
        if case_number is not None and case_number <= MAX_CASE_NUMBER:
            search_conditions.append(Case.case_number == case_number)
        try:
            search_conditions.append(Case.id == uuid.UUID(query.search_term.strip()))
        except ValueError:
            pass
        filters.append(or_(*search_conditions))

    if query.short_id:
        normalized_short_id = query.short_id.strip().upper()
        if len(normalized_short_id) > 1000:
            raise ValueError("Short ID cannot exceed 1000 characters")
        if "\x00" in normalized_short_id:
            raise ValueError("Short ID cannot contain null bytes")

        match = SHORT_ID_PATTERN.match(normalized_short_id)
        if not match:
            raise ValueError("Short ID must match CASE-<number> or <number>")

        short_id_number = int(match.group(1))
        if short_id_number > MAX_CASE_NUMBER:
            raise ValueError(f"Short ID number cannot exceed {MAX_CASE_NUMBER}")
        filters.append(Case.case_number == short_id_number)

    return filters


class CaseSearchService(BaseWorkspaceService):
    """Execute validated case queries with one compiler and page hydrator."""

    service_name = "case_search"

    def __init__(
        self,
        session: AsyncSession,
        role: Role,
        *,
        fields: CaseFieldsService,
        hierarchy: CaseHierarchyService,
    ):
        super().__init__(session, role)
        self.fields = fields
        self.hierarchy = hierarchy

    async def legacy_tag_refs(
        self, tag_ids: Sequence[uuid.UUID] | None
    ) -> list[str] | None:
        """Resolve every legacy tag ID within the authorized workspace."""
        if not tag_ids:
            return None
        rows = (
            (
                await self.session.execute(
                    select(CaseTag.id, CaseTag.ref).where(
                        CaseTag.workspace_id == self.workspace_id,
                        CaseTag.id.in_(tag_ids),
                    )
                )
            )
            .tuples()
            .all()
        )
        if set(tag_ids) - {row[0] for row in rows}:
            raise TracecatValidationError("Unknown case tag filter")
        return [row[1] for row in rows]

    async def compatibility_filters(self, query: CaseQuery) -> Filter | None:
        """Translate shipped matching inputs separately from the authored tree."""
        tags = await resolve_case_tag_filters(
            session=self.session, workspace_id=self.workspace_id, tags=query.tags
        )
        dropdowns: dict[str, list[str]] = {}
        for entry in query.dropdown or []:
            if ":" not in entry:
                raise TracecatValidationError(
                    f"Invalid dropdown filter format: {entry!r}. Expected 'definition_ref:option_ref'."
                )
            ref, option = entry.split(":", 1)
            dropdowns.setdefault(ref, []).append(option)
        assignees = query.assignee_id or []
        return legacy_case_filters(
            status=query.status,
            priority=query.priority,
            severity=query.severity,
            assignee_ids=[value for value in assignees if isinstance(value, uuid.UUID)],
            include_unassigned=any(isinstance(value, str) for value in assignees),
            tag_refs=[tag.ref for tag in tags],
            dropdown_filters=dropdowns,
            start_time=query.start_time,
            end_time=query.end_time,
            updated_after=query.updated_after,
            updated_before=query.updated_before,
        )

    async def prepare_resolver(
        self,
        trees: Sequence[Filter | None],
        *,
        case_addons_enabled: bool,
        extra_fields: Sequence[str] = (),
    ) -> CaseFieldResolver:
        """Validate all leaves and load custom schema only when referenced."""
        dropdown_refs = await validate_case_filter_metadata(
            session=self.session,
            workspace_id=self.workspace_id,
            filters=trees,
            case_addons_enabled=case_addons_enabled,
            extra_dropdown_refs=[
                field.removeprefix("dropdowns.")
                for field in extra_fields
                if field.startswith("dropdowns.")
            ],
        )
        referenced_fields = [
            leaf.field for tree in trees for leaf in iter_case_filter_conditions(tree)
        ]
        schema = (
            await self.fields.get_field_schema()
            if any(
                field.startswith(CUSTOM_FIELD_PREFIX)
                for field in (*referenced_fields, *extra_fields)
            )
            else {}
        )
        return CaseFieldResolver(self.workspace_id, schema, dropdown_refs)

    async def prepare_query(
        self,
        query: CaseQuery,
        *,
        case_addons_enabled: bool,
        legacy_filters: Filter | None = None,
        linked_row: tuple[uuid.UUID, uuid.UUID] | None = None,
        exclude_case_id: uuid.UUID | None = None,
    ) -> list[ColumnElement[bool]]:
        """Keep trusted scope and compatibility constraints outside the user tree."""
        compatibility = await self.compatibility_filters(query)
        trees = (query.filters, compatibility, legacy_filters)
        resolver = await self.prepare_resolver(
            trees, case_addons_enabled=case_addons_enabled
        )
        predicates = [
            Case.workspace_id == self.workspace_id,
            *case_identity_predicates(query),
        ]
        predicates.extend(
            compile_filter(tree, resolver) for tree in trees if tree is not None
        )
        parent_id, top_level_only = await self.hierarchy.resolve_filters(
            parent_id=query.parent_id, hierarchy=query.hierarchy
        )
        if parent_id is not None:
            predicates.append(Case.parent_id == parent_id)
        elif top_level_only:
            predicates.append(Case.parent_id.is_(None))
        if linked_row is not None:
            table_id, row_id = linked_row
            predicates.append(
                Case.id.in_(
                    select(CaseTableRow.case_id).where(
                        CaseTableRow.workspace_id == self.workspace_id,
                        CaseTableRow.table_id == table_id,
                        CaseTableRow.row_id == row_id,
                    )
                )
            )
        if exclude_case_id is not None:
            predicates.append(Case.id != exclude_case_id)
        return predicates

    async def query_cases(
        self,
        request: CaseSearchRequest,
        *,
        case_addons_enabled: bool,
        legacy_filters: Filter | None = None,
        linked_row: tuple[uuid.UUID, uuid.UUID] | None = None,
        exclude_case_id: uuid.UUID | None = None,
    ) -> CursorPaginatedResponse[CaseReadMinimal]:
        """Paginate once, then hydrate only the selected page."""
        payload_projection = (
            case_payload_projection(
                request.payload_fields or (), request.payload_paths or ()
            )
            if request.payload_fields or request.payload_paths
            else None
        )
        filters = await self.prepare_query(
            request,
            case_addons_enabled=case_addons_enabled,
            legacy_filters=legacy_filters,
            linked_row=linked_row,
            exclude_case_id=exclude_case_id,
        )
        include_case_addons = case_addons_enabled
        # Keep pagination independent of detail flags and exclude large text /
        # JSON columns. Wildcard raiseload suppresses model-default selectin
        # relationships, including table_rows and unrelated nested collections.
        stmt = (
            select(Case)
            .where(Case.workspace_id == self.workspace_id)
            .options(
                load_only(
                    Case.id,
                    Case.workspace_id,
                    Case.case_number,
                    Case.created_at,
                    Case.updated_at,
                    Case.summary,
                    Case.status,
                    Case.priority,
                    Case.severity,
                    Case.assignee_id,
                    Case.parent_id,
                    raiseload=True,
                ),
                raiseload("*"),
            )
        )

        for clause in filters:
            stmt = stmt.where(clause)

        total_estimate: int | None = None
        if request.include_count:
            # Preserve the legacy exact filtered count. The UI can use the
            # aggregate endpoint as its sole population-count source instead.
            count_stmt = select(func.count()).select_from(Case).where(*filters)
            total_count = await self.session.scalar(count_stmt)
            total_estimate = int(total_count or 0)

        # Determine sort column and direction
        sort_column = request.order_by or "created_at"
        sort_direction = request.sort or "desc"

        # Map computed properties to their underlying columns
        if sort_column == "short_id":
            sort_column = "case_number"

        # Validate and get sort attribute
        # For "tasks", use a correlated subquery; for enum fields, sort by semantic rank.
        task_count_expr: ColumnElement[int] | None = None
        enum_sort_values: Sequence[str] | None = None
        if sort_column == "tasks":
            task_count_expr = func.coalesce(
                select(func.count())
                .where(CaseTask.case_id == Case.id)
                .correlate(Case)
                .scalar_subquery(),
                0,
            )
            sort_attr = task_count_expr
        elif sort_column == "priority":
            enum_sort_values = CASE_PRIORITY_SORT_ORDER
            sort_attr = _enum_sort_expr(Case.priority, enum_sort_values)
        elif sort_column == "severity":
            enum_sort_values = CASE_SEVERITY_SORT_ORDER
            sort_attr = _enum_sort_expr(Case.severity, enum_sort_values)
        elif sort_column == "status":
            enum_sort_values = CASE_STATUS_SORT_ORDER
            sort_attr = _enum_sort_expr(Case.status, enum_sort_values)
        elif sort_column in ("assignee", "tags"):
            sort_attr = case_relationship_sort_key(self.workspace_id, sort_column)
        else:
            sort_attr = {
                "created_at": Case.created_at,
                "updated_at": Case.updated_at,
                "id": Case.id,
                "case_number": Case.case_number,
            }[sort_column]

        # The paginator owns cursor validation, seek predicates, scan direction,
        # and reconstruction of backward pages. ``reverse`` remains accepted at
        # the service boundary for compatibility; cursor direction is encoded in
        # the opaque continuation token.
        if sort_column == "id":
            if sort_direction == "asc":
                ordering = (sort_attr.asc(),)
            else:
                ordering = (sort_attr.desc(),)
        else:
            if sort_direction == "asc":
                ordering = (sort_attr.asc().nulls_last(), Case.id.asc())
            else:
                ordering = (sort_attr.desc().nulls_last(), Case.id.desc())

        case_page = await paginate(
            self.session,
            stmt,
            page=PageParams(limit=request.limit, cursor=request.cursor),
            order_by=ordering,
        )
        cases = case_page.items

        hydration_options: list[ExecutableOption] = [
            load_only(Case.id, Case.payload, raiseload=True)
            if request.include_payload and payload_projection is None
            else load_only(Case.id, raiseload=True),
            raiseload("*"),
            selectinload(Case.tags),
            selectinload(Case.assignee),
        ]
        if include_case_addons:
            hydration_options.extend(
                [
                    selectinload(Case.dropdown_values).selectinload(
                        CaseDropdownValue.definition
                    ),
                    selectinload(Case.dropdown_values).selectinload(
                        CaseDropdownValue.option
                    ),
                ]
            )
            if request.include_durations:
                hydration_options.append(selectinload(Case.durations))
        selected_payloads: dict[uuid.UUID, dict[str, Any] | None] = {}
        if cases:
            # SQLAlchemy's identity map hydrates the already-paginated objects
            # without replacing their order or loading the lookahead case.
            hydration_stmt = (
                select(Case)
                .where(
                    Case.workspace_id == self.workspace_id,
                    Case.id.in_([case.id for case in cases]),
                )
                .options(*hydration_options)
            )
            if payload_projection is not None:
                # Keep projected values separate from Case.payload so a partial
                # read never replaces the full object in the session identity map.
                selected_stmt = hydration_stmt.add_columns(
                    payload_projection.label("selected_payload")
                )
                hydrated_rows = (
                    (await self.session.execute(selected_stmt)).tuples().all()
                )
                selected_payloads = {
                    case.id: payload for case, payload in hydrated_rows
                }
                hydrated_ids = set(selected_payloads)
            else:
                hydrated = (await self.session.scalars(hydration_stmt)).all()
                hydrated_ids = {case.id for case in hydrated}
            # A deletion can occur between statement snapshots. Omit missing
            # rows instead of accessing relationships that were never loaded.
            cases = [case for case in cases if case.id in hydrated_ids]

        # Fetch task counts and sub-case relationships for response hydration.
        task_counts = await self.get_task_counts([case.id for case in cases])
        hierarchies = await self.hierarchy.get_hierarchy(cases)

        # Convert to CaseReadMinimal objects with tags and dropdown values
        case_items = []
        for case in cases:
            # Tags are already loaded via selectinload
            tag_reads = [
                CaseTagRead.model_validate(tag, from_attributes=True)
                for tag in case.tags
            ]

            dropdown_reads = []
            duration_reads: list[CaseDurationRead] | None = None
            if include_case_addons:
                # Dropdown values are already loaded via selectinload
                dropdown_reads = [
                    CaseDropdownValueRead(
                        id=dv.id,
                        definition_id=dv.definition_id,
                        definition_ref=dv.definition.ref,
                        definition_name=dv.definition.name,
                        option_id=dv.option.id if dv.option else None,
                        option_label=dv.option.label if dv.option else None,
                        option_ref=dv.option.ref if dv.option else None,
                        option_icon_name=dv.option.icon_name if dv.option else None,
                        option_color=dv.option.color if dv.option else None,
                    )
                    for dv in case.dropdown_values
                ]
            if include_case_addons and request.include_durations and case.durations:
                # Durations are already loaded via selectinload
                duration_reads = [
                    CaseDurationRead.model_validate(d, from_attributes=True)
                    for d in case.durations
                ]

            payload: dict[str, Any] | None = None
            if payload_projection is not None:
                payload = selected_payloads[case.id]
            elif request.include_payload:
                payload = case.payload

            case_items.append(
                CaseReadMinimal(
                    id=case.id,
                    created_at=case.created_at,
                    updated_at=case.updated_at,
                    short_id=case.short_id,
                    summary=case.summary,
                    status=case.status,
                    priority=case.priority,
                    severity=case.severity,
                    assignee=UserRead.model_validate(
                        case.assignee, from_attributes=True
                    )
                    if case.assignee
                    else None,
                    tags=tag_reads,
                    dropdown_values=dropdown_reads,
                    durations=duration_reads,
                    payload=payload,
                    num_tasks_completed=task_counts[case.id]["completed"],
                    num_tasks_total=task_counts[case.id]["total"],
                    parent=hierarchies.get(case.id, TOP_LEVEL).parent,
                    num_sub_cases=hierarchies.get(case.id, TOP_LEVEL).num_sub_cases,
                )
            )

        if request.include_rows:
            rows = await CaseRowHydrator(self.session, self.role).hydrate_case_rows(
                case_ids=[item.id for item in case_items], include_row_data=True
            )
            case_items = [
                item.model_copy(update={"rows": rows.get(item.id, [])})
                for item in case_items
            ]
        if request.field_ids:
            values = await self.fields.batch_get_fields(
                case_ids=[item.id for item in case_items], field_ids=request.field_ids
            )
            case_items = [
                item.model_copy(update={"field_values": values.get(item.id)})
                for item in case_items
            ]

        return CursorPaginatedResponse(
            items=case_items,
            next_cursor=case_page.next_cursor,
            prev_cursor=case_page.prev_cursor,
            has_more=case_page.has_more,
            has_previous=case_page.has_previous,
            total_estimate=total_estimate,
        )

    async def get_task_counts(
        self, case_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, dict[str, int]]:
        """Get task counts (completed/total) for cases."""
        if not case_ids:
            return {}

        stmt = (
            select(
                CaseTask.case_id,
                func.count().label("total"),
                func.sum(
                    sa.case((CaseTask.status == CaseTaskStatus.COMPLETED, 1), else_=0)
                ).label("completed"),
            )
            .where(CaseTask.case_id.in_(case_ids))
            .group_by(CaseTask.case_id)
        )

        result = await self.session.execute(stmt)
        rows = result.tuples().all()

        # Build result dict with defaults for cases without tasks
        counts = {case_id: {"completed": 0, "total": 0} for case_id in case_ids}
        for case_id, total, completed in rows:
            counts[case_id] = {
                "completed": int(completed or 0),
                "total": int(total or 0),
            }

        return counts

    async def query_aggregates(
        self,
        query: CaseQuery,
        *,
        case_addons_enabled: bool,
        legacy_filters: Filter | None = None,
    ) -> CaseSearchAggregateRead:
        """Return exact status totals using the same matching predicates as rows."""
        filters = await self.prepare_query(
            query,
            case_addons_enabled=case_addons_enabled,
            legacy_filters=legacy_filters,
        )
        aggregate_stmt = select(
            func.count(Case.id).label("total"),
            func.coalesce(
                func.sum(sa.case((Case.status == CaseStatus.NEW, 1), else_=0)),
                0,
            ).label("new"),
            func.coalesce(
                func.sum(sa.case((Case.status == CaseStatus.IN_PROGRESS, 1), else_=0)),
                0,
            ).label("in_progress"),
            func.coalesce(
                func.sum(sa.case((Case.status == CaseStatus.ON_HOLD, 1), else_=0)),
                0,
            ).label("on_hold"),
            func.coalesce(
                func.sum(sa.case((Case.status == CaseStatus.RESOLVED, 1), else_=0)),
                0,
            ).label("resolved"),
            func.coalesce(
                func.sum(sa.case((Case.status == CaseStatus.CLOSED, 1), else_=0)),
                0,
            ).label("closed"),
            func.coalesce(
                func.sum(sa.case((Case.status == CaseStatus.UNKNOWN, 1), else_=0)),
                0,
            ).label("unknown"),
            func.coalesce(
                func.sum(
                    sa.case(
                        (Case.status == CaseStatus.OTHER, 1),
                        else_=0,
                    )
                ),
                0,
            ).label("other"),
        )

        for clause in filters:
            aggregate_stmt = aggregate_stmt.where(clause)

        row = (await self.session.execute(aggregate_stmt)).one()

        return CaseSearchAggregateRead(
            total=int(row.total or 0),
            status_groups=CaseStatusGroupCounts(
                new=int(row.new or 0),
                in_progress=int(row.in_progress or 0),
                on_hold=int(row.on_hold or 0),
                resolved=int(row.resolved or 0),
                closed=int(row.closed or 0),
                unknown=int(row.unknown or 0),
                other=int(row.other or 0),
            ),
        )
