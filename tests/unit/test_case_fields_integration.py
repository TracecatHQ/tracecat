import pytest
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.models import CaseCreate, CaseFieldCreate, CaseUpdate
from tracecat.cases.service import CaseFieldsService, CasesService
from tracecat.db.schemas import Case, CaseFields, User
from tracecat.tables.enums import SqlType
from tracecat.types.auth import Role

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def cases_service(session: AsyncSession, svc_role: Role) -> CasesService:
    """Create a cases service instance for testing."""
    return CasesService(session=session, role=svc_role)


@pytest.fixture
async def case_fields_service(
    session: AsyncSession, svc_admin_role: Role
) -> CaseFieldsService:
    """Create a case fields service instance for testing."""
    return CaseFieldsService(session=session, role=svc_admin_role)


@pytest.fixture
def case_create_params() -> CaseCreate:
    """Sample case creation parameters."""
    return CaseCreate(
        summary="Test Case",
        description="This is a test case for integration testing",
        status=CaseStatus.NEW,
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
    )


@pytest.mark.anyio
class TestCaseFieldsIntegration:
    async def test_create_case_without_fields(
        self,
        cases_service: CasesService,
        case_create_params: CaseCreate,
        session: AsyncSession,
    ) -> None:
        """Test creating a case without fields and verify no fields are created."""
        # Create case without fields using base parameters
        created_case = await cases_service.create_case(case_create_params)

        # Verify case was created successfully
        assert created_case.summary == case_create_params.summary
        assert created_case.description == case_create_params.description

        # Verify that a CaseFields row WAS created (new behavior: always create to ensure defaults)
        assert created_case.fields is not None

        # Query the database to verify fields row exists for this case
        statement = select(CaseFields).where(CaseFields.case_id == created_case.id)
        result = await session.exec(statement)
        case_fields = result.one_or_none()
        assert case_fields is not None

        # Verify get_fields returns the row with metadata but no custom fields
        fields_data = await cases_service.fields.get_fields(created_case)
        assert fields_data is not None
        # Should have metadata fields but no custom fields
        assert "id" in fields_data
        assert "case_id" in fields_data
        assert "created_at" in fields_data
        assert "updated_at" in fields_data
        # No other fields should be present (i.e., no custom fields)
        assert len(fields_data) == 4

        # Verify get_case returns the same case with fields row
        retrieved_case = await cases_service.get_case(created_case.id)
        assert retrieved_case is not None
        assert retrieved_case.id == created_case.id
        assert retrieved_case.fields is not None

    async def test_create_case_with_fields_before_columns_exist(
        self, cases_service: CasesService, case_create_params: CaseCreate
    ) -> None:
        """Test creating a case with fields and verify the fields are properly saved."""
        # Create case parameters with fields
        params_with_fields = CaseCreate(
            summary=case_create_params.summary,
            description=case_create_params.description,
            status=case_create_params.status,
            priority=case_create_params.priority,
            severity=case_create_params.severity,
            fields={"custom_field1": "test value", "custom_field2": 123},
        )

        # Create case with fields
        # Since we haven't created the fields yet, this should raise an error
        with pytest.raises(ValueError):
            await cases_service.create_case(params_with_fields)

    async def test_create_case_with_fields(
        self,
        cases_service: CasesService,
        case_fields_service: CaseFieldsService,
        case_create_params: CaseCreate,
        session: AsyncSession,
    ) -> None:
        """Test creating a case with fields and verify the fields are properly saved."""
        # Create the fields
        await case_fields_service.create_field(
            CaseFieldCreate(
                name="custom_field1",
                type=SqlType.TEXT,
            )
        )
        await case_fields_service.create_field(
            CaseFieldCreate(
                name="custom_field2",
                type=SqlType.INTEGER,
            )
        )
        # Create case parameters with fields
        params_with_fields = CaseCreate(
            summary=case_create_params.summary,
            description=case_create_params.description,
            status=case_create_params.status,
            priority=case_create_params.priority,
            severity=case_create_params.severity,
            fields={"custom_field1": "test value", "custom_field2": 123},
        )

        created_case = await cases_service.create_case(params_with_fields)

        # Verify case was created successfully
        assert created_case.summary == params_with_fields.summary
        assert created_case.description == params_with_fields.description

        # Verify that fields were created and associated with the case
        assert created_case.fields is not None
        assert created_case.fields.case_id == created_case.id

        # Query the fields directly from the database to verify
        statement = select(CaseFields).where(CaseFields.case_id == created_case.id)
        result = await session.exec(statement)
        case_fields = result.one_or_none()
        assert case_fields is not None

        # Now check the values of the fields
        fields_data = await cases_service.fields.get_fields(created_case)
        assert fields_data is not None
        assert fields_data["custom_field1"] == "test value"
        assert fields_data["custom_field2"] == 123

    async def test_add_custom_field_and_use_it(
        self,
        cases_service: CasesService,
        case_fields_service: CaseFieldsService,
        case_create_params: CaseCreate,
        session: AsyncSession,
    ) -> None:
        """Test adding a custom field schema and then using it in a case."""
        # First, create a custom field schema
        field_params = CaseFieldCreate(
            name="priority_reason",
            type=SqlType.TEXT,
        )
        await case_fields_service.create_field(field_params)

        # Create a case with the custom field
        params_with_fields = CaseCreate(
            summary=case_create_params.summary,
            description=case_create_params.description,
            status=case_create_params.status,
            priority=case_create_params.priority,
            severity=case_create_params.severity,
            fields={"priority_reason": "Critical customer impact"},
        )

        # Create case with fields
        created_case = await cases_service.create_case(params_with_fields)

        # Verify the custom field was saved
        fields_data = await cases_service.fields.get_fields(created_case)
        assert fields_data is not None
        assert fields_data["priority_reason"] == "Critical customer impact"

        # Update the field value
        update_params = CaseUpdate(
            fields={"priority_reason": "Updated reason: Affects multiple customers"}
        )
        updated_case = await cases_service.update_case(created_case, update_params)

        # Verify the field was updated
        updated_fields = await cases_service.fields.get_fields(updated_case)
        assert updated_fields is not None
        assert (
            updated_fields["priority_reason"]
            == "Updated reason: Affects multiple customers"
        )

    async def test_update_existing_case_fields(
        self,
        cases_service: CasesService,
        case_fields_service: CaseFieldsService,
        case_create_params: CaseCreate,
        session: AsyncSession,
    ) -> None:
        """Test updating fields for a case that already has fields."""
        # Create the fields
        await case_fields_service.create_field(
            CaseFieldCreate(
                name="field1",
                type=SqlType.TEXT,
            )
        )
        await case_fields_service.create_field(
            CaseFieldCreate(
                name="field2",
                type=SqlType.INTEGER,
            )
        )

        # Create a case with initial fields
        params_with_fields = CaseCreate(
            summary=case_create_params.summary,
            description=case_create_params.description,
            status=case_create_params.status,
            priority=case_create_params.priority,
            severity=case_create_params.severity,
            fields={"field1": "initial value", "field2": 100},
        )

        # Create case with fields
        created_case = await cases_service.create_case(params_with_fields)

        # Verify initial fields
        initial_fields = await cases_service.fields.get_fields(created_case)
        assert initial_fields is not None
        assert initial_fields["field1"] == "initial value"
        assert initial_fields["field2"] == 100

        # Update fields: modify one existing field and add a new field
        update_params = CaseUpdate(
            fields={"field1": "updated value", "field3": "new field"}
        )

        # Update the case
        with pytest.raises(ValueError):
            await cases_service.update_case(created_case, update_params)

    async def test_case_fields_cascade_delete(
        self,
        cases_service: CasesService,
        case_fields_service: CaseFieldsService,
        case_create_params: CaseCreate,
        session: AsyncSession,
    ) -> None:
        """Test that deleting a case properly cascades to its fields."""
        # Create the fields
        await case_fields_service.create_field(
            CaseFieldCreate(
                name="test_field",
                type=SqlType.TEXT,
            )
        )
        # Create a case with fields
        params_with_fields = CaseCreate(
            summary=case_create_params.summary,
            description=case_create_params.description,
            status=case_create_params.status,
            priority=case_create_params.priority,
            severity=case_create_params.severity,
            fields={"test_field": "test value"},
        )

        # Create case with fields
        created_case = await cases_service.create_case(params_with_fields)
        case_id = created_case.id

        # Verify case and fields exist
        assert created_case.fields is not None
        fields_id = created_case.fields.id

        # Delete the case
        await cases_service.delete_case(created_case)

        # Verify case is deleted
        case_statement = select(Case).where(Case.id == case_id)
        case_result = await session.exec(case_statement)
        assert case_result.first() is None

        # Verify fields were also deleted due to cascade delete
        fields_statement = select(CaseFields).where(CaseFields.id == fields_id)
        fields_result = await session.exec(fields_statement)
        assert fields_result.one_or_none() is None


@pytest.mark.anyio
class TestCaseAssigneeIntegration:
    @pytest.fixture
    async def test_user(self, session: AsyncSession) -> User:
        """Create a test user for assignment."""
        # Create an actual user in the database for testing assignee relationship
        user = User(
            email="test-assignee@example.com",
            hashed_password="hashed_password_for_testing",
            is_active=True,
            is_verified=True,
            is_superuser=False,
            last_login_at=None,
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user

    async def test_create_case_with_assignee(
        self,
        cases_service: CasesService,
        case_create_params: CaseCreate,
        test_user: User,
        session: AsyncSession,
    ) -> None:
        """Test creating a case with an assignee and verify the relationship is stored correctly."""
        # Assign the case to our test user
        case_create_params.assignee_id = test_user.id

        # Create case
        created_case = await cases_service.create_case(case_create_params)

        # Verify assignee was set
        assert created_case.assignee_id == test_user.id

        # Verify the case can be retrieved with assignee
        retrieved_case = await cases_service.get_case(created_case.id)
        assert retrieved_case is not None
        assert retrieved_case.assignee_id == test_user.id

        # Verify database state directly
        statement = select(Case).where(Case.id == created_case.id)
        result = await session.exec(statement)
        case_from_db = result.one()
        assert case_from_db.assignee_id == test_user.id

    async def test_update_case_assignee(
        self,
        cases_service: CasesService,
        case_create_params: CaseCreate,
        test_user: User,
        session: AsyncSession,
    ) -> None:
        """Test updating a case to add an assignee."""
        # Create case without assignee
        created_case = await cases_service.create_case(case_create_params)
        assert created_case.assignee_id is None

        # Update case with assignee
        update_params = CaseUpdate(assignee_id=test_user.id)
        updated_case = await cases_service.update_case(created_case, update_params)

        # Verify assignee was set
        assert updated_case.assignee_id == test_user.id

        # Verify database state directly
        statement = select(Case).where(Case.id == created_case.id)
        result = await session.exec(statement)
        case_from_db = result.one()
        assert case_from_db.assignee_id == test_user.id

    async def test_remove_case_assignee(
        self,
        cases_service: CasesService,
        case_create_params: CaseCreate,
        test_user: User,
        session: AsyncSession,
    ) -> None:
        """Test removing an assignee from a case."""
        # Create case with assignee
        case_create_params.assignee_id = test_user.id
        created_case = await cases_service.create_case(case_create_params)
        assert created_case.assignee_id == test_user.id

        # Update to remove assignee
        update_params = CaseUpdate(assignee_id=None)
        updated_case = await cases_service.update_case(created_case, update_params)

        # Verify assignee was removed
        assert updated_case.assignee_id is None

        # Verify database state directly
        statement = select(Case).where(Case.id == created_case.id)
        result = await session.exec(statement)
        case_from_db = result.one()
        assert case_from_db.assignee_id is None

    async def test_list_cases_with_assignee_filtering(
        self,
        cases_service: CasesService,
        case_create_params: CaseCreate,
        test_user: User,
        session: AsyncSession,
    ) -> None:
        """Test that cases can be filtered by assignee."""
        # Create cases with and without assignees
        case1 = await cases_service.create_case(case_create_params)

        assigned_params = CaseCreate(
            summary="Assigned Case",
            description="This case has an assignee",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
            assignee_id=test_user.id,
        )
        case2 = await cases_service.create_case(assigned_params)

        # Get all cases
        all_cases = await cases_service.list_cases()
        assert len(all_cases) >= 2

        # Verify at least one case with our test user
        cases_with_assignee = [
            case for case in all_cases if case.assignee_id == test_user.id
        ]
        assert len(cases_with_assignee) >= 1
        assert case2.id in [case.id for case in cases_with_assignee]

        # Verify at least one case without assignee
        cases_without_assignee = [
            case for case in all_cases if case.assignee_id is None
        ]
        assert len(cases_without_assignee) >= 1
        assert case1.id in [case.id for case in cases_without_assignee]
