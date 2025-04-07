import uuid
from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.cases.models import (
    CaseCreate,
    CaseFieldCreate,
    CaseFieldUpdate,
    CaseUpdate,
)
from tracecat.db.schemas import Case, CaseFields
from tracecat.service import BaseService
from tracecat.tables.models import TableRowInsert
from tracecat.tables.service import TableEditorService, TablesService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatAuthorizationError


class CasesService(BaseService):
    service_name = "cases"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)
        if self.role.workspace_id is None:
            raise TracecatAuthorizationError("Cases service requires workspace")
        self.workspace_id = self.role.workspace_id
        self.tables = TablesService(session=self.session, role=self.role)
        self.fields = CaseFieldsService(session=self.session, role=self.role)

    async def list_cases(self) -> Sequence[Case]:
        statement = select(Case).where(Case.owner_id == self.workspace_id)
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
        )

        self.session.add(case)
        await self.session.flush()  # Generate case ID

        # If fields are provided, create the fields row
        if params.fields:
            await self.fields.insert_field_values(case, params.fields)

        await self.session.commit()
        # Make sure to refresh the case to get the fields relationship loaded
        await self.session.refresh(case)
        self.logger.warning("Created case", case=case, fields=case.fields)
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
                self.logger.info(
                    "Existing fields",
                    existing_fields=existing_fields,
                    fields=fields,
                )
                existing_fields.update(fields)
                await self.fields.update_field_values(case_fields.id, existing_fields)
            else:
                await self.fields.insert_field_values(case, fields)

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


class CaseFieldsService(BaseService):
    """Service that manages the fields table."""

    service_name = "case_fields"
    _table = CaseFields.__tablename__
    _schema = "public"

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role)
        if self.role.workspace_id is None:
            raise TracecatAuthorizationError("Case fields service requires workspace")
        self.workspace_id = self.role.workspace_id
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

    async def insert_field_values(
        self, case: Case, fields: dict[str, Any]
    ) -> dict[str, Any]:
        """Add fields to a case. Non-transactional.

        Args:
            case: The case to add fields to
            fields: The fields to add
        """
        # This will automatically set the foreign key to the case
        res = await self.editor.insert_row(
            TableRowInsert(data={"case_id": case.id, **fields})
        )
        await self.session.flush()
        return res

    async def update_field_values(self, id: uuid.UUID, fields: dict[str, Any]) -> None:
        """Update a case field value. Non-transactional.

        Args:
            id: The id of the case field to update
            fields: The fields to update
        """
        await self.editor.update_row(id, fields)
