import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import Any, Literal

import sqlalchemy as sa
from asyncpg import UndefinedColumnError
from sqlalchemy.exc import ProgrammingError
from sqlmodel import cast, col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.models import (
    CaseCommentCreate,
    CaseCommentUpdate,
    CaseCreate,
    CaseFieldCreate,
    CaseFieldUpdate,
    CaseUpdate,
)
from tracecat.db.schemas import Case, CaseComment, CaseFields, User
from tracecat.service import BaseWorkspaceService
from tracecat.tables.service import TableEditorService, TablesService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatAuthorizationError, TracecatException


class CasesService(BaseWorkspaceService):
    service_name = "cases"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)
        self.tables = TablesService(session=self.session, role=self.role)
        self.fields = CaseFieldsService(session=self.session, role=self.role)

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

    async def search_cases(
        self,
        search_term: str | None = None,
        status: CaseStatus | None = None,
        priority: CasePriority | None = None,
        severity: CaseSeverity | None = None,
        limit: int | None = None,
        order_by: Literal["created_at", "updated_at", "priority", "severity", "status"]
        | None = None,
        sort: Literal["asc", "desc"] | None = None,
    ) -> Sequence[Case]:
        """Search cases based on various criteria.

        Args:
            search_term: Text to search for in case summary and description
            status: Filter by case status
            priority: Filter by case priority
            severity: Filter by case severity
            limit: Maximum number of cases to return
            order_by: Field to order the cases by
            sort: Direction to sort (asc or desc)

        Returns:
            Sequence of cases matching the search criteria
        """
        statement = select(Case).where(Case.owner_id == self.workspace_id)

        # Apply search term filter (search in summary and description)
        if search_term:
            statement = statement.where(
                sa.or_(
                    col(Case.summary).ilike(f"%{search_term}%"),
                    col(Case.description).ilike(f"%{search_term}%"),
                )
            )

        # Apply status filter
        if status:
            statement = statement.where(Case.status == status)

        # Apply priority filter
        if priority:
            statement = statement.where(Case.priority == priority)

        # Apply severity filter
        if severity:
            statement = statement.where(Case.severity == severity)

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

    async def get_case(self, case_id: uuid.UUID) -> Case | None:
        """Get a case with its associated custom fields.

        Args:
            case_id: UUID of the case to retrieve

        Returns:
            Tuple containing the case and its fields (or None if no fields exist)

        Raises:
            TracecatNotFoundError: If the case doesn't exist
        """
        statement = select(Case).where(
            Case.owner_id == self.workspace_id,
            Case.id == case_id,
        )

        result = await self.session.exec(statement)
        return result.first()

    async def create_case(self, params: CaseCreate) -> Case:
        # Create the base case first
        case = Case(
            owner_id=self.workspace_id,
            summary=params.summary,
            description=params.description,
            priority=params.priority,
            severity=params.severity,
            status=params.status,
            assignee_id=params.assignee_id,
        )

        self.session.add(case)
        await self.session.flush()  # Generate case ID

        # If fields are provided, create the fields row
        if params.fields:
            await self.fields.create_field_values(case, params.fields)

        await self.session.commit()
        # Make sure to refresh the case to get the fields relationship loaded
        await self.session.refresh(case)
        return case

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

        # Update case parameters if provided
        set_fields = params.model_dump(exclude_unset=True)

        if fields := set_fields.pop("fields", None):
            # If fields was set, we need to update the fields row
            # It must be a dictionary because we validated it in the model
            # Get existing fields
            if not isinstance(fields, dict):
                raise ValueError("Fields must be a dict")

            if case_fields := case.fields:
                # Merge existing fields with new fields
                existing_fields = await self.fields.get_fields(case) or {}
                existing_fields.update(fields)
                await self.fields.update_field_values(case_fields.id, existing_fields)
            else:
                # Case has no fields row yet, create one
                await self.fields.create_field_values(case, fields)

        # Handle the rest
        for key, value in set_fields.items():
            setattr(case, key, value)

        # Commit changes and refresh case
        await self.session.commit()
        await self.session.refresh(case)
        return case

    async def delete_case(self, case: Case) -> None:
        """Delete a case and optionally its associated field data.

        Args:
            case: The case object to delete
            delete_fields: Whether to also delete the associated field data
        """
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
            res = await self.editor.update_row(row_id=case_fields.id, data=fields)
            await self.session.flush()
            return res
        except ProgrammingError as e:
            while cause := e.__cause__:
                e = cause
            if isinstance(e, UndefinedColumnError):
                raise TracecatException(
                    f"Failed to create case fields. {str(e).replace('relation', 'table').capitalize()}."
                    " Please ensure these fields have been created and try again."
                ) from e
            raise TracecatException(
                f"Unexpected error creating case fields: {e}"
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
                    f"Failed to update case fields. {str(e).replace('relation', 'table').capitalize()}."
                    " Please ensure these fields have been created and try again."
                ) from e
            raise TracecatException(
                f"Unexpected error updating case fields: {e}"
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
