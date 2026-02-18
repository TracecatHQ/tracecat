import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import sqlalchemy as sa
from asyncpg import UndefinedColumnError
from pydantic import ValidationError
from sqlalchemy import and_, cast, func, or_, select
from sqlalchemy.dialects.postgresql import UUID, insert
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from sqlalchemy.orm.attributes import flag_modified
from sqlalchemy.sql.elements import ColumnElement

from tracecat.audit.logger import audit_log
from tracecat.auth.schemas import UserRead
from tracecat.auth.types import Role
from tracecat.cases.attachments import CaseAttachmentService
from tracecat.cases.dropdowns.schemas import CaseDropdownValueRead
from tracecat.cases.durations.service import CaseDurationService
from tracecat.cases.enums import (
    CaseEventType,
    CasePriority,
    CaseSeverity,
    CaseStatus,
    CaseTaskStatus,
)
from tracecat.cases.schemas import (
    AssigneeChangedEvent,
    CaseCommentCreate,
    CaseCommentUpdate,
    CaseCreate,
    CaseEventVariant,
    CaseReadMinimal,
    CaseTaskCreate,
    CaseTaskUpdate,
    CaseUpdate,
    CaseViewedEvent,
    ClosedEvent,
    CreatedEvent,
    FieldDiff,
    FieldsChangedEvent,
    PayloadChangedEvent,
    PriorityChangedEvent,
    ReopenedEvent,
    SeverityChangedEvent,
    StatusChangedEvent,
    TaskAssigneeChangedEvent,
    TaskCreatedEvent,
    TaskDeletedEvent,
    TaskPriorityChangedEvent,
    TaskStatusChangedEvent,
    TaskWorkflowChangedEvent,
    UpdatedEvent,
)
from tracecat.cases.tags.schemas import CaseTagRead
from tracecat.cases.tags.service import CaseTagsService
from tracecat.cases.triggers.publisher import publish_case_event_payload
from tracecat.contexts import ctx_run
from tracecat.custom_fields import CustomFieldsService
from tracecat.custom_fields.schemas import CustomFieldCreate, CustomFieldUpdate
from tracecat.db.models import (
    Case,
    CaseComment,
    CaseDropdownDefinition,
    CaseDropdownOption,
    CaseDropdownValue,
    CaseEvent,
    CaseFields,
    CaseTagLink,
    CaseTask,
    User,
    Workflow,
)
from tracecat.db.session_events import add_after_commit_callback
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatException,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.expressions.expectations import (
    ExpectedField,
    create_expectation_model,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.pagination import (
    BaseCursorPaginator,
    CursorPaginatedResponse,
    CursorPaginationParams,
)
from tracecat.service import BaseWorkspaceService
from tracecat.tables.common import normalize_column_options
from tracecat.tables.enums import SqlType
from tracecat.tables.service import TablesService


def _normalize_filter_values(values: Any) -> list[Any]:
    """Ensure filter inputs are lists of unique values."""
    if values is None:
        return []
    if isinstance(values, str | bytes):
        return [values]
    if isinstance(values, Sequence):
        unique: list[Any] = []
        for value in values:
            if value not in unique:
                unique.append(value)
        return unique
    return [values]


# Semantic sort order for enum-backed case fields.
CASE_PRIORITY_SORT_ORDER = tuple(priority.value for priority in CasePriority)
CASE_SEVERITY_SORT_ORDER = tuple(severity.value for severity in CaseSeverity)
CASE_STATUS_SORT_ORDER = tuple(status.value for status in CaseStatus)


def _enum_sort_expr(column: Any, ordered_values: Sequence[str]) -> ColumnElement[int]:
    """Build a SQL expression that sorts enum/text values by semantic order."""
    return sa.case(
        *[
            (column == enum_value, index)
            for index, enum_value in enumerate(ordered_values)
        ],
        else_=len(ordered_values),
    )


def _enum_sort_rank(value: Any, ordered_values: Sequence[str]) -> int:
    """Map enum/text values to their semantic sort rank."""
    normalized_value = getattr(value, "value", value)
    if not isinstance(normalized_value, str):
        return len(ordered_values)
    try:
        return ordered_values.index(normalized_value)
    except ValueError:
        return len(ordered_values)


# Treat multiple views inside this window as a single "view" to avoid spam.
CASE_VIEW_EVENT_DEDUP_WINDOW = timedelta(minutes=5)


class CasesService(BaseWorkspaceService):
    service_name = "cases"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)
        self.tables = TablesService(session=self.session, role=self.role)
        self.fields = CaseFieldsService(session=self.session, role=self.role)
        self.events = CaseEventsService(session=self.session, role=self.role)
        self.attachments = CaseAttachmentService(session=self.session, role=self.role)
        self.tags = CaseTagsService(session=self.session, role=self.role)

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

    async def search_cases(
        self,
        params: CursorPaginationParams,
        search_term: str | None = None,
        status: CaseStatus | Sequence[CaseStatus] | None = None,
        priority: CasePriority | Sequence[CasePriority] | None = None,
        severity: CaseSeverity | Sequence[CaseSeverity] | None = None,
        assignee_ids: Sequence[uuid.UUID] | None = None,
        include_unassigned: bool = False,
        tag_ids: list[uuid.UUID] | None = None,
        dropdown_filters: dict[str, list[str]] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        updated_before: datetime | None = None,
        updated_after: datetime | None = None,
        order_by: Literal[
            "created_at", "updated_at", "priority", "severity", "status", "tasks"
        ]
        | None = None,
        sort: Literal["asc", "desc"] | None = None,
    ) -> CursorPaginatedResponse[CaseReadMinimal]:
        """Search cases with cursor-based pagination and filtering."""
        paginator = BaseCursorPaginator(self.session)
        filters: list[Any] = [Case.workspace_id == self.workspace_id]

        # Base query - eagerly load tags, assignee, and dropdown values
        stmt = (
            select(Case)
            .options(selectinload(Case.tags))
            .options(selectinload(Case.assignee))
            .options(
                selectinload(Case.dropdown_values).selectinload(
                    CaseDropdownValue.definition
                )
            )
            .options(
                selectinload(Case.dropdown_values).selectinload(
                    CaseDropdownValue.option
                )
            )
        )

        # Apply search term filter
        if search_term:
            # Validate search term to prevent abuse
            if len(search_term) > 1000:
                raise ValueError("Search term cannot exceed 1000 characters")
            if "\x00" in search_term:
                raise ValueError("Search term cannot contain null bytes")

            # Use SQLAlchemy's concat function for proper parameter binding
            search_pattern = func.concat("%", search_term, "%")
            # Build short_id expression in SQL: 'CASE-' + lpad(case_number, 4, '0')
            short_id_expr = func.concat(
                "CASE-", func.lpad(cast(Case.case_number, sa.String), 4, "0")
            )
            filters.append(
                or_(
                    Case.summary.ilike(search_pattern),
                    Case.description.ilike(search_pattern),
                    short_id_expr.ilike(search_pattern),
                )
            )

        normalized_statuses = _normalize_filter_values(status)
        if normalized_statuses:
            filters.append(Case.status.in_(normalized_statuses))

        # Apply priority filter
        normalized_priorities = _normalize_filter_values(priority)
        if normalized_priorities:
            filters.append(Case.priority.in_(normalized_priorities))

        # Apply severity filter
        normalized_severities = _normalize_filter_values(severity)
        if normalized_severities:
            filters.append(Case.severity.in_(normalized_severities))

        # Apply assignee filter
        if include_unassigned or assignee_ids:
            unique_assignees = list(dict.fromkeys(assignee_ids or []))
            assignee_conditions: list[Any] = []

            if unique_assignees:
                assignee_conditions.append(Case.assignee_id.in_(unique_assignees))

            if include_unassigned:
                assignee_conditions.append(Case.assignee_id.is_(None))

            if assignee_conditions:
                assignee_clause = (
                    assignee_conditions[0]
                    if len(assignee_conditions) == 1
                    else or_(*assignee_conditions)
                )
                filters.append(assignee_clause)

        # Apply tag filtering if tag_ids provided (AND logic - case must have all tags)
        if tag_ids:
            for tag_id in tag_ids:
                filters.append(
                    Case.id.in_(
                        select(CaseTagLink.case_id).where(CaseTagLink.tag_id == tag_id)
                    )
                )

        # Apply dropdown filters (AND across definitions, OR within a definition's option refs)
        if dropdown_filters:
            for def_ref, option_refs in dropdown_filters.items():
                if not option_refs:
                    continue
                filters.append(
                    Case.id.in_(
                        select(CaseDropdownValue.case_id)
                        .join(
                            CaseDropdownDefinition,
                            CaseDropdownValue.definition_id
                            == CaseDropdownDefinition.id,
                        )
                        .join(
                            CaseDropdownOption,
                            CaseDropdownValue.option_id == CaseDropdownOption.id,
                        )
                        .where(
                            CaseDropdownDefinition.ref == def_ref,
                            CaseDropdownOption.ref.in_(option_refs),
                        )
                    )
                )

        # Apply date filters
        if start_time is not None:
            filters.append(Case.created_at >= start_time)

        if end_time is not None:
            filters.append(Case.created_at <= end_time)

        if updated_after is not None:
            filters.append(Case.updated_at >= updated_after)

        if updated_before is not None:
            filters.append(Case.updated_at <= updated_before)

        for clause in filters:
            stmt = stmt.where(clause)

        # Compute total count with applied filters (workspace scoped)
        count_stmt = select(func.count()).select_from(Case)
        for clause in filters:
            count_stmt = count_stmt.where(clause)

        total_count = await self.session.scalar(count_stmt)
        total_estimate = int(total_count or 0)

        # Determine sort column and direction
        sort_column = order_by or "created_at"
        sort_direction = sort or "desc"

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
        else:
            sort_attr = getattr(Case, sort_column)

        # Apply cursor-based pagination with sort-column-aware filtering
        # The cursor stores (sort_column, sort_value, created_at, id) for proper pagination
        if params.cursor:
            cursor_data = paginator.decode_cursor(params.cursor)
            cursor_id = uuid.UUID(cursor_data.id)

            # Check if cursor was created with the same sort column (for proper pagination)
            cursor_sort_value = cursor_data.sort_value
            cursor_has_sort_value = (
                cursor_data.sort_column == sort_column and cursor_sort_value is not None
            )
            if cursor_has_sort_value and sort_column == "tasks":
                cursor_has_sort_value = isinstance(cursor_sort_value, int)
            elif cursor_has_sort_value and enum_sort_values is not None:
                cursor_has_sort_value = isinstance(cursor_sort_value, int)

            if cursor_has_sort_value:
                sort_filter_col = sort_attr
                sort_cursor_value = cursor_sort_value

                # Composite filtering: (sort_col, id) matches ORDER BY
                # Use id as tie-breaker since it's always unique
                if sort_direction == "asc":
                    if params.reverse:
                        # Going backward: get records before cursor in sort order
                        stmt = stmt.where(
                            or_(
                                sort_filter_col < sort_cursor_value,
                                and_(
                                    sort_filter_col == sort_cursor_value,
                                    Case.id < cursor_id,
                                ),
                            )
                        )
                    else:
                        # Going forward: get records after cursor in sort order
                        stmt = stmt.where(
                            or_(
                                sort_filter_col > sort_cursor_value,
                                and_(
                                    sort_filter_col == sort_cursor_value,
                                    Case.id > cursor_id,
                                ),
                            )
                        )
                else:
                    # Descending order
                    if params.reverse:
                        # Going backward: get records after cursor in sort order
                        stmt = stmt.where(
                            or_(
                                sort_filter_col > sort_cursor_value,
                                and_(
                                    sort_filter_col == sort_cursor_value,
                                    Case.id > cursor_id,
                                ),
                            )
                        )
                    else:
                        # Going forward: get records before cursor in sort order
                        stmt = stmt.where(
                            or_(
                                sort_filter_col < sort_cursor_value,
                                and_(
                                    sort_filter_col == sort_cursor_value,
                                    Case.id < cursor_id,
                                ),
                            )
                        )

        # Apply sorting: (sort_col, id) for stable pagination
        # Use id as tie-breaker unless we're already sorting by id
        if sort_column == "id":
            # No tie-breaker needed when sorting by id (already unique)
            if sort_direction == "asc":
                stmt = stmt.order_by(sort_attr.asc())
            else:
                stmt = stmt.order_by(sort_attr.desc())
        else:
            # Add id as tie-breaker for non-unique columns
            if sort_direction == "asc":
                stmt = stmt.order_by(sort_attr.asc(), Case.id.asc())
            else:
                stmt = stmt.order_by(sort_attr.desc(), Case.id.desc())

        # Fetch limit + 1 to determine if there are more items
        stmt = stmt.limit(params.limit + 1)
        result = await self.session.execute(stmt)
        all_cases = result.scalars().all()

        # Check if there are more items
        has_more = len(all_cases) > params.limit
        cases = all_cases[: params.limit] if has_more else all_cases

        # Fetch task counts for all cases in one query (needed for cursor generation if sorting by tasks)
        task_counts = await self.get_task_counts([case.id for case in cases])

        # Generate cursors with sort column info for proper pagination
        next_cursor = None
        prev_cursor = None
        has_previous = params.cursor is not None

        def get_cursor_sort_value(case: Case) -> datetime | str | int | float | None:
            """Encode cursor sort values using the same semantics as ORDER BY."""
            if sort_column == "tasks":
                return task_counts.get(case.id, {}).get("total", 0)
            if enum_sort_values is not None:
                return _enum_sort_rank(
                    getattr(case, sort_column, None), enum_sort_values
                )
            return getattr(case, sort_column, None)

        if has_more and cases:
            last_case = cases[-1]
            sort_value = get_cursor_sort_value(last_case)
            next_cursor = paginator.encode_cursor(
                last_case.id,
                sort_column=sort_column,
                sort_value=sort_value,
            )

        if params.cursor and cases:
            first_case = cases[0]
            sort_value = get_cursor_sort_value(first_case)
            # For reverse pagination, swap the cursor meaning
            if params.reverse:
                next_cursor = paginator.encode_cursor(
                    first_case.id,
                    sort_column=sort_column,
                    sort_value=sort_value,
                )
            else:
                prev_cursor = paginator.encode_cursor(
                    first_case.id,
                    sort_column=sort_column,
                    sort_value=sort_value,
                )

        # Convert to CaseReadMinimal objects with tags and dropdown values
        case_items = []
        for case in cases:
            # Tags are already loaded via selectinload
            tag_reads = [
                CaseTagRead.model_validate(tag, from_attributes=True)
                for tag in case.tags
            ]

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
                    num_tasks_completed=task_counts[case.id]["completed"],
                    num_tasks_total=task_counts[case.id]["total"],
                )
            )

        return CursorPaginatedResponse(
            items=case_items,
            next_cursor=next_cursor,
            prev_cursor=prev_cursor,
            has_more=has_more,
            has_previous=has_previous,
            total_estimate=total_estimate,
        )

    async def list_cases(
        self,
        limit: int,
        order_by: Literal[
            "created_at", "updated_at", "priority", "severity", "status", "tasks"
        ]
        | None = None,
        sort: Literal["asc", "desc"] | None = None,
    ) -> CursorPaginatedResponse[CaseReadMinimal]:
        """List cases with a simplified default search query."""
        return await self.search_cases(
            params=CursorPaginationParams(limit=limit, cursor=None, reverse=False),
            order_by=order_by,
            sort=sort,
        )

    async def get_case(
        self, case_id: uuid.UUID, *, track_view: bool = False
    ) -> Case | None:
        """Get a case with its associated custom fields.

        Args:
            case_id: UUID of the case to retrieve

        Returns:
            Tuple containing the case and its fields (or None if no fields exist)

        Raises:
            TracecatNotFoundError: If the case doesn't exist
        """
        statement = (
            select(Case)
            .where(
                Case.workspace_id == self.workspace_id,
                Case.id == case_id,
            )
            .options(selectinload(Case.tags))
        )

        result = await self.session.execute(statement)
        case = result.scalars().first()

        if case and track_view:
            try:
                await self.events.create_case_viewed_event(case)
                await self.session.commit()
            except Exception:
                await self.session.rollback()
                self.logger.exception(
                    "Failed to record case viewed event",
                    case_id=case_id,
                    user_id=self.role.user_id,
                )

        return case

    @audit_log(resource_type="case", action="create")
    async def create_case(self, params: CaseCreate) -> Case:
        try:
            # Ensure the workspace-scoped `case_fields` schema/table exists before we
            # take locks on the `case` table (e.g. via INSERT/UPDATE). This avoids
            # deadlocks under concurrency when schema initialization requires a
            # ShareRowExclusiveLock on the referenced `case` table.
            await self.fields._ensure_schema_ready()

            now = datetime.now(UTC)
            # Create the base case first
            case = Case(
                workspace_id=self.workspace_id,
                summary=params.summary,
                description=params.description,
                priority=params.priority,
                severity=params.severity,
                status=params.status,
                assignee_id=params.assignee_id,
                payload=params.payload,
                created_at=now,
                updated_at=now,
            )

            self.session.add(case)
            await self.session.flush()  # Generate case ID

            # Always create the fields row to ensure defaults are applied
            # Pass empty dict if no fields provided to trigger default value application
            await self.fields.upsert_field_values(case, params.fields or {})

            run_ctx = ctx_run.get()
            await self.events.create_event(
                case=case,
                event=CreatedEvent(wf_exec_id=run_ctx.wf_exec_id if run_ctx else None),
            )

            # Commit once to persist case, fields, and event atomically
            await self.session.commit()
            # Make sure to refresh the case to get the fields relationship loaded
            await self.session.refresh(case)
            return case
        except Exception:
            await self.session.rollback()
            raise

    @audit_log(resource_type="case", action="update")
    async def update_case(self, case: Case, params: CaseUpdate) -> Case:
        """Update a case and optionally its custom fields.

        Args:
            case: The case object to update
            params: Optional case update parameters
            fields_data: Optional new field values

        Returns:
            Updated case with fields

        Raises:
            TracecatNotFoundError: If the case has no fields when trying to update fields
        """

        run_ctx = ctx_run.get()
        wf_exec_id = run_ctx.wf_exec_id if run_ctx else None

        # Update case parameters if provided
        set_fields = params.model_dump(exclude_unset=True)

        # Check for status changes
        if new_status := set_fields.pop("status", None):
            old_status = case.status
            if old_status != new_status:
                case.status = new_status
                # Record status change with detailed information about previous and new status
                if new_status == CaseStatus.CLOSED:
                    event = ClosedEvent(
                        old=old_status, new=new_status, wf_exec_id=wf_exec_id
                    )
                elif old_status == CaseStatus.CLOSED:
                    event = ReopenedEvent(
                        old=old_status, new=new_status, wf_exec_id=wf_exec_id
                    )
                else:
                    event = StatusChangedEvent(
                        old=old_status, new=new_status, wf_exec_id=wf_exec_id
                    )
                await self.events.create_event(case=case, event=event)

        # Check for priority changes
        if new_priority := set_fields.pop("priority", None):
            old_priority = case.priority
            if old_priority != new_priority:
                case.priority = new_priority
                # Record priority change with detailed information
                await self.events.create_event(
                    case=case,
                    event=PriorityChangedEvent(
                        old=old_priority, new=new_priority, wf_exec_id=wf_exec_id
                    ),
                )

        # Check for severity changes
        if new_severity := set_fields.pop("severity", None):
            old_severity = case.severity
            if old_severity != new_severity:
                case.severity = new_severity
                # Record severity change with detailed information
                await self.events.create_event(
                    case=case,
                    event=SeverityChangedEvent(
                        old=old_severity, new=new_severity, wf_exec_id=wf_exec_id
                    ),
                )

        if fields := set_fields.pop("fields", None):
            # If fields was set, we need to update the fields row
            # It must be a dictionary because we validated it in the model
            if not isinstance(fields, dict):
                raise ValueError("Fields must be a dict")

            # Get existing fields for diff calculation
            existing_fields = await self.fields.get_fields(case) or {}

            # Upsert the field values (handles both create and update)
            await self.fields.upsert_field_values(case, fields)

            # Calculate diffs for event
            diffs = []
            for field, value in fields.items():
                old_value = existing_fields.get(field)
                if old_value != value:
                    diffs.append(FieldDiff(field=field, old=old_value, new=value))
            await self.events.create_event(
                case=case,
                event=FieldsChangedEvent(changes=diffs, wf_exec_id=wf_exec_id),
            )

        # Handle the rest of the field updates
        events: list[CaseEventVariant] = []
        for key, value in set_fields.items():
            old = getattr(case, key, None)
            setattr(case, key, value)
            if key == "assignee_id":
                events.append(
                    AssigneeChangedEvent(old=old, new=value, wf_exec_id=wf_exec_id)
                )
            elif key == "summary":
                events.append(
                    UpdatedEvent(
                        field="summary", old=old, new=value, wf_exec_id=wf_exec_id
                    )
                )
            elif key == "payload":
                # Only record event if payload actually changed
                if old != value:
                    events.append(PayloadChangedEvent(wf_exec_id=wf_exec_id))

        try:
            # If there are any remaining changed fields, record a general update activity
            for event in events:
                await self.events.create_event(case=case, event=event)

            # Commit once to persist all updates and emitted events atomically
            await self.session.commit()
            await self.session.refresh(case)
            return case
        except Exception:
            await self.session.rollback()
            raise

    @audit_log(resource_type="case", action="delete")
    async def delete_case(self, case: Case) -> None:
        """Delete a case and optionally its associated field data.

        Args:
            case: The case object to delete
            delete_fields: Whether to also delete the associated field data
        """
        # No need to record a delete activity - when we delete the case,
        # all related activities will be removed too due to cascade delete.
        # However, this activity could be useful in an audit log elsewhere
        # if system-wide activities are implemented separately.

        await self.session.delete(case)
        await self.session.commit()


class CaseFieldsService(CustomFieldsService):
    """Service that manages workspace-scoped case fields.

    CaseFields is the workspace-level definition storing schema metadata.
    The actual per-case values are stored in the workspace's custom fields schema in the `case_fields` table.
    """

    service_name = "case_fields"
    # Hardcoded to preserve existing workspace-scoped table names
    # (metadata table was renamed from case_fields to case_field)
    _table = "case_fields"
    _reserved_columns = {"id", "case_id", "created_at", "updated_at", "workspace_id"}

    def _table_definition(self) -> sa.Table:
        """Return the SQLAlchemy Table definition for the case_fields workspace table."""
        return sa.Table(
            self.sanitized_table_name,
            sa.MetaData(),
            sa.Column("id", UUID(as_uuid=True), primary_key=True),
            sa.Column("case_id", UUID(as_uuid=True), unique=True, nullable=False),
            sa.Column(
                "created_at",
                sa.TIMESTAMP(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            sa.Column(
                "updated_at",
                sa.TIMESTAMP(timezone=True),
                nullable=False,
                server_default=sa.func.now(),
            ),
            # Use the actual Case table column to avoid metadata resolution issues
            sa.ForeignKeyConstraint(
                ["case_id"],
                [Case.__table__.c.id],
                name="fk_case_fields_case",
                ondelete="CASCADE",
            ),
            schema=self.schema_name,
        )

    async def get_field_schema(self) -> dict[str, Any]:
        """Get the field schema (column definitions) for this workspace.

        Returns a dict mapping field_id -> {type, options (only for SELECT/MULTI_SELECT)}
        """
        stmt = sa.select(CaseFields.schema).where(
            CaseFields.workspace_id == self.workspace_id
        )
        result = await self.session.execute(stmt)
        schema = result.scalar_one_or_none()
        return schema or {}

    async def _update_field_schema(
        self, field_id: str, field_def: dict[str, Any] | None
    ) -> None:
        """Update the field schema for a specific field.

        Args:
            field_id: The field name/id
            field_def: The field definition dict {type, options (for SELECT/MULTI_SELECT only)} or None to remove
        """
        stmt = sa.select(CaseFields).where(CaseFields.workspace_id == self.workspace_id)
        result = await self.session.execute(stmt)
        definition = result.scalar_one_or_none()

        if definition is None:
            definition = CaseFields(workspace_id=self.workspace_id, schema={})
            self.session.add(definition)
            await self.session.flush()

        current_schema = definition.schema or {}

        if field_def is None:
            current_schema.pop(field_id, None)
        else:
            current_schema[field_id] = field_def

        definition.schema = current_schema
        flag_modified(definition, "schema")
        await self.session.flush()

    async def create_field(self, params: CustomFieldCreate) -> None:
        """Create a new custom field column and update the schema."""

        await self._ensure_schema_ready()
        params.nullable = True  # Custom fields remain nullable by default
        await self.editor.create_column(params)

        # Store field metadata in schema
        # Schema structure: {field_name: {type, options (only for SELECT/MULTI_SELECT)}}
        field_def: dict[str, Any] = {"type": params.type.value}

        # Only include options for SELECT/MULTI_SELECT types
        if params.type in (SqlType.SELECT, SqlType.MULTI_SELECT) and params.options:
            field_def["options"] = normalize_column_options(params.options)

        await self._update_field_schema(params.name, field_def)

        await self.session.commit()

    async def update_field(self, field_id: str, params: CustomFieldUpdate) -> None:
        """Update a custom field column and update the schema if needed."""
        await self._ensure_schema_ready()

        # Get current schema to preserve type info
        current_schema = await self.get_field_schema()
        current_field_def = current_schema.get(field_id, {})

        # Update the physical column (name, default, etc.)
        await self.editor.update_column(field_id, params)

        # Determine the new field name (if renamed)
        new_field_id = params.name if params.name is not None else field_id

        # Build updated field definition
        # Preserve the type from current schema, or use params.type if provided
        field_type = params.type.value if params.type else current_field_def.get("type")
        if field_type:
            new_field_def: dict[str, Any] = {"type": field_type}

            # Update options if provided (even empty list clears options)
            if params.options is not None:
                normalized = normalize_column_options(params.options)
                if normalized:
                    new_field_def["options"] = normalized
            elif "options" in current_field_def:
                # Preserve existing options if not being updated
                new_field_def["options"] = current_field_def["options"]

            # If field was renamed, remove old entry
            if new_field_id != field_id:
                await self._update_field_schema(field_id, None)

            # Update/add the field definition
            await self._update_field_schema(new_field_id, new_field_def)

        await self.session.commit()

    async def delete_field(self, field_id: str) -> None:
        """Delete a custom field and remove it from the schema."""
        await self._ensure_schema_ready()
        if field_id in self._reserved_columns:
            raise ValueError(f"Field {field_id} is a reserved field")

        # Remove from schema first
        await self._update_field_schema(field_id, None)

        await self.editor.delete_column(field_id)
        await self.session.commit()

    async def ensure_workspace_row(self, case_id: uuid.UUID) -> uuid.UUID:
        """Ensure a workspace data row exists for the given case.

        Args:
            case_id: The case ID to ensure a row exists for

        Returns:
            The row ID for the case's field values

        Raises:
            TracecatException: If the row could not be created or retrieved
        """
        await self._ensure_schema_ready()
        table = self._table_definition()
        row_id = uuid.uuid4()

        # Use ON CONFLICT DO UPDATE SET id = id to ensure we always get a row ID back,
        # even if the row already exists. This prevents race conditions where two
        # concurrent requests both try to insert the same row.
        insert_stmt = insert(table).values(id=row_id, case_id=case_id)
        stmt = insert_stmt.on_conflict_do_update(
            index_elements=[table.c.case_id],
            set_={table.c.id: table.c.id},  # No-op update to ensure RETURNING works
        ).returning(table.c.id)

        result = await self.session.execute(stmt)
        inserted_id = result.scalar_one_or_none()

        if inserted_id is None:
            raise TracecatException(
                "Failed to ensure case fields workspace row for the given case."
            )

        return inserted_id

    async def get_fields(self, case: Case) -> dict[str, Any] | None:
        """Retrieve custom field values for a case."""
        await self._ensure_schema_ready()
        table = self._table_definition()
        row_id = await self.session.scalar(
            sa.select(table.c.id).where(table.c.case_id == case.id)
        )
        return await self.editor.get_row(row_id) if row_id else None

    async def upsert_field_values(
        self, case: Case, fields: dict[str, Any]
    ) -> dict[str, Any]:
        """Upsert custom field values for a case.

        This method ensures the workspace row exists and updates field values.
        It does NOT commit - the caller is responsible for committing the transaction
        to ensure atomicity with other operations (e.g., case creation/update).

        Args:
            case: The case to upsert field values for
            fields: Dictionary of field names to values

        Returns:
            Dictionary containing the updated row data

        Raises:
            TracecatException: If the case has no workspace or if field operations fail
            TracecatNotFoundError: If the row is not found after ensuring it exists
        """
        if case.workspace_id is None:
            raise TracecatException(
                "Cannot upsert case fields without an owning workspace."
            )
        row_id = await self.ensure_workspace_row(case.id)

        try:
            if fields:
                res = await self.editor.update_row(row_id=row_id, data=fields)
                await self.session.flush()
                return res
            return await self.editor.get_row(row_id=row_id)
        except TracecatNotFoundError as e:
            self.logger.error(
                "Case fields row not found after upsert",
                row_id=row_id,
                case_id=case.id,
                fields=fields,
                error=str(e),
            )
            field_names = list(fields.keys()) if fields else []
            field_info = (
                f" Fields attempted: {', '.join(field_names)}." if field_names else ""
            )
            raise TracecatException(
                "Failed to save custom field values for case. The field row was upserted but could not be updated."
                f"{field_info} Please verify all custom fields exist in Settings > Cases > Custom Fields and have correct data types."
            ) from e
        except ProgrammingError as e:
            while cause := e.__cause__:
                e = cause
            if isinstance(e, UndefinedColumnError):
                raise TracecatException(
                    "Failed to create case fields. One or more custom fields do not exist. Please ensure these fields have been created and try again."
                ) from e
            raise TracecatException(
                "Failed to create case fields due to an unexpected error."
            ) from e


class CaseCommentsService(BaseWorkspaceService):
    """Service for managing case comments."""

    service_name = "case_comments"

    async def get_comment(self, comment_id: uuid.UUID) -> CaseComment | None:
        """Get a comment by ID.

        Args:
            case: The case to get the comment for
            comment_id: The ID of the comment to get

        Returns:
            The comment if found, None otherwise
        """
        statement = select(CaseComment).where(
            CaseComment.workspace_id == self.workspace_id,
            CaseComment.id == comment_id,
        )

        result = await self.session.execute(statement)
        return result.scalars().first()

    async def list_comments(
        self, case: Case, *, with_users: bool = True
    ) -> list[tuple[CaseComment, User | None]]:
        """List all comments for a case with optional user information.

        Args:
            case: The case to get comments for
            with_users: Whether to include user information (default: True)

        Returns:
            A list of tuples containing comments and their associated users (or None if no user)
        """

        if with_users:
            statement = (
                select(CaseComment, User)
                .outerjoin(User, cast(CaseComment.user_id, sa.UUID) == User.id)
                .where(CaseComment.case_id == case.id)
                .order_by(cast(CaseComment.created_at, sa.DateTime))
            )
            result = await self.session.execute(statement)
            return list(result.tuples().all())
        else:
            statement = (
                select(CaseComment)
                .where(CaseComment.case_id == case.id)
                .order_by(cast(CaseComment.created_at, sa.DateTime))
            )
            result = await self.session.execute(statement)
            # Return in the same format as the join query for consistency
            return [(comment, None) for comment in result.scalars().all()]

    async def create_comment(
        self, case: Case, params: CaseCommentCreate
    ) -> CaseComment:
        """Create a new comment on a case.

        Args:
            case: The case to comment on
            params: The comment parameters

        Returns:
            The created comment
        """
        comment = CaseComment(
            workspace_id=self.workspace_id,
            case_id=case.id,
            content=params.content,
            parent_id=params.parent_id,
            user_id=self.role.user_id,
        )

        self.session.add(comment)
        await self.session.commit()
        await self.session.refresh(comment)

        return comment

    async def update_comment(
        self, comment: CaseComment, params: CaseCommentUpdate
    ) -> CaseComment:
        """Update an existing comment.

        Args:
            comment: The comment to update
            params: The updated comment parameters

        Returns:
            The updated comment

        Raises:
            TracecatNotFoundError: If the comment doesn't exist
            TracecatAuthorizationError: If the user doesn't own the comment
        """
        # Check if the user owns the comment
        if comment.user_id != self.role.user_id:
            raise TracecatAuthorizationError("You cannot update this comment")

        set_fields = params.model_dump(exclude_unset=True)
        for key, value in set_fields.items():
            setattr(comment, key, value)

        # Set last_edited_at
        comment.last_edited_at = datetime.now(UTC)

        await self.session.commit()
        await self.session.refresh(comment)

        return comment

    async def delete_comment(self, comment: CaseComment) -> None:
        """Delete a comment.

        Args:
            case: The case the comment belongs to
            comment_id: The ID of the comment to delete

        Raises:
            TracecatNotFoundError: If the comment doesn't exist
            TracecatAuthorizationError: If the user doesn't own the comment
        """

        # Check if the user owns the comment
        if comment.user_id != self.role.user_id:
            raise TracecatAuthorizationError("You can only delete your own comments")

        await self.session.delete(comment)
        await self.session.commit()


class CaseEventsService(BaseWorkspaceService):
    """Service for managing case events."""

    service_name = "case_events"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)

    async def list_events(self, case: Case) -> Sequence[CaseEvent]:
        """List all events for a case."""
        statement = (
            select(CaseEvent)
            .where(CaseEvent.case_id == case.id)
            # Order by creation time (newest first) and fall back to surrogate_id
            # to ensure deterministic ordering when timestamps are equal.
            .order_by(
                CaseEvent.created_at.desc(),
                CaseEvent.surrogate_id.desc(),
            )
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def create_event(self, case: Case, event: CaseEventVariant) -> CaseEvent:
        """Create a new activity record for a case with variant-specific data.

        Note: This method is non-committing. The caller is responsible for
        wrapping operations in a transaction and committing once at the end
        to preserve atomicity across multi-step updates.

        Duration sync is performed automatically after each event is created,
        so callers do not need to call sync_case_durations separately.
        """

        db_event = CaseEvent(
            workspace_id=self.workspace_id,
            case_id=case.id,
            type=event.type,
            data=event.model_dump(exclude={"type"}, mode="json"),
            user_id=self.role.user_id,
        )
        self.session.add(db_event)
        # Flush so that generated fields (e.g., id) are available if needed
        await self.session.flush()

        event_id = str(db_event.id)
        event_type = (
            db_event.type.value if hasattr(db_event.type, "value") else db_event.type
        )
        created_at = db_event.created_at or datetime.now(UTC)
        case_id = str(case.id)
        workspace_id = str(case.workspace_id)

        async def _publish_case_event() -> None:
            await publish_case_event_payload(
                event_id=event_id,
                case_id=case_id,
                workspace_id=workspace_id,
                event_type=event_type,
                created_at=created_at,
            )

        add_after_commit_callback(self.session, _publish_case_event)

        # Auto-sync durations whenever an event is created
        durations_service = CaseDurationService(session=self.session, role=self.role)
        await durations_service.sync_case_durations(case)

        return db_event

    async def create_case_viewed_event(
        self,
        case: Case,
        *,
        dedupe_window: timedelta = CASE_VIEW_EVENT_DEDUP_WINDOW,
    ) -> CaseEvent | None:
        """Record a case viewed event if the current user hasn't viewed recently."""
        if not self.role.user_id:
            return None

        now_utc = datetime.now(UTC)
        stmt = (
            select(CaseEvent)
            .where(
                CaseEvent.workspace_id == self.workspace_id,
                CaseEvent.case_id == case.id,
                CaseEvent.type == CaseEventType.CASE_VIEWED,
                CaseEvent.user_id == self.role.user_id,
            )
            .order_by(CaseEvent.created_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        last_event = result.scalars().first()
        if last_event:
            last_created_at = last_event.created_at
            if last_created_at.tzinfo is None:
                if datetime.now() - last_created_at < dedupe_window:
                    return None
            else:
                if now_utc - last_created_at < dedupe_window:
                    return None

        return await self.create_event(case=case, event=CaseViewedEvent())


class CaseTasksService(BaseWorkspaceService):
    """Service for managing case tasks."""

    service_name = "case_tasks"

    async def list_tasks(self, case_id: uuid.UUID) -> Sequence[CaseTask]:
        """List all tasks for a case.

        Args:
            case_id: The ID of the case to get tasks for

        Returns:
            A sequence of tasks for the case, ordered by priority (highest first) then creation date
        """
        statement = (
            select(CaseTask)
            .where(
                CaseTask.workspace_id == self.workspace_id,
                CaseTask.case_id == case_id,
            )
            .order_by(
                CaseTask.priority.desc(),
                cast(CaseTask.created_at, sa.DateTime),
            )
        )
        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_task(self, task_id: uuid.UUID) -> CaseTask:
        """Get a task by ID.

        Args:
            task_id: The ID of the task to get

        Returns:
            The task

        Raises:
            TracecatNotFoundError: If the task is not found
        """
        statement = select(CaseTask).where(
            CaseTask.workspace_id == self.workspace_id,
            CaseTask.id == task_id,
        )
        result = await self.session.execute(statement)
        task = result.scalars().first()
        if not task:
            raise TracecatNotFoundError(f"Task {task_id} not found")
        return task

    async def _validate_default_trigger_values(
        self,
        workflow_id: uuid.UUID | None,
        default_trigger_values: dict[str, Any],
    ) -> None:
        """Validate default_trigger_values against workflow's expects schema.

        Args:
            workflow_id: The workflow ID to validate against
            default_trigger_values: The trigger values to validate

        Raises:
            TracecatNotFoundError: If the workflow is not found
            TracecatValidationError: If values don't match schema
        """

        if not workflow_id:
            raise TracecatValidationError(
                "Cannot set default_trigger_values without a workflow_id"
            )
        # Fetch workflow
        stmt = select(Workflow).where(
            Workflow.workspace_id == self.workspace_id,
            Workflow.id == workflow_id,
        )
        result = await self.session.execute(stmt)
        workflow = result.scalars().first()

        if not workflow:
            raise TracecatNotFoundError(f"Workflow {workflow_id} not found")

        # Skip validation if workflow has no expects schema
        if not workflow.expects:
            return

        expects_schema = {
            field_name: ExpectedField.model_validate(field_schema)
            for field_name, field_schema in workflow.expects.items()
        }

        validator = create_expectation_model(
            expects_schema, model_name="DefaultTriggerValuesValidator"
        )

        try:
            validator(**default_trigger_values)
        except ValidationError as e:
            raise TracecatValidationError(
                f"Invalid default_trigger_values for workflow '{workflow.title}': {e}"
            ) from e

    async def create_task(self, case_id: uuid.UUID, params: CaseTaskCreate) -> CaseTask:
        """Create a new task for a case.

        Args:
            case_id: The ID of the case to create a task for
            params: The task parameters

        Returns:
            The created task

        Raises:
            TracecatNotFoundError: If the case is not found in the current workspace
        """
        statement = select(Case).where(
            Case.workspace_id == self.workspace_id,
            Case.id == case_id,
        )
        result = await self.session.execute(statement)
        case = result.scalars().first()
        if not case:
            raise TracecatNotFoundError(f"Case {case_id} not found")

        # Convert workflow_id from AnyWorkflowID to UUID
        workflow_uuid = (
            WorkflowUUID.new(params.workflow_id) if params.workflow_id else None
        )

        if params.default_trigger_values:
            await self._validate_default_trigger_values(
                workflow_uuid, params.default_trigger_values
            )

        task = CaseTask(
            workspace_id=self.workspace_id,
            case_id=case_id,
            title=params.title,
            description=params.description,
            priority=params.priority,
            status=params.status,
            assignee_id=params.assignee_id,
            workflow_id=workflow_uuid,
            default_trigger_values=params.default_trigger_values,
        )
        self.session.add(task)
        # Flush to get task ID before emitting event
        await self.session.flush()

        run_ctx = ctx_run.get()
        wf_exec_id = run_ctx.wf_exec_id if run_ctx else None

        # Emit task created event
        events_svc = CaseEventsService(session=self.session, role=self.role)
        await events_svc.create_event(
            case=case,
            event=TaskCreatedEvent(
                task_id=task.id, title=task.title, wf_exec_id=wf_exec_id
            ),
        )

        # Update parent case's updated_at timestamp
        case.updated_at = datetime.now(UTC)

        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def update_task(self, task_id: uuid.UUID, params: CaseTaskUpdate) -> CaseTask:
        """Update a task.

        Args:
            task_id: The ID of the task to update
            params: The task update parameters

        Returns:
            The updated task

        Raises:
            TracecatNotFoundError: If the task is not found
        """
        task = await self.get_task(task_id)

        # Load case for event context
        statement = select(Case).where(
            Case.workspace_id == self.workspace_id,
            Case.id == task.case_id,
        )
        result = await self.session.execute(statement)
        case = result.scalars().first()
        if not case:
            raise TracecatNotFoundError(f"Case {task.case_id} not found")

        run_ctx = ctx_run.get()
        wf_exec_id = run_ctx.wf_exec_id if run_ctx else None
        events_svc = CaseEventsService(session=self.session, role=self.role)

        # Update only provided fields and emit events
        set_fields = params.model_dump(exclude_unset=True)

        # Status change
        if (new_status := set_fields.pop("status", None)) is not None:
            old_status = task.status
            if old_status != new_status:
                task.status = new_status
                await events_svc.create_event(
                    case=case,
                    event=TaskStatusChangedEvent(
                        task_id=task.id,
                        title=task.title,
                        old=old_status,
                        new=new_status,
                        wf_exec_id=wf_exec_id,
                    ),
                )

        # Assignee change
        if (new_assignee := set_fields.pop("assignee_id", None)) is not None:
            old_assignee = task.assignee_id
            if old_assignee != new_assignee:
                task.assignee_id = new_assignee
                await events_svc.create_event(
                    case=case,
                    event=TaskAssigneeChangedEvent(
                        task_id=task.id,
                        title=task.title,
                        old=old_assignee,
                        new=new_assignee,
                        wf_exec_id=wf_exec_id,
                    ),
                )

        # Priority change
        if (new_priority := set_fields.pop("priority", None)) is not None:
            old_priority = task.priority
            if old_priority != new_priority:
                task.priority = new_priority
                await events_svc.create_event(
                    case=case,
                    event=TaskPriorityChangedEvent(
                        task_id=task.id,
                        title=task.title,
                        old=old_priority,
                        new=new_priority,
                        wf_exec_id=wf_exec_id,
                    ),
                )

        # Workflow change - handle separately to allow clearing (setting to None)
        # Check if the field was provided in the update payload using model_fields_set
        if "workflow_id" in params.model_fields_set:
            old_wfid = task.workflow_id
            new_wfid = None
            if params.workflow_id is not None:
                # Convert workflow_id from AnyWorkflowID to UUID
                new_wfid = WorkflowUUID.new(params.workflow_id)

            # Validate existing default_trigger_values against new workflow if both exist
            # Only validate if default_trigger_values is NOT being updated in this request
            # (if it is, the new values will be validated later)
            if (
                new_wfid
                and task.default_trigger_values
                and "default_trigger_values" not in params.model_fields_set
            ):
                await self._validate_default_trigger_values(
                    new_wfid, task.default_trigger_values
                )

            if old_wfid != new_wfid:
                task.workflow_id = new_wfid
                await events_svc.create_event(
                    case=case,
                    event=TaskWorkflowChangedEvent(
                        task_id=task.id,
                        title=task.title,
                        old=WorkflowUUID.new(old_wfid) if old_wfid else None,
                        new=new_wfid,
                        wf_exec_id=wf_exec_id,
                    ),
                )

        # default_trigger_values change - validate against current workflow if it exists
        if "default_trigger_values" in params.model_fields_set:
            new_default_values = set_fields.pop("default_trigger_values", None)
            if new_default_values:
                await self._validate_default_trigger_values(
                    task.workflow_id, new_default_values
                )
            task.default_trigger_values = new_default_values

        # Title and description - update silently without events
        if (new_title := set_fields.pop("title", None)) is not None:
            task.title = new_title

        if (new_desc := set_fields.pop("description", None)) is not None:
            task.description = new_desc

        # Update parent case's updated_at timestamp
        case.updated_at = datetime.now(UTC)

        await self.session.commit()
        await self.session.refresh(task)
        return task

    async def delete_task(self, task_id: uuid.UUID) -> None:
        """Delete a task.

        Args:
            task_id: The ID of the task to delete

        Raises:
            TracecatNotFoundError: If the task is not found
        """
        task = await self.get_task(task_id)

        # Load case for event context
        statement = select(Case).where(
            Case.workspace_id == self.workspace_id,
            Case.id == task.case_id,
        )
        result = await self.session.execute(statement)
        case = result.scalars().first()
        if not case:
            raise TracecatNotFoundError(f"Case {task.case_id} not found")

        run_ctx = ctx_run.get()
        wf_exec_id = run_ctx.wf_exec_id if run_ctx else None
        events_svc = CaseEventsService(session=self.session, role=self.role)

        # Emit delete event before deleting to capture title
        await events_svc.create_event(
            case=case,
            event=TaskDeletedEvent(
                task_id=task.id, title=task.title, wf_exec_id=wf_exec_id
            ),
        )

        # Update parent case's updated_at timestamp
        case.updated_at = datetime.now(UTC)

        await self.session.delete(task)
        await self.session.commit()
