import uuid
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import sqlalchemy as sa
from asyncpg import UndefinedColumnError
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import aliased, selectinload
from sqlmodel import and_, cast, col, desc, func, or_, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.auth.schemas import UserRead
from tracecat.auth.types import Role
from tracecat.cases.attachments import CaseAttachmentService
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
    CaseFieldCreate,
    CaseFieldUpdate,
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
from tracecat.contexts import ctx_run
from tracecat.db.models import (
    Case,
    CaseComment,
    CaseEvent,
    CaseFields,
    CaseTagLink,
    CaseTask,
    User,
)
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatException,
    TracecatNotFoundError,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.service import BaseWorkspaceService
from tracecat.tables.service import TableEditorService, TablesService
from tracecat.types.pagination import (
    BaseCursorPaginator,
    CursorPaginatedResponse,
    CursorPaginationParams,
)


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
        self.durations = CaseDurationService(session=self.session, role=self.role)

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
                    sa.case(
                        (col(CaseTask.status) == CaseTaskStatus.COMPLETED, 1), else_=0
                    )
                ).label("completed"),
            )
            .where(col(CaseTask.case_id).in_(case_ids))
            .group_by(col(CaseTask.case_id))
        )

        result = await self.session.exec(stmt)
        rows = result.all()

        # Build result dict with defaults for cases without tasks
        counts = {case_id: {"completed": 0, "total": 0} for case_id in case_ids}
        for case_id, total, completed in rows:
            counts[case_id] = {
                "completed": int(completed or 0),
                "total": int(total or 0),
            }

        return counts

    async def list_cases(
        self,
        limit: int | None = None,
        order_by: Literal["created_at", "updated_at", "priority", "severity", "status"]
        | None = None,
        sort: Literal["asc", "desc"] | None = None,
    ) -> Sequence[Case]:
        statement = select(Case).where(Case.owner_id == self.workspace_id)
        if limit is not None:
            statement = statement.limit(limit)
        if order_by is not None:
            attr = getattr(Case, order_by)
            if sort == "asc":
                statement = statement.order_by(attr.asc())
            elif sort == "desc":
                statement = statement.order_by(attr.desc())
            else:
                statement = statement.order_by(attr)
        result = await self.session.exec(statement)
        return result.all()

    async def list_cases_paginated(
        self,
        params: CursorPaginationParams,
        search_term: str | None = None,
        status: CaseStatus | Sequence[CaseStatus] | None = None,
        priority: CasePriority | Sequence[CasePriority] | None = None,
        severity: CaseSeverity | Sequence[CaseSeverity] | None = None,
        assignee_ids: Sequence[uuid.UUID] | None = None,
        include_unassigned: bool = False,
        tag_ids: list[uuid.UUID] | None = None,
    ) -> CursorPaginatedResponse[CaseReadMinimal]:
        """List cases with cursor-based pagination and filtering."""
        paginator = BaseCursorPaginator(self.session)

        # Get estimated total count from table statistics
        total_estimate = await paginator.get_table_row_estimate("cases")

        # Base query with workspace filter - eagerly load tags and assignee
        stmt = (
            select(Case)
            .where(Case.owner_id == self.workspace_id)
            .options(selectinload(Case.tags))  # type: ignore
            .options(selectinload(Case.assignee))  # type: ignore
            .order_by(col(Case.created_at).desc(), col(Case.id).desc())
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
            stmt = stmt.where(
                or_(
                    col(Case.summary).ilike(search_pattern),
                    col(Case.description).ilike(search_pattern),
                )
            )

        normalized_statuses = _normalize_filter_values(status)
        if normalized_statuses:
            stmt = stmt.where(col(Case.status).in_(normalized_statuses))

        # Apply priority filter
        normalized_priorities = _normalize_filter_values(priority)
        if normalized_priorities:
            stmt = stmt.where(col(Case.priority).in_(normalized_priorities))

        # Apply severity filter
        normalized_severities = _normalize_filter_values(severity)
        if normalized_severities:
            stmt = stmt.where(col(Case.severity).in_(normalized_severities))

        # Apply assignee filter
        if include_unassigned or assignee_ids:
            unique_assignees = list(dict.fromkeys(assignee_ids or []))
            assignee_conditions: list[Any] = []

            if unique_assignees:
                assignee_conditions.append(col(Case.assignee_id).in_(unique_assignees))

            if include_unassigned:
                assignee_conditions.append(col(Case.assignee_id).is_(None))

            if assignee_conditions:
                if len(assignee_conditions) == 1:
                    stmt = stmt.where(assignee_conditions[0])
                else:
                    stmt = stmt.where(or_(*assignee_conditions))

        # Apply tag filtering if tag_ids provided (AND logic - case must have all tags)
        if tag_ids:
            for tag_id in tag_ids:
                stmt = stmt.where(
                    col(Case.id).in_(
                        select(CaseTagLink.case_id).where(CaseTagLink.tag_id == tag_id)
                    )
                )

        # Apply cursor filtering
        if params.cursor:
            cursor_data = paginator.decode_cursor(params.cursor)
            cursor_time = cursor_data.created_at
            cursor_id = uuid.UUID(cursor_data.id)

            if params.reverse:
                stmt = stmt.where(
                    or_(
                        col(Case.created_at) > cursor_time,
                        and_(
                            col(Case.created_at) == cursor_time,
                            col(Case.id) > cursor_id,
                        ),
                    )
                ).order_by(col(Case.created_at).asc(), col(Case.id).asc())
            else:
                stmt = stmt.where(
                    or_(
                        col(Case.created_at) < cursor_time,
                        and_(
                            col(Case.created_at) == cursor_time,
                            col(Case.id) < cursor_id,
                        ),
                    )
                )

        # Fetch limit + 1 to determine if there are more items
        stmt = stmt.limit(params.limit + 1)
        result = await self.session.exec(stmt)
        all_cases = result.all()

        # Check if there are more items
        has_more = len(all_cases) > params.limit
        cases = all_cases[: params.limit] if has_more else all_cases

        # Generate cursors
        next_cursor = None
        prev_cursor = None
        has_previous = params.cursor is not None

        if has_more and cases:
            last_case = cases[-1]
            next_cursor = paginator.encode_cursor(last_case.created_at, last_case.id)

        if params.cursor and cases:
            first_case = cases[0]
            # For reverse pagination, swap the cursor meaning
            if params.reverse:
                next_cursor = paginator.encode_cursor(
                    first_case.created_at, first_case.id
                )
            else:
                prev_cursor = paginator.encode_cursor(
                    first_case.created_at, first_case.id
                )

        # Fetch task counts for all cases in one query
        task_counts = await self.get_task_counts([case.id for case in cases])

        # Convert to CaseReadMinimal objects with tags
        case_items = []
        for case in cases:
            # Tags are already loaded via selectinload
            tag_reads = [
                CaseTagRead.model_validate(tag, from_attributes=True)
                for tag in case.tags
            ]

            case_items.append(
                CaseReadMinimal(
                    id=case.id,
                    created_at=case.created_at,
                    updated_at=case.updated_at,
                    short_id=f"CASE-{case.case_number:04d}",
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

    async def search_cases(
        self,
        search_term: str | None = None,
        status: CaseStatus | Sequence[CaseStatus] | None = None,
        priority: CasePriority | Sequence[CasePriority] | None = None,
        severity: CaseSeverity | Sequence[CaseSeverity] | None = None,
        tag_ids: list[uuid.UUID] | None = None,
        start_time: datetime | None = None,
        end_time: datetime | None = None,
        updated_before: datetime | None = None,
        updated_after: datetime | None = None,
        order_by: Literal["created_at", "updated_at", "priority", "severity", "status"]
        | None = None,
        sort: Literal["asc", "desc"] | None = None,
        limit: int | None = None,
    ) -> Sequence[Case]:
        """Search cases based on various criteria.

        Args:
            search_term: Text to search for in case summary and description
            status: Filter by case status
            priority: Filter by case priority
            severity: Filter by case severity
            start_time: Filter by case creation time
            end_time: Filter by case creation time
            updated_before: Filter by case update time
            updated_after: Filter by case update time
            order_by: Field to order the cases by
            sort: Direction to sort (asc or desc)
            limit: Maximum number of cases to return

        Returns:
            Sequence of cases matching the search criteria
        """
        statement = (
            select(Case)
            .where(Case.owner_id == self.workspace_id)
            .options(selectinload(Case.tags))  # type: ignore
        )

        # Apply search term filter (search in summary and description)
        if search_term:
            # Validate search term to prevent abuse
            if len(search_term) > 1000:
                raise ValueError("Search term cannot exceed 1000 characters")
            if "\x00" in search_term:
                raise ValueError("Search term cannot contain null bytes")

            # Use SQLAlchemy's concat function for proper parameter binding
            search_pattern = func.concat("%", search_term, "%")
            statement = statement.where(
                or_(
                    col(Case.summary).ilike(search_pattern),
                    col(Case.description).ilike(search_pattern),
                )
            )

        # Apply status filter
        normalized_statuses = _normalize_filter_values(status)
        if normalized_statuses:
            statement = statement.where(col(Case.status).in_(normalized_statuses))

        # Apply priority filter
        normalized_priorities = _normalize_filter_values(priority)
        if normalized_priorities:
            statement = statement.where(col(Case.priority).in_(normalized_priorities))

        # Apply severity filter
        normalized_severities = _normalize_filter_values(severity)
        if normalized_severities:
            statement = statement.where(col(Case.severity).in_(normalized_severities))

        # Apply tag filtering if specified (AND logic for multiple tags)
        if tag_ids:
            for tag_id in tag_ids:
                # Self-join for each tag to ensure case has ALL specified tags
                tag_alias = aliased(CaseTagLink)
                statement = statement.join(
                    tag_alias,
                    and_(tag_alias.case_id == Case.id, tag_alias.tag_id == tag_id),
                )

        # Apply date filters
        if start_time is not None:
            statement = statement.where(Case.created_at >= start_time)

        if end_time is not None:
            statement = statement.where(Case.created_at <= end_time)

        if updated_after is not None:
            statement = statement.where(Case.updated_at >= updated_after)

        if updated_before is not None:
            statement = statement.where(Case.updated_at <= updated_before)

        # Apply limit
        if limit is not None:
            statement = statement.limit(limit)

        # Apply ordering
        if order_by is not None:
            attr = getattr(Case, order_by)
            if sort == "asc":
                statement = statement.order_by(attr.asc())
            elif sort == "desc":
                statement = statement.order_by(attr.desc())
            else:
                statement = statement.order_by(attr)

        result = await self.session.exec(statement)
        return result.all()

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
                Case.owner_id == self.workspace_id,
                Case.id == case_id,
            )
            .options(selectinload(Case.tags))  # type: ignore
        )

        result = await self.session.exec(statement)
        case = result.first()

        if case and track_view:
            try:
                created_event = await self.events.create_case_viewed_event(case)
            except Exception:
                await self.session.rollback()
                self.logger.exception(
                    "Failed to record case viewed event",
                    case_id=case_id,
                    user_id=self.role.user_id,
                )
            else:
                if created_event is not None:
                    try:
                        await self.durations.sync_case_durations(case)
                        await self.session.commit()
                    except Exception:
                        await self.session.rollback()
                        self.logger.exception(
                            "Failed to persist case viewed tracking updates",
                            case_id=case_id,
                            user_id=self.role.user_id,
                        )

        return case

    async def create_case(self, params: CaseCreate) -> Case:
        try:
            # Create the base case first
            case = Case(
                owner_id=self.workspace_id,
                summary=params.summary,
                description=params.description,
                priority=params.priority,
                severity=params.severity,
                status=params.status,
                assignee_id=params.assignee_id,
                payload=params.payload,
            )

            self.session.add(case)
            await self.session.flush()  # Generate case ID

            # Always create the fields row to ensure defaults are applied
            # Pass empty dict if no fields provided to trigger default value application
            await self.fields.create_field_values(case, params.fields or {})

            run_ctx = ctx_run.get()
            await self.events.create_event(
                case=case,
                event=CreatedEvent(wf_exec_id=run_ctx.wf_exec_id if run_ctx else None),
            )

            await self.durations.sync_case_durations(case)

            # Commit once to persist case, fields, and event atomically
            await self.session.commit()
            # Make sure to refresh the case to get the fields relationship loaded
            await self.session.refresh(case)
            return case
        except Exception:
            await self.session.rollback()
            raise

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
            # Get existing fields
            if not isinstance(fields, dict):
                raise ValueError("Fields must be a dict")

            if case_fields := case.fields:
                # Merge existing fields with new fields
                existing_fields = await self.fields.get_fields(case) or {}
                await self.fields.update_field_values(
                    case_fields.id, existing_fields | fields
                )
            else:
                # Case has no fields row yet, create one
                existing_fields: dict[str, Any] = {}
                await self.fields.create_field_values(case, fields)
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

            await self.durations.sync_case_durations(case)

            # Commit once to persist all updates and emitted events atomically
            await self.session.commit()
            await self.session.refresh(case)
            return case
        except Exception:
            await self.session.rollback()
            raise

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


class CaseFieldsService(BaseWorkspaceService):
    """Service that manages the fields table."""

    service_name = "case_fields"
    _table = CaseFields.__tablename__
    _schema = "public"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)
        self.editor = TableEditorService(
            session=self.session,
            role=self.role,
            table_name=self._table,
            schema_name=self._schema,
        )

    async def list_fields(
        self,
    ) -> Sequence[sa.engine.interfaces.ReflectedColumn]:
        """List all case fields.

        Returns:
            The case fields
        """

        return await self.editor.get_columns()

    async def create_field(self, params: CaseFieldCreate) -> None:
        """Create a new case field.

        Args:
            params: The parameters for the field to create
        """
        params.nullable = True  # For now, all fields are nullable
        await self.editor.create_column(params)
        await self.session.commit()

    async def update_field(self, field_id: str, params: CaseFieldUpdate) -> None:
        """Update a case field.

        Args:
            field_id: The name of the field to update
            params: The parameters for the field to update
        """
        await self.editor.update_column(field_id, params)
        await self.session.commit()

    async def delete_field(self, field_id: str) -> None:
        """Delete a case field.

        Args:
            field_id: The name of the field to delete
        """
        if field_id in CaseFields.model_fields:
            raise ValueError(f"Field {field_id} is a reserved field")

        await self.editor.delete_column(field_id)
        await self.session.commit()

    async def get_fields(self, case: Case) -> dict[str, Any] | None:
        """Get the fields for a case.

        Args:
            case: The case to get fields for
        """
        if case.fields is None:
            return None
        return await self.editor.get_row(case.fields.id)

    async def create_field_values(
        self, case: Case, fields: dict[str, Any]
    ) -> dict[str, Any]:
        """Add fields to a case. Non-transactional.

        Args:
            case: The case to add fields to
            fields: The fields to add
        """
        # Create a new CaseFields record with case_id
        case_fields = CaseFields(case_id=case.id)
        self.session.add(case_fields)
        await self.session.flush()  # Populate the ID

        # This will use the created case_fields ID
        try:
            if fields:
                # If fields provided, update the row with those values
                res = await self.editor.update_row(row_id=case_fields.id, data=fields)
                await self.session.flush()
                return res
            else:
                # If no fields provided, just get the row to return defaults
                res = await self.editor.get_row(row_id=case_fields.id)
                return res
        except TracecatNotFoundError as e:
            # This happens when UPDATE/SELECT finds no row - shouldn't occur after INSERT
            self.logger.error(
                "Case fields row not found after creation",
                case_fields_id=case_fields.id,
                case_id=case.id,
                fields=fields,
                error=str(e),
            )
            # Extract field names for better error message
            field_names = list(fields.keys()) if fields else []
            field_info = (
                f" Fields attempted: {', '.join(field_names)}." if field_names else ""
            )
            raise TracecatException(
                f"Failed to save custom field values for case. The field row was created but could not be updated.{field_info} "
                "Please verify all custom fields exist in Settings > Cases > Custom Fields and have correct data types."
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

    async def update_field_values(self, id: uuid.UUID, fields: dict[str, Any]) -> None:
        """Update a case field value. Non-transactional.

        Args:
            id: The id of the case field to update
            fields: The fields to update
        """
        try:
            await self.editor.update_row(id, fields)
        except ProgrammingError as e:
            while cause := e.__cause__:
                e = cause
            if isinstance(e, UndefinedColumnError):
                raise TracecatException(
                    "Failed to update case fields. One or more custom fields do not exist. Please ensure these fields have been created and try again."
                ) from e
            raise TracecatException(
                "Failed to update case fields due to an unexpected error."
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
            CaseComment.owner_id == self.workspace_id,
            CaseComment.id == comment_id,
        )

        result = await self.session.exec(statement)
        return result.first()

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
            result = await self.session.exec(statement)
            return list(result.all())
        else:
            statement = (
                select(CaseComment)
                .where(CaseComment.case_id == case.id)
                .order_by(cast(CaseComment.created_at, sa.DateTime))
            )
            result = await self.session.exec(statement)
            # Return in the same format as the join query for consistency
            return [(comment, None) for comment in result.all()]

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
            owner_id=self.workspace_id,
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
            .order_by(desc(col(CaseEvent.created_at)))
        )
        result = await self.session.exec(statement)
        return result.all()

    async def create_event(self, case: Case, event: CaseEventVariant) -> CaseEvent:
        """Create a new activity record for a case with variant-specific data.

        Note: This method is non-committing. The caller is responsible for
        wrapping operations in a transaction and committing once at the end
        to preserve atomicity across multi-step updates.
        """

        db_event = CaseEvent(
            owner_id=self.workspace_id,
            case_id=case.id,
            type=event.type,
            data=event.model_dump(exclude={"type"}, mode="json"),
            user_id=self.role.user_id,
        )
        self.session.add(db_event)
        # Flush so that generated fields (e.g., id) are available if needed
        await self.session.flush()
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
                CaseEvent.owner_id == self.workspace_id,
                CaseEvent.case_id == case.id,
                CaseEvent.type == CaseEventType.CASE_VIEWED,
                CaseEvent.user_id == self.role.user_id,
            )
            .order_by(desc(col(CaseEvent.created_at)))
            .limit(1)
        )
        result = await self.session.exec(stmt)
        last_event = result.first()
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
                CaseTask.owner_id == self.workspace_id,
                CaseTask.case_id == case_id,
            )
            .order_by(
                col(CaseTask.priority).desc(),
                cast(CaseTask.created_at, sa.DateTime),
            )
        )
        result = await self.session.exec(statement)
        return result.all()

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
            CaseTask.owner_id == self.workspace_id,
            CaseTask.id == task_id,
        )
        result = await self.session.exec(statement)
        task = result.first()
        if not task:
            raise TracecatNotFoundError(f"Task {task_id} not found")
        return task

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
            Case.owner_id == self.workspace_id,
            Case.id == case_id,
        )
        result = await self.session.exec(statement)
        case = result.first()
        if not case:
            raise TracecatNotFoundError(f"Case {case_id} not found")

        # Convert workflow_id from AnyWorkflowID to UUID
        workflow_uuid = (
            WorkflowUUID.new(params.workflow_id) if params.workflow_id else None
        )

        task = CaseTask(
            owner_id=self.workspace_id,
            case_id=case_id,
            title=params.title,
            description=params.description,
            priority=params.priority,
            status=params.status,
            assignee_id=params.assignee_id,
            workflow_id=workflow_uuid,
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
            Case.owner_id == self.workspace_id,
            Case.id == task.case_id,
        )
        result = await self.session.exec(statement)
        case = result.first()
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
            Case.owner_id == self.workspace_id,
            Case.id == task.case_id,
        )
        result = await self.session.exec(statement)
        case = result.first()
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
