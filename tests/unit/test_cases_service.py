import uuid  # noqa: I001
import asyncio
from collections.abc import Iterator
from decimal import Decimal
from datetime import UTC, datetime
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload as sa_selectinload

from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.cases.dropdowns.schemas import (
    CaseDropdownDefinitionCreate,
    CaseDropdownOptionCreate,
    CaseDropdownValueInput,
)
from tracecat.cases.dropdowns.service import (
    CaseDropdownDefinitionsService,
    CaseDropdownValuesService,
)
from tracecat.cases.enums import (
    CaseEventType,
    CasePriority,
    CaseSeverity,
    CaseStatus,
)
from tracecat.cases.schemas import (
    CaseCreate,
    CaseFieldCreate,
    CaseReadMinimal,
    CaseUpdate,
)
from tracecat.tags.schemas import TagCreate
from tracecat.cases.service import CaseFieldsService, CasesService
from tracecat.cases.tags.service import CaseTagsService
from tracecat.db.models import Case, Workspace
from tracecat.exceptions import TracecatAuthorizationError
from tracecat.pagination import CursorPaginationParams
from tracecat.tables.enums import SqlType

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def stub_case_duration_sync() -> Iterator[None]:
    with patch(
        "tracecat.cases.service.CaseDurationService.sync_case_durations",
        new=AsyncMock(return_value=None),
    ):
        yield


@pytest.fixture(autouse=True)
def stub_case_addons_entitlement() -> Iterator[None]:
    with patch.object(
        CasesService,
        "has_entitlement",
        new=AsyncMock(return_value=True),
    ):
        yield


@pytest.mark.anyio
async def test_service_initialization_requires_workspace(session: AsyncSession) -> None:
    """Test that service initialization requires a workspace ID."""
    # Create a role without workspace_id
    role_without_workspace = Role(
        type="service",
        user_id=uuid.uuid4(),
        workspace_id=None,
        service_id="tracecat-service",
        scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-service"],
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
        description="This is a test case for unit testing",
        status=CaseStatus.NEW,
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
    )


async def _list_cases(
    cases_service: CasesService,
    *,
    limit: int = 100,
    order_by: Literal[
        "created_at", "updated_at", "priority", "severity", "status", "tasks"
    ]
    | None = None,
    sort: Literal["asc", "desc"] | None = None,
) -> list[CaseReadMinimal]:
    response = await cases_service.list_cases(
        limit=limit,
        order_by=order_by,
        sort=sort,
    )
    return response.items


async def _create_dropdown_with_option(
    cases_service: CasesService,
    *,
    definition_name: str,
    definition_ref: str,
    option_label: str,
    option_ref: str,
):
    definitions_service = CaseDropdownDefinitionsService(
        session=cases_service.session, role=cases_service.role
    )
    definition = await definitions_service.create_definition(
        CaseDropdownDefinitionCreate(
            name=definition_name,
            ref=definition_ref,
            is_ordered=False,
            options=[],
        )
    )
    option = await definitions_service.add_option(
        definition.id,
        CaseDropdownOptionCreate(
            label=option_label,
            ref=option_ref,
            position=0,
        ),
    )
    return definition, option


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
        assert created_case.workspace_id == cases_service.workspace_id

        # Retrieve case
        retrieved_case = await cases_service.get_case(created_case.id)
        assert retrieved_case is not None
        assert retrieved_case.id == created_case.id
        assert retrieved_case.summary == case_create_params.summary
        assert retrieved_case.description == case_create_params.description
        assert retrieved_case.status == case_create_params.status
        assert retrieved_case.priority == case_create_params.priority
        assert retrieved_case.severity == case_create_params.severity

    async def test_create_and_get_case_with_assignee(
        self,
        cases_service: CasesService,
        case_create_params: CaseCreate,
        session: AsyncSession,
    ) -> None:
        """Test creating and retrieving a case with an assignee."""
        # For this test, we'll mock the assignee validation by patching the method
        with patch(
            "tracecat.cases.service.CasesService.create_case"
        ) as mock_create_case:
            # Create a mock case with assignee_id
            mock_case = MagicMock()
            mock_case.assignee_id = uuid.uuid4()
            mock_create_case.return_value = mock_case

            # Add user ID as assignee to params
            case_create_params.assignee_id = mock_case.assignee_id

            # Call the mocked method
            result = await cases_service.create_case(case_create_params)

            # Verify assignee ID is set correctly
            assert result.assignee_id == case_create_params.assignee_id
            mock_create_case.assert_called_once()

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
        cases = await _list_cases(cases_service)
        assert len(cases) >= 2
        case_ids = {case.id for case in cases}
        assert case1.id in case_ids
        assert case2.id in case_ids

    async def test_create_case_allocates_consecutive_workspace_case_numbers(
        self, cases_service: CasesService
    ) -> None:
        """Case numbers should advance monotonically within one workspace."""
        first_case = await cases_service.create_case(
            CaseCreate(
                summary="First numbered case",
                description="First numbered case",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.LOW,
            )
        )
        second_case = await cases_service.create_case(
            CaseCreate(
                summary="Second numbered case",
                description="Second numbered case",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.LOW,
            )
        )

        assert first_case.case_number == 1
        assert first_case.short_id == "CASE-0001"
        assert second_case.case_number == 2
        assert second_case.short_id == "CASE-0002"

    async def test_create_case_allows_duplicate_short_ids_across_workspaces(
        self,
        cases_service: CasesService,
        session: AsyncSession,
        svc_organization,
    ) -> None:
        """Different workspaces should each be able to allocate CASE-0001."""
        first_case = await cases_service.create_case(
            CaseCreate(
                summary="Workspace one",
                description="Workspace one",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.LOW,
            )
        )
        second_workspace = Workspace(
            name="second-workspace",
            organization_id=svc_organization.id,
        )
        session.add(second_workspace)
        await session.commit()

        second_role = cases_service.role.model_copy(
            update={
                "workspace_id": second_workspace.id,
                "organization_id": second_workspace.organization_id,
            }
        )
        second_service = CasesService(session=session, role=second_role)
        second_case = await second_service.create_case(
            CaseCreate(
                summary="Workspace two",
                description="Workspace two",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.LOW,
            )
        )

        assert first_case.case_number == 1
        assert second_case.case_number == 1
        assert first_case.short_id == second_case.short_id == "CASE-0001"

    async def test_search_cases_short_id_is_workspace_scoped(
        self,
        cases_service: CasesService,
        session: AsyncSession,
        svc_organization,
    ) -> None:
        """Exact short ID search should not cross workspace boundaries."""
        target_case = await cases_service.create_case(
            CaseCreate(
                summary="Workspace one target",
                description="Workspace one target",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.LOW,
            )
        )
        second_workspace = Workspace(
            name="search-scope-workspace",
            organization_id=svc_organization.id,
        )
        session.add(second_workspace)
        await session.commit()

        second_role = cases_service.role.model_copy(
            update={
                "workspace_id": second_workspace.id,
                "organization_id": second_workspace.organization_id,
            }
        )
        second_service = CasesService(session=session, role=second_role)
        second_case = await second_service.create_case(
            CaseCreate(
                summary="Workspace two target",
                description="Workspace two target",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.LOW,
            )
        )

        assert target_case.short_id == second_case.short_id == "CASE-0001"

        params = CursorPaginationParams(limit=20, cursor=None, reverse=False)
        response = await cases_service.search_cases(
            params=params,
            short_id="CASE-0001",
            order_by="created_at",
            sort="asc",
        )

        result_ids = {item.id for item in response.items}
        assert target_case.id in result_ids
        assert second_case.id not in result_ids

    async def test_create_case_concurrently_allocates_unique_workspace_numbers(
        self,
        cases_service: CasesService,
        session: AsyncSession,
        svc_role: Role,
        svc_workspace: Workspace,
    ) -> None:
        """Concurrent creates should serialize on the workspace counter row."""
        await cases_service.fields._ensure_schema_ready()
        await cases_service.session.commit()
        assert session.bind is not None
        session_factory = async_sessionmaker(bind=session.bind, expire_on_commit=False)

        async def create_case(index: int) -> int:
            async with session_factory() as concurrent_session:
                service = CasesService(
                    session=concurrent_session,
                    role=svc_role.model_copy(deep=True),
                )
                case = await service.create_case(
                    CaseCreate(
                        summary=f"Concurrent case {index}",
                        description=f"Concurrent case {index}",
                        status=CaseStatus.NEW,
                        priority=CasePriority.MEDIUM,
                        severity=CaseSeverity.LOW,
                    )
                )
                return case.case_number

        with patch.object(
            CaseFieldsService,
            "_ensure_schema_ready",
            new=AsyncMock(return_value=None),
        ):
            case_numbers = await asyncio.gather(*(create_case(i) for i in range(5)))

        assert sorted(case_numbers) == [1, 2, 3, 4, 5]

        async with session_factory() as verification_session:
            workspace = await verification_session.scalar(
                select(Workspace).where(Workspace.id == svc_workspace.id)
            )
            assert workspace is not None
            assert workspace.last_case_number == 5

            stored_case_numbers = (
                await verification_session.execute(
                    select(Case.case_number).where(
                        Case.workspace_id == svc_workspace.id
                    )
                )
            ).scalars()
            assert sorted(stored_case_numbers.all()) == [1, 2, 3, 4, 5]

    async def test_list_cases_with_limit(
        self, cases_service: CasesService, case_create_params: CaseCreate
    ) -> None:
        """Test listing cases with a limit."""
        # Create multiple cases
        for i in range(3):
            await cases_service.create_case(
                CaseCreate(
                    summary=f"Test Case {i}",
                    description=f"Description for test case {i}",
                    status=CaseStatus.NEW,
                    priority=CasePriority.MEDIUM,
                    severity=CaseSeverity.LOW,
                )
            )

        # List cases with limit
        cases = await _list_cases(cases_service, limit=2)
        assert len(cases) == 2

    async def test_list_cases_with_order_by(self, cases_service: CasesService) -> None:
        """Test listing cases with order_by parameter."""
        # Create cases with different priorities to test ordering
        low_priority_case = await cases_service.create_case(
            CaseCreate(
                summary="Low Priority Case",
                description="This is a low priority case",
                status=CaseStatus.NEW,
                priority=CasePriority.LOW,
                severity=CaseSeverity.LOW,
            )
        )

        high_priority_case = await cases_service.create_case(
            CaseCreate(
                summary="High Priority Case",
                description="This is a high priority case",
                status=CaseStatus.NEW,
                priority=CasePriority.HIGH,
                severity=CaseSeverity.LOW,
            )
        )

        # Test ordering by priority ascending
        cases = await _list_cases(cases_service, order_by="priority", sort="asc")

        # Verify cases are ordered correctly (low priority should come first)
        # Find the indices of our test cases
        low_idx = next(
            (i for i, case in enumerate(cases) if case.id == low_priority_case.id), None
        )
        high_idx = next(
            (i for i, case in enumerate(cases) if case.id == high_priority_case.id),
            None,
        )

        # Both cases should be found
        assert low_idx is not None
        assert high_idx is not None

        # Low priority should come before high priority in ascending order
        assert low_idx < high_idx

    async def test_list_cases_with_ascending_sort(
        self, cases_service: CasesService
    ) -> None:
        """Test listing cases with ascending sort order."""
        # Create cases with different severities to test ordering
        low_severity_case = await cases_service.create_case(
            CaseCreate(
                summary="Low Severity Case",
                description="This is a low severity case",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.LOW,
            )
        )

        high_severity_case = await cases_service.create_case(
            CaseCreate(
                summary="High Severity Case",
                description="This is a high severity case",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.HIGH,
            )
        )

        # Test explicit ascending ordering by severity
        cases = await _list_cases(cases_service, order_by="severity", sort="asc")

        # Find the indices of our test cases
        low_idx = next(
            (i for i, case in enumerate(cases) if case.id == low_severity_case.id), None
        )
        high_idx = next(
            (i for i, case in enumerate(cases) if case.id == high_severity_case.id),
            None,
        )

        # Both cases should be found
        assert low_idx is not None
        assert high_idx is not None

        # Low severity should come before high severity in ascending order
        assert low_idx < high_idx

    async def test_list_cases_with_descending_sort(
        self, cases_service: CasesService
    ) -> None:
        """Test listing cases with descending sort order."""
        # Create cases with different statuses to test ordering
        new_case = await cases_service.create_case(
            CaseCreate(
                summary="New Case",
                description="This is a new case",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.MEDIUM,
            )
        )

        resolved_case = await cases_service.create_case(
            CaseCreate(
                summary="Resolved Case",
                description="This is a resolved case",
                status=CaseStatus.RESOLVED,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.MEDIUM,
            )
        )

        # Test descending ordering by status
        cases = await _list_cases(cases_service, order_by="status", sort="desc")

        # Find the indices of our test cases
        new_idx = next(
            (i for i, case in enumerate(cases) if case.id == new_case.id), None
        )
        resolved_idx = next(
            (i for i, case in enumerate(cases) if case.id == resolved_case.id), None
        )

        # Both cases should be found
        assert new_idx is not None
        assert resolved_idx is not None

        # Resolved status should come before new status in descending order
        # because RESOLVED enum value is higher than NEW
        assert resolved_idx < new_idx

    async def test_list_cases_with_created_at_ordering(
        self, cases_service: CasesService
    ) -> None:
        """Test listing cases ordered by creation time."""
        # Create cases in sequence to ensure different created_at timestamps
        first_case = await cases_service.create_case(
            CaseCreate(
                summary="First Case",
                description="This is the first created case",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.MEDIUM,
            )
        )

        # Small delay to ensure different timestamps
        await asyncio.sleep(0.01)

        second_case = await cases_service.create_case(
            CaseCreate(
                summary="Second Case",
                description="This is the second created case",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.MEDIUM,
            )
        )

        # Test ascending order (oldest first)
        asc_cases = await _list_cases(cases_service, order_by="created_at", sort="asc")

        # Find the indices of our test cases
        first_idx_asc = next(
            (i for i, case in enumerate(asc_cases) if case.id == first_case.id), None
        )
        second_idx_asc = next(
            (i for i, case in enumerate(asc_cases) if case.id == second_case.id), None
        )

        # Both cases should be found
        assert first_idx_asc is not None
        assert second_idx_asc is not None

        # First created case should come before second created case in ascending order
        assert first_idx_asc < second_idx_asc

        # Test descending order (newest first)
        desc_cases = await _list_cases(
            cases_service, order_by="created_at", sort="desc"
        )

        # Find the indices of our test cases
        first_idx_desc = next(
            (i for i, case in enumerate(desc_cases) if case.id == first_case.id), None
        )
        second_idx_desc = next(
            (i for i, case in enumerate(desc_cases) if case.id == second_case.id), None
        )

        # Both cases should be found
        assert first_idx_desc is not None
        assert second_idx_desc is not None

        # Second created case should come before first created case in descending order
        assert second_idx_desc < first_idx_desc

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

    async def test_update_case_close_emits_case_closed_event(
        self, cases_service: CasesService, case_create_params: CaseCreate
    ) -> None:
        """Closing a case should persist a specialized close event."""
        created_case = await cases_service.create_case(case_create_params)

        await cases_service.update_case(
            created_case,
            CaseUpdate(status=CaseStatus.CLOSED),
        )

        events = await cases_service.events.list_events(created_case)
        assert len(events) >= 2
        assert events[0].type == CaseEventType.CASE_CLOSED
        assert events[0].data["old"] == CaseStatus.NEW.value
        assert events[0].data["new"] == CaseStatus.CLOSED.value

    async def test_update_case_with_assignee(
        self,
        cases_service: CasesService,
        case_create_params: CaseCreate,
        mocker,
    ) -> None:
        """Test updating a case to add an assignee."""
        # Create a case without assignee first
        with patch("tracecat.cases.service.CasesService.create_case") as mock_create:
            # Create a mock case without assignee
            mock_case = MagicMock()
            mock_case.assignee_id = None
            mock_create.return_value = mock_case

            # Create case
            case = await cases_service.create_case(case_create_params)
            assert case.assignee_id is None

        # Now patch the update_case method to simulate adding an assignee
        with patch("tracecat.cases.service.CasesService.update_case") as mock_update:
            # Create a mock case with assignee
            mock_updated_case = MagicMock()
            mock_updated_case.assignee_id = uuid.uuid4()
            mock_update.return_value = mock_updated_case

            # Update case with assignee
            update_params = CaseUpdate(assignee_id=mock_updated_case.assignee_id)
            result = await cases_service.update_case(case, update_params)

            # Verify assignee was set
            assert result.assignee_id == mock_updated_case.assignee_id
            mock_update.assert_called_once()

    async def test_remove_case_assignee(
        self,
        cases_service: CasesService,
        case_create_params: CaseCreate,
        mocker,
    ) -> None:
        """Test removing an assignee from a case."""
        # First create a case with assignee
        with patch("tracecat.cases.service.CasesService.create_case") as mock_create:
            # Create a mock case with assignee
            mock_case = MagicMock()
            mock_case.assignee_id = uuid.uuid4()
            mock_create.return_value = mock_case

            # Set the assignee ID in params
            case_create_params.assignee_id = mock_case.assignee_id

            # Create case
            case = await cases_service.create_case(case_create_params)
            assert case.assignee_id is not None

        # Now patch update_case to simulate removing the assignee
        with patch("tracecat.cases.service.CasesService.update_case") as mock_update:
            # Create a mock case without assignee
            mock_updated_case = MagicMock()
            mock_updated_case.assignee_id = None
            mock_update.return_value = mock_updated_case

            # Update case to remove assignee
            update_params = CaseUpdate(assignee_id=None)
            result = await cases_service.update_case(case, update_params)

            # Verify assignee was removed
            assert result.assignee_id is None
            mock_update.assert_called_once()

    async def test_update_case_with_fields(
        self,
        cases_service: CasesService,
        case_fields_service: CaseFieldsService,
        case_create_params: CaseCreate,
    ) -> None:
        """Test updating a case with fields."""
        # First create custom field definitions
        await case_fields_service.create_field(
            CaseFieldCreate(name="field1", type=SqlType.TEXT)
        )
        await case_fields_service.create_field(
            CaseFieldCreate(name="field2", type=SqlType.INTEGER)
        )

        # Create case without initial field values
        created_case = await cases_service.create_case(case_create_params)

        # Verify case was created and has a field values row
        fields_before = await cases_service.fields.get_fields(created_case)
        assert fields_before is not None

        # Update parameters including fields
        update_params = CaseUpdate(
            summary="Updated Test Case",
            fields={"field1": "updated_value", "field2": 2},
        )

        # Update case
        updated_case = await cases_service.update_case(created_case, update_params)

        # Verify fields were updated
        assert updated_case.summary == "Updated Test Case"
        fields = await cases_service.fields.get_fields(updated_case)
        assert fields is not None
        assert fields["field1"] == "updated_value"
        assert fields["field2"] == 2

    async def test_create_case_with_dropdown_values(
        self, cases_service: CasesService, case_create_params: CaseCreate
    ) -> None:
        """Test creating a case with dropdown values."""
        definition, option = await _create_dropdown_with_option(
            cases_service,
            definition_name="Environment",
            definition_ref="environment",
            option_label="Production",
            option_ref="prod",
        )
        params = CaseCreate(
            summary=case_create_params.summary,
            description=case_create_params.description,
            status=case_create_params.status,
            priority=case_create_params.priority,
            severity=case_create_params.severity,
            dropdown_values=[
                CaseDropdownValueInput(
                    definition_ref=definition.ref,
                    option_ref=option.ref,
                )
            ],
        )

        created_case = await cases_service.create_case(params)

        dropdowns_service = CaseDropdownValuesService(
            session=cases_service.session, role=cases_service.role
        )
        values = await dropdowns_service.list_values_for_case(created_case.id)
        assert len(values) == 1
        assert values[0].definition_id == definition.id
        assert values[0].option_id == option.id

    async def test_update_case_with_dropdown_values(
        self, cases_service: CasesService, case_create_params: CaseCreate
    ) -> None:
        """Test updating a case with dropdown values."""
        definition, initial_option = await _create_dropdown_with_option(
            cases_service,
            definition_name="Triage",
            definition_ref="triage",
            option_label="Needs Review",
            option_ref="needs_review",
        )
        definitions_service = CaseDropdownDefinitionsService(
            session=cases_service.session, role=cases_service.role
        )
        target_option = await definitions_service.add_option(
            definition.id,
            CaseDropdownOptionCreate(
                label="Escalated",
                ref="escalated",
                position=1,
            ),
        )

        created_case = await cases_service.create_case(
            CaseCreate(
                summary=case_create_params.summary,
                description=case_create_params.description,
                status=case_create_params.status,
                priority=case_create_params.priority,
                severity=case_create_params.severity,
                dropdown_values=[
                    CaseDropdownValueInput(
                        definition_id=definition.id,
                        option_id=initial_option.id,
                    )
                ],
            )
        )

        await cases_service.update_case(
            created_case,
            CaseUpdate(
                dropdown_values=[
                    CaseDropdownValueInput(
                        definition_id=definition.id,
                        option_ref="escalated",
                    )
                ]
            ),
        )

        dropdowns_service = CaseDropdownValuesService(
            session=cases_service.session, role=cases_service.role
        )
        values = await dropdowns_service.list_values_for_case(created_case.id)
        assert len(values) == 1
        assert values[0].definition_id == definition.id
        assert values[0].option_id == target_option.id

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

        # Mock the upsert_field_values method
        with patch.object(
            cases_service.fields, "upsert_field_values"
        ) as mock_upsert_fields:
            mock_upsert_fields.return_value = {
                "custom_field1": "test value",
                "custom_field2": 123,
            }

            # Create case with fields
            created_case = await cases_service.create_case(params_with_fields)

            # Verify case was created successfully
            assert created_case.summary == params_with_fields.summary
            assert created_case.description == params_with_fields.description

            # Verify that upsert_field_values was called with the case and fields
            mock_upsert_fields.assert_called_once_with(
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
                cases_service.fields, "normalize_field_values"
            ) as mock_normalize_fields,
            patch.object(
                cases_service.fields, "upsert_field_values"
            ) as mock_upsert_fields,
        ):
            # Setup mock to return existing field values
            mock_get_fields.return_value = {
                "existing_field1": "original value",
                "existing_field2": 456,
            }
            mock_normalize_fields.return_value = {
                "existing_field1": "updated value",
                "new_field": "new value",
            }

            # Update just the fields
            update_params = CaseUpdate(
                fields={"existing_field1": "updated value", "new_field": "new value"}
            )

            # Update the case
            await cases_service.update_case(created_case, update_params)

            # Verify get_fields was called
            mock_get_fields.assert_called_once_with(created_case)
            mock_normalize_fields.assert_called_once_with(
                {"existing_field1": "updated value", "new_field": "new value"}
            )

            # Verify upsert_field_values was called with the fields (not merged)
            mock_upsert_fields.assert_called_once_with(
                created_case,
                {"existing_field1": "updated value", "new_field": "new value"},
            )

    async def test_update_case_skips_no_op_field_event(
        self, cases_service: CasesService, case_create_params: CaseCreate
    ) -> None:
        """No-op field updates should not emit empty field-change events."""
        created_case = await cases_service.create_case(case_create_params)

        with (
            patch.object(
                cases_service.fields,
                "get_fields",
                return_value={"numeric_field": Decimal("1.30")},
            ) as mock_get_fields,
            patch.object(
                cases_service.fields,
                "normalize_field_values",
                return_value={"numeric_field": Decimal("1.30")},
            ) as mock_normalize_fields,
            patch.object(
                cases_service.fields, "upsert_field_values"
            ) as mock_upsert_fields,
            patch.object(cases_service.events, "create_event") as mock_create_event,
        ):
            await cases_service.update_case(
                created_case, CaseUpdate(fields={"numeric_field": "1.30"})
            )

            mock_get_fields.assert_called_once_with(created_case)
            mock_normalize_fields.assert_called_once_with({"numeric_field": "1.30"})
            mock_upsert_fields.assert_called_once_with(
                created_case, {"numeric_field": Decimal("1.30")}
            )
            mock_create_event.assert_not_awaited()

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

    async def test_search_cases_aliases_list_cases(
        self, cases_service: CasesService
    ) -> None:
        """search_cases should match default list_cases behavior when unfiltered."""
        await cases_service.create_case(
            CaseCreate(
                summary="First Case",
                description="This is the first case",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.LOW,
            )
        )
        await asyncio.sleep(0.01)
        await cases_service.create_case(
            CaseCreate(
                summary="Second Case",
                description="This is the second case",
                status=CaseStatus.IN_PROGRESS,
                priority=CasePriority.HIGH,
                severity=CaseSeverity.MEDIUM,
            )
        )

        params = CursorPaginationParams(limit=10, cursor=None, reverse=False)
        list_response = await cases_service.list_cases(
            limit=10,
            order_by="created_at",
            sort="asc",
        )
        search_response = await cases_service.search_cases(
            params=params,
            order_by="created_at",
            sort="asc",
        )

        assert search_response.model_dump() == list_response.model_dump()

    async def test_search_cases_gates_duration_selectinload(
        self, cases_service: CasesService
    ) -> None:
        """Duration eager loading should be opt-in for cases list/search."""
        params = CursorPaginationParams(limit=10, cursor=None, reverse=False)
        loader_calls: list[str] = []

        def tracked_selectinload(attr: Any):
            if key := getattr(attr, "key", None):
                loader_calls.append(key)
            return sa_selectinload(attr)

        with patch(
            "tracecat.cases.service.selectinload", side_effect=tracked_selectinload
        ):
            await cases_service.search_cases(params=params)

        assert "durations" not in loader_calls

        loader_calls.clear()
        with patch(
            "tracecat.cases.service.selectinload", side_effect=tracked_selectinload
        ):
            await cases_service.search_cases(params=params, include_durations=True)

        assert "durations" in loader_calls

    async def test_search_cases_tag_filter_uses_or_logic(
        self, cases_service: CasesService
    ) -> None:
        """Multiple tag filters should match cases that have any selected tag."""
        tags_service = CaseTagsService(
            session=cases_service.session,
            role=cases_service.role,
        )
        tag_one = await tags_service.create_tag(
            TagCreate(name="Tag One", color="#111111")
        )
        tag_two = await tags_service.create_tag(
            TagCreate(name="Tag Two", color="#222222")
        )

        case_with_first_tag = await cases_service.create_case(
            CaseCreate(
                summary="Case with first tag",
                description="Case linked to first tag",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.LOW,
            )
        )
        case_with_second_tag = await cases_service.create_case(
            CaseCreate(
                summary="Case with second tag",
                description="Case linked to second tag",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.LOW,
            )
        )
        unrelated_case = await cases_service.create_case(
            CaseCreate(
                summary="Untagged case",
                description="Case without matching tags",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.LOW,
            )
        )

        await tags_service.add_case_tag(case_with_first_tag.id, str(tag_one.id))
        await tags_service.add_case_tag(case_with_second_tag.id, str(tag_two.id))

        params = CursorPaginationParams(limit=20, cursor=None, reverse=False)
        response = await cases_service.search_cases(
            params=params,
            tag_ids=[tag_one.id, tag_two.id],
            order_by="created_at",
            sort="asc",
        )

        result_ids = {item.id for item in response.items}
        assert case_with_first_tag.id in result_ids
        assert case_with_second_tag.id in result_ids
        assert unrelated_case.id not in result_ids

    async def test_search_cases_short_id_exact_match(
        self, cases_service: CasesService
    ) -> None:
        """short_id filtering should match the exact case short ID only."""
        target_case = await cases_service.create_case(
            CaseCreate(
                summary="Target case",
                description="Contains search target",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.LOW,
            )
        )
        await asyncio.sleep(0.01)
        other_case = await cases_service.create_case(
            CaseCreate(
                summary="Other case",
                description="Should not match target fragment",
                status=CaseStatus.NEW,
                priority=CasePriority.MEDIUM,
                severity=CaseSeverity.LOW,
            )
        )

        params = CursorPaginationParams(limit=20, cursor=None, reverse=False)
        response = await cases_service.search_cases(
            params=params,
            short_id=target_case.short_id,
            order_by="created_at",
            sort="asc",
        )

        result_ids = {item.id for item in response.items}
        assert target_case.id in result_ids
        assert other_case.id not in result_ids

        # Partial strings should not match case short IDs.
        non_exact_short_id = (
            str(target_case.case_number * 10)
            if target_case.case_number < 10
            else str(target_case.case_number)[:-1]
        )
        non_exact_response = await cases_service.search_cases(
            params=params,
            short_id=non_exact_short_id,
            order_by="created_at",
            sort="asc",
        )
        non_exact_ids = {item.id for item in non_exact_response.items}
        assert target_case.id not in non_exact_ids

    async def test_search_cases_short_id_rejects_invalid_value(
        self, cases_service: CasesService
    ) -> None:
        """short_id filtering should reject non-numeric case identifiers."""
        params = CursorPaginationParams(limit=20, cursor=None, reverse=False)
        with pytest.raises(
            ValueError, match="Short ID must match CASE-<number> or <number>"
        ):
            await cases_service.search_cases(
                params=params,
                short_id="CASE-ABCD",
            )

    async def test_get_search_case_aggregates_applies_enum_filters(
        self, cases_service: CasesService, session: AsyncSession
    ) -> None:
        """Aggregate counts should respect include/exclude enum filters."""
        now = datetime.now(UTC)
        session.add_all(
            [
                Case(
                    workspace_id=cases_service.workspace_id,
                    case_number=1,
                    summary="Case A",
                    description="A",
                    status=CaseStatus.NEW,
                    priority=CasePriority.MEDIUM,
                    severity=CaseSeverity.LOW,
                    created_at=now,
                    updated_at=now,
                ),
                Case(
                    workspace_id=cases_service.workspace_id,
                    case_number=2,
                    summary="Case B",
                    description="B",
                    status=CaseStatus.IN_PROGRESS,
                    priority=CasePriority.HIGH,
                    severity=CaseSeverity.MEDIUM,
                    created_at=now,
                    updated_at=now,
                ),
                Case(
                    workspace_id=cases_service.workspace_id,
                    case_number=3,
                    summary="Case C",
                    description="C",
                    status=CaseStatus.ON_HOLD,
                    priority=CasePriority.HIGH,
                    severity=CaseSeverity.HIGH,
                    created_at=now,
                    updated_at=now,
                ),
                Case(
                    workspace_id=cases_service.workspace_id,
                    case_number=4,
                    summary="Case D",
                    description="D",
                    status=CaseStatus.RESOLVED,
                    priority=CasePriority.CRITICAL,
                    severity=CaseSeverity.HIGH,
                    created_at=now,
                    updated_at=now,
                ),
                Case(
                    workspace_id=cases_service.workspace_id,
                    case_number=5,
                    summary="Case E",
                    description="E",
                    status=CaseStatus.CLOSED,
                    priority=CasePriority.LOW,
                    severity=CaseSeverity.LOW,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        await session.commit()

        aggregates = await cases_service.get_search_case_aggregates(
            status=[CaseStatus.IN_PROGRESS, CaseStatus.RESOLVED],
            priority=[CasePriority.HIGH, CasePriority.CRITICAL],
            severity=[CaseSeverity.MEDIUM, CaseSeverity.HIGH],
        )

        assert aggregates.total == 2
        assert aggregates.status_groups.new == 0
        assert aggregates.status_groups.in_progress == 1
        assert aggregates.status_groups.on_hold == 0
        assert aggregates.status_groups.resolved == 1
        assert aggregates.status_groups.closed == 0
        assert aggregates.status_groups.unknown == 0
        assert aggregates.status_groups.other == 0

    async def test_get_search_case_aggregates_excludes_unknown_from_other(
        self, cases_service: CasesService, session: AsyncSession
    ) -> None:
        """`other` aggregate should not include cases with `unknown` status."""

        now = datetime.now(UTC)
        session.add_all(
            [
                Case(
                    workspace_id=cases_service.workspace_id,
                    case_number=1,
                    summary="Other status case",
                    description="Other",
                    status=CaseStatus.OTHER,
                    priority=CasePriority.MEDIUM,
                    severity=CaseSeverity.MEDIUM,
                    created_at=now,
                    updated_at=now,
                ),
                Case(
                    workspace_id=cases_service.workspace_id,
                    case_number=2,
                    summary="Unknown status case",
                    description="Unknown",
                    status=CaseStatus.UNKNOWN,
                    priority=CasePriority.MEDIUM,
                    severity=CaseSeverity.MEDIUM,
                    created_at=now,
                    updated_at=now,
                ),
            ]
        )
        await session.commit()

        aggregates = await cases_service.get_search_case_aggregates()

        assert aggregates.total == 2
        assert aggregates.status_groups.new == 0
        assert aggregates.status_groups.in_progress == 0
        assert aggregates.status_groups.on_hold == 0
        assert aggregates.status_groups.resolved == 0
        assert aggregates.status_groups.closed == 0
        assert aggregates.status_groups.unknown == 1
        assert aggregates.status_groups.other == 1

    async def test_create_case_with_nonexistent_field(
        self, cases_service: CasesService
    ) -> None:
        """Test creating a case with a field that doesn't exist in the schema."""

        params = CaseCreate(
            summary="Test Case",
            description="Test case with invalid field",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
            fields={"nonexistent_field": "some value"},
        )

        # Should raise ValueError with clear message about undefined column
        with pytest.raises(ValueError):
            await cases_service.create_case(params)

    async def test_create_case_fields_update_fails(
        self, cases_service: CasesService, case_create_params: CaseCreate, mocker
    ) -> None:
        """Test that case creation is atomic when field update fails."""
        from tracecat.exceptions import TracecatException, TracecatNotFoundError

        # Add fields to the params
        params_with_fields = CaseCreate(
            summary=case_create_params.summary,
            description=case_create_params.description,
            status=case_create_params.status,
            priority=case_create_params.priority,
            severity=case_create_params.severity,
            fields={"test_field": "test_value"},
        )

        # Mock update_row to raise TracecatNotFoundError (simulating UPDATE matching 0 rows)
        mocker.patch.object(
            cases_service.fields.editor,
            "update_row",
            side_effect=TracecatNotFoundError("Row not found in table case_fields"),
        )

        # Should raise TracecatException with helpful message
        with pytest.raises(TracecatException) as exc_info:
            await cases_service.create_case(params_with_fields)

        # Verify error message is user-friendly
        error_msg = str(exc_info.value).lower()
        assert "failed to save custom field values" in error_msg
        assert "test_field" in error_msg  # Should mention the field name
        assert (
            "row" not in error_msg or "case_fields" not in error_msg
        )  # Should not expose DB details

        # Verify the case was NOT created (transaction rolled back)
        cases = await _list_cases(cases_service)
        assert len(cases) == 0

    async def test_create_case_atomic_rollback_on_field_error(
        self, cases_service: CasesService
    ) -> None:
        """Test that case creation is fully atomic - if fields fail, case also rolls back."""

        # Try to create case with invalid field
        params = CaseCreate(
            summary="Test Case",
            description="Test case that should rollback",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
            fields={"this_field_does_not_exist_in_schema": "value"},
        )

        # Should fail
        with pytest.raises(ValueError):
            await cases_service.create_case(params)

        # Verify the case was NOT created (entire transaction rolled back)
        cases = await _list_cases(cases_service)
        assert len(cases) == 0, "Case should not exist if fields creation failed"
