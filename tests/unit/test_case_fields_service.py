import uuid
from unittest.mock import patch

import pytest
import sqlalchemy as sa
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.models import CaseFieldCreate, CaseFieldUpdate
from tracecat.cases.service import CaseFieldsService
from tracecat.db.schemas import Case, CaseFields
from tracecat.tables.enums import SqlType
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatAuthorizationError

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def case_fields_service(
    session: AsyncSession, svc_role: Role
) -> CaseFieldsService:
    """Create a case fields service instance for testing."""
    return CaseFieldsService(session=session, role=svc_role)


@pytest.fixture
async def test_case(session: AsyncSession, svc_role: Role) -> Case:
    """Create a test case for use in field tests."""
    # Create a case directly using SQLModel
    case = Case(
        owner_id=svc_role.workspace_id if svc_role.workspace_id else uuid.uuid4(),
        summary="Test Case for Fields",
        description="This is a test case for testing fields",
        status=CaseStatus.NEW,
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
    )
    session.add(case)
    await session.commit()
    await session.refresh(case)
    return case


@pytest.mark.anyio
class TestCaseFieldsService:
    async def test_init_requires_workspace_id(self, session: AsyncSession) -> None:
        """Test that service initialization requires a workspace ID."""
        # Create a role without workspace_id
        role_without_workspace = Role(
            type="service",
            user_id=uuid.uuid4(),
            workspace_id=None,
            service_id="tracecat-service",
            access_level=AccessLevel.BASIC,
        )

        # Attempt to create service without workspace should raise error
        with pytest.raises(TracecatAuthorizationError):
            CaseFieldsService(session=session, role=role_without_workspace)

    async def test_list_fields(self, case_fields_service: CaseFieldsService) -> None:
        """Test listing case fields."""
        # Mock the editor.get_columns method
        mock_columns = [
            {"name": "id", "type": sa.types.UUID(), "nullable": False},
            {"name": "case_id", "type": sa.types.UUID(), "nullable": False},
            {"name": "created_at", "type": sa.types.TIMESTAMP(), "nullable": False},
            {"name": "updated_at", "type": sa.types.TIMESTAMP(), "nullable": False},
            {"name": "custom_field1", "type": sa.types.String(), "nullable": True},
            {"name": "custom_field2", "type": sa.types.Integer(), "nullable": True},
        ]

        with patch.object(
            case_fields_service.editor, "get_columns"
        ) as mock_get_columns:
            mock_get_columns.return_value = mock_columns

            # Call the list_fields method
            fields = await case_fields_service.list_fields()

            # Verify the method returned the mock columns
            assert fields == mock_columns
            mock_get_columns.assert_called_once()

    async def test_create_field(self, case_fields_service: CaseFieldsService) -> None:
        """Test creating a case field."""
        # Create field parameters
        field_params = CaseFieldCreate(
            name="test_field",
            type=SqlType.TEXT,
        )

        # Mock the editor.create_column method
        with patch.object(
            case_fields_service.editor, "create_column"
        ) as mock_create_column:
            # Call the create_field method
            await case_fields_service.create_field(field_params)

            # Verify the method was called with the right parameters
            mock_create_column.assert_called_once_with(field_params)

    async def test_update_field(self, case_fields_service: CaseFieldsService) -> None:
        """Test updating a case field."""
        # Create field update parameters
        field_update = CaseFieldUpdate(
            name="updated_field",
        )

        # Mock the editor.update_column method
        with patch.object(
            case_fields_service.editor, "update_column"
        ) as mock_update_column:
            # Call the update_field method
            await case_fields_service.update_field("test_field", field_update)

            # Verify the method was called with the right parameters
            mock_update_column.assert_called_once_with("test_field", field_update)

    async def test_delete_field(self, case_fields_service: CaseFieldsService) -> None:
        """Test deleting a case field."""
        # Mock the editor.delete_column method
        with patch.object(
            case_fields_service.editor, "delete_column"
        ) as mock_delete_column:
            # Call the delete_field method
            await case_fields_service.delete_field("test_field")

            # Verify the method was called with the right parameters
            mock_delete_column.assert_called_once_with("test_field")

    async def test_delete_field_reserved(
        self, case_fields_service: CaseFieldsService
    ) -> None:
        """Test that deleting a reserved field raises an error."""
        # Try to delete a reserved field (using a field from the CaseFields model)
        with pytest.raises(ValueError, match="Field case_id is a reserved field"):
            await case_fields_service.delete_field("case_id")

    async def test_get_fields_none(
        self, case_fields_service: CaseFieldsService, test_case: Case
    ) -> None:
        """Test getting fields when case has no fields."""
        # Ensure the case has no fields
        test_case.fields = None

        # Get fields for the case
        fields = await case_fields_service.get_fields(test_case)

        # Verify no fields were returned
        assert fields is None

    async def test_get_fields(
        self,
        case_fields_service: CaseFieldsService,
        test_case: Case,
        session: AsyncSession,
    ) -> None:
        """Test getting fields for a case with fields."""
        # Create a CaseFields object for the test case
        case_fields = CaseFields(case_id=test_case.id)
        session.add(case_fields)
        await session.commit()

        # Update the test_case with the fields
        test_case.fields = case_fields
        await session.commit()

        # Mock the editor.get_row method to return a response with a 'data' field
        mock_fields_data = {
            "id": case_fields.id,
            "case_id": test_case.id,
            "data": {
                "custom_field1": "test value",
                "custom_field2": 123,
            },
        }

        with patch.object(case_fields_service.editor, "get_row") as mock_get_row:
            mock_get_row.return_value = mock_fields_data

            # Get fields for the case
            fields = await case_fields_service.get_fields(test_case)

            # Verify the fields were returned correctly
            assert fields == mock_fields_data
            mock_get_row.assert_called_once_with(case_fields.id)

    async def test_create_field_values(
        self, case_fields_service: CaseFieldsService, test_case: Case
    ) -> None:
        """Test inserting field values for a case."""
        # Field values to insert
        fields_data = {"custom_field1": "test value", "custom_field2": 123}

        # Mock result from editor.update_row - now includes a 'data' field
        mock_result = {"id": uuid.uuid4(), "case_id": test_case.id, **fields_data}

        # Mock the editor.update_row method
        with patch.object(case_fields_service.editor, "update_row") as mock_update_row:
            mock_update_row.return_value = mock_result

            # Insert field values
            result = await case_fields_service.create_field_values(
                test_case, fields_data
            )

            # Verify the result matches the full mock_result, not just fields_data
            assert result == mock_result
            mock_update_row.assert_called_once()

            # Verify the call arguments - the actual structure is {'row_id': UUID, 'data': {...}}
            call_kwargs = mock_update_row.call_args.kwargs
            assert "row_id" in call_kwargs
            assert "data" in call_kwargs
            assert call_kwargs["data"] == fields_data

    async def test_update_field_values(
        self, case_fields_service: CaseFieldsService
    ) -> None:
        """Test updating field values."""
        # Create a UUID for the fields row
        fields_id = uuid.uuid4()

        # Field values to update
        field_values = {"custom_field1": "updated value", "custom_field2": 456}

        # Mock the editor.update_row method
        with patch.object(case_fields_service.editor, "update_row") as mock_update_row:
            # Update field values
            await case_fields_service.update_field_values(fields_id, field_values)

            # Verify the method was called with the right parameters
            # The field values are passed directly, not wrapped in a 'data' object
            mock_update_row.assert_called_once_with(fields_id, field_values)
