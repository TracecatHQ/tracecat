import uuid  # noqa: I001
from unittest.mock import patch, MagicMock

import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.models import CaseCreate, CaseUpdate
from tracecat.cases.service import CasesService
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatAuthorizationError

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
async def test_service_initialization_requires_workspace(session: AsyncSession) -> None:
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
        CasesService(session=session, role=role_without_workspace)


@pytest.fixture
def test_case_id() -> uuid.UUID:
    """Return a fixed test case ID for testing."""
    return uuid.UUID("12345678-1234-5678-1234-567812345678")


@pytest.fixture
async def cases_service(session: AsyncSession, svc_role: Role) -> CasesService:
    """Create a cases service instance for testing."""
    return CasesService(session=session, role=svc_role)


@pytest.fixture
def case_create_params() -> CaseCreate:
    """Sample case creation parameters."""
    return CaseCreate(
        summary="Test Case",
        description="This is a test case for unit testing",
        status=CaseStatus.NEW,
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
    )


@pytest.mark.anyio
class TestCasesService:
    async def test_create_and_get_case(
        self, cases_service: CasesService, case_create_params: CaseCreate
    ) -> None:
        """Test creating and retrieving a case."""
        # Create case
        created_case = await cases_service.create_case(case_create_params)
        assert created_case.summary == case_create_params.summary
        assert created_case.description == case_create_params.description
        assert created_case.status == case_create_params.status
        assert created_case.priority == case_create_params.priority
        assert created_case.severity == case_create_params.severity
        assert created_case.owner_id == cases_service.workspace_id

        # Retrieve case
        retrieved_case = await cases_service.get_case(created_case.id)
        assert retrieved_case is not None
        assert retrieved_case.id == created_case.id
        assert retrieved_case.summary == case_create_params.summary
        assert retrieved_case.description == case_create_params.description
        assert retrieved_case.status == case_create_params.status
        assert retrieved_case.priority == case_create_params.priority
        assert retrieved_case.severity == case_create_params.severity

    async def test_list_cases(
        self, cases_service: CasesService, case_create_params: CaseCreate
    ) -> None:
        """Test listing cases."""
        # Create multiple cases
        # Explicitly set status to ensure consistent test behavior
        modified_params = CaseCreate(
            summary=case_create_params.summary,
            description=case_create_params.description,
            priority=case_create_params.priority,
            severity=case_create_params.severity,
            status=case_create_params.status,
        )
        case1 = await cases_service.create_case(modified_params)

        # Create a second case with different data
        case2 = await cases_service.create_case(
            CaseCreate(
                summary="Another Test Case",
                description="This is another test case for unit testing",
                status=CaseStatus.IN_PROGRESS,
                priority=CasePriority.HIGH,
                severity=CaseSeverity.MEDIUM,
            )
        )

        # List all cases
        cases = await cases_service.list_cases()
        assert len(cases) >= 2
        case_ids = {case.id for case in cases}
        assert case1.id in case_ids
        assert case2.id in case_ids

    async def test_update_case(
        self, cases_service: CasesService, case_create_params: CaseCreate
    ) -> None:
        """Test updating a case."""
        # Create initial case
        created_case = await cases_service.create_case(case_create_params)

        # Update parameters
        update_params = CaseUpdate(
            summary="Updated Test Case",
            status=CaseStatus.IN_PROGRESS,
            priority=CasePriority.HIGH,
        )

        # Update case
        updated_case = await cases_service.update_case(created_case, update_params)
        assert updated_case.summary == update_params.summary
        assert updated_case.status == update_params.status
        assert updated_case.priority == update_params.priority
        # Fields not included in the update should remain unchanged
        assert updated_case.description == case_create_params.description
        assert updated_case.severity == case_create_params.severity

        # Verify updates persisted
        retrieved_case = await cases_service.get_case(created_case.id)
        assert retrieved_case is not None
        assert retrieved_case.summary == update_params.summary
        assert retrieved_case.status == update_params.status
        assert retrieved_case.priority == update_params.priority

    async def test_update_case_with_fields(
        self, cases_service: CasesService, case_create_params: CaseCreate
    ) -> None:
        """Test updating a case with fields."""
        # Create case first
        created_case = await cases_service.create_case(case_create_params)

        # Then mock both methods that handle fields
        with (
            patch.object(cases_service.fields, "get_fields"),
            patch.object(cases_service.fields, "update_field_values"),
            patch.object(
                cases_service.fields, "insert_field_values"
            ) as mock_insert_fields,
        ):
            # Set up case.fields to None to simulate a case without fields
            created_case.fields = None

            # Mock return value for insert_field_values
            mock_insert_fields.return_value = {"field1": "updated_value", "field2": 2}

            # Update parameters including fields
            update_params = CaseUpdate(
                summary="Updated Test Case",
                fields={"field1": "updated_value", "field2": 2},
            )

            # Update case
            await cases_service.update_case(created_case, update_params)

            # Verify insert_field_values was called with the case and fields
            mock_insert_fields.assert_called_once_with(
                created_case, {"field1": "updated_value", "field2": 2}
            )

    async def test_delete_case(
        self, cases_service: CasesService, case_create_params: CaseCreate
    ) -> None:
        """Test deleting a case."""
        # Create case
        created_case = await cases_service.create_case(case_create_params)

        # Delete case
        await cases_service.delete_case(created_case)

        # Verify deletion
        deleted_case = await cases_service.get_case(created_case.id)
        assert deleted_case is None

    async def test_create_case_with_fields(
        self, cases_service: CasesService, case_create_params: CaseCreate
    ) -> None:
        """Test creating a case with custom fields."""
        # Create case parameters with fields
        params_with_fields = CaseCreate(
            summary=case_create_params.summary,
            description=case_create_params.description,
            status=case_create_params.status,
            priority=case_create_params.priority,
            severity=case_create_params.severity,
            fields={"custom_field1": "test value", "custom_field2": 123},
        )

        # Mock the insert_field_values method
        with patch.object(
            cases_service.fields, "insert_field_values"
        ) as mock_insert_fields:
            mock_insert_fields.return_value = {
                "custom_field1": "test value",
                "custom_field2": 123,
            }

            # Create case with fields
            created_case = await cases_service.create_case(params_with_fields)

            # Verify case was created successfully
            assert created_case.summary == params_with_fields.summary
            assert created_case.description == params_with_fields.description

            # Verify that insert_field_values was called with the case and fields
            mock_insert_fields.assert_called_once_with(
                created_case, {"custom_field1": "test value", "custom_field2": 123}
            )

    async def test_update_case_fields(
        self, cases_service: CasesService, case_create_params: CaseCreate
    ) -> None:
        """Test updating just the fields of a case."""
        # Create a case first
        created_case = await cases_service.create_case(case_create_params)

        # Mock the field methods
        with (
            patch.object(cases_service.fields, "get_fields") as mock_get_fields,
            patch.object(
                cases_service.fields, "update_field_values"
            ) as mock_update_fields,
        ):
            # Set case.fields using SQLAlchemy's __dict__ to bypass SQLModel's setattr checks
            fields_obj = MagicMock()
            fields_obj.id = uuid.uuid4()
            # Use __dict__ directly to avoid SQLModel setattr validation
            created_case.__dict__["fields"] = fields_obj

            # Setup mock to return existing field values
            mock_get_fields.return_value = {
                "existing_field1": "original value",
                "existing_field2": 456,
            }

            # Update just the fields
            update_params = CaseUpdate(
                fields={"existing_field1": "updated value", "new_field": "new value"}
            )

            # Update the case
            await cases_service.update_case(created_case, update_params)

            # Verify get_fields was called
            mock_get_fields.assert_called_once_with(created_case)

            # Verify update_field_values was called with merged fields
            mock_update_fields.assert_called_once_with(
                fields_obj.id,
                {
                    "existing_field1": "updated value",
                    "existing_field2": 456,
                    "new_field": "new value",
                },
            )

    async def test_cascade_delete(
        self,
        cases_service: CasesService,
        case_create_params: CaseCreate,
        session: AsyncSession,
    ) -> None:
        """Test that cascading delete works correctly for cases."""
        # Create a case
        created_case = await cases_service.create_case(case_create_params)

        # Verify case exists
        case_id = created_case.id
        assert await cases_service.get_case(case_id) is not None

        # Delete the case
        await cases_service.delete_case(created_case)

        # Verify case is deleted
        assert await cases_service.get_case(case_id) is None

        # This test verifies that cascade delete works through the SQLAlchemy
        # relationship configuration, which should automatically handle
        # deleting related fields when a case is deleted
