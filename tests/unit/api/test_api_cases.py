"""HTTP-level tests for cases API endpoints."""

import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.cases import router as cases_router
from tracecat.cases.dropdowns.schemas import CaseDropdownValueRead
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.schemas import CaseReadMinimal
from tracecat.db.models import Case, CaseTag, Workspace
from tracecat.pagination import CursorPaginatedResponse


@pytest.fixture
def mock_case(test_workspace: Workspace) -> Case:
    """Create a mock case DB object."""
    case = Case(
        workspace_id=test_workspace.id,
        summary="Test Case Summary",
        description="Test case description with details",
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.MEDIUM,
        status=CaseStatus.NEW,  # Changed from OPEN to NEW
        payload={"source": "test"},
        assignee_id=None,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        updated_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    # Set attributes that are auto-generated or relationships
    case.id = uuid.UUID("cccccccc-cccc-4ccc-cccc-cccccccccccc")
    case.case_number = 1
    case.tags = []
    case.assignee = None
    case.dropdown_values = []
    return case


@pytest.fixture
def mock_case_tag(test_workspace: Workspace) -> CaseTag:
    """Create a mock case tag DB object."""
    return CaseTag(
        id=uuid.uuid4(),
        name="incident",
        ref="incident",  # Changed from slug to ref
        color="#FF0000",
        workspace_id=test_workspace.id,
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
        updated_at=datetime(2024, 1, 1, tzinfo=UTC),
    )


@pytest.mark.anyio
async def test_list_cases_success(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    """Test GET /cases returns paginated list of cases."""
    with (
        patch.object(cases_router, "CasesService") as MockService,
    ):
        # Create mock service instance
        mock_svc = AsyncMock()

        # Mock paginated response with CaseReadMinimal instances
        mock_case_read = CaseReadMinimal(
            id=mock_case.id,
            created_at=mock_case.created_at,
            updated_at=mock_case.updated_at,
            short_id=mock_case.short_id,
            summary=mock_case.summary,
            status=mock_case.status,
            priority=mock_case.priority,
            severity=mock_case.severity,
            assignee=None,
            tags=[],
            num_tasks_completed=0,
            num_tasks_total=0,
        )

        mock_response = CursorPaginatedResponse(
            items=[mock_case_read],
            next_cursor=None,
            prev_cursor=None,
            has_more=False,
            has_previous=False,
        )
        mock_svc.list_cases.return_value = mock_response

        # Set up service mock
        MockService.return_value = mock_svc

        # Make request
        response = client.get(
            "/cases",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Verify response structure
        assert "items" in data
        assert len(data["items"]) == 1
        assert "has_more" in data
        assert "next_cursor" in data

        # Verify case data
        case = data["items"][0]
        assert case["summary"] == "Test Case Summary"
        assert case["status"] == "new"
        assert case["priority"] == "medium"
        assert case["severity"] == "medium"
        assert "short_id" in case


@pytest.mark.anyio
async def test_list_cases_with_filters(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    """Test GET /cases with filtering by status, priority, severity."""
    with (
        patch.object(cases_router, "CasesService") as MockService,
    ):
        mock_svc = AsyncMock()
        mock_case_read = CaseReadMinimal(
            id=mock_case.id,
            created_at=mock_case.created_at,
            updated_at=mock_case.updated_at,
            short_id=mock_case.short_id,
            summary=mock_case.summary,
            status=mock_case.status,
            priority=mock_case.priority,
            severity=mock_case.severity,
            assignee=None,
            tags=[],
            num_tasks_completed=0,
            num_tasks_total=0,
        )

        mock_response = CursorPaginatedResponse(
            items=[mock_case_read],
            next_cursor=None,
            prev_cursor=None,
            has_more=False,
            has_previous=False,
        )
        mock_svc.list_cases.return_value = mock_response
        MockService.return_value = mock_svc

        # Make request with filters
        response = client.get(
            "/cases",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "status": "new",
                "priority": "medium",
                "severity": "medium",
                "search_term": "Test",
                "start_time": "2024-01-01T00:00:00Z",
                "end_time": "2024-01-31T23:59:59Z",
                "updated_after": "2024-01-01T00:00:00Z",
                "updated_before": "2024-01-31T23:59:59Z",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["items"]) == 1


@pytest.mark.anyio
async def test_create_case_success(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    """Test POST /cases creates a new case."""
    with (
        patch.object(cases_router, "CasesService") as MockService,
    ):
        mock_svc = AsyncMock()
        mock_svc.create_case.return_value = mock_case
        MockService.return_value = mock_svc

        # Make request
        response = client.post(
            "/cases",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={
                "summary": "Test Case Summary",
                "description": "Test case description with details",
                "priority": "medium",
                "severity": "medium",
                "status": "new",  # Changed from "open" to "new"
                "payload": {"source": "test"},
            },
        )

        # Assertions
        assert response.status_code == status.HTTP_201_CREATED

        # Verify service was called
        mock_svc.create_case.assert_called_once()


@pytest.mark.anyio
async def test_create_case_validation_error(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test POST /cases with invalid data returns 422."""
    # Make request with missing required fields
    response = client.post(
        "/cases",
        params={"workspace_id": str(test_admin_role.workspace_id)},
        json={
            "summary": "Test",
            # Missing required fields: description, priority, severity
        },
    )

    # Should return validation error
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY


@pytest.mark.anyio
async def test_get_case_success(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    """Test GET /cases/{id} returns case details."""
    with (
        patch.object(cases_router, "CasesService") as MockService,
        patch.object(cases_router, "CaseDropdownValuesService") as MockDropdownService,
    ):
        mock_svc = AsyncMock()
        mock_svc.get_case.return_value = mock_case
        # Mock fields service
        mock_svc.fields = AsyncMock()
        mock_svc.fields.get_fields.return_value = {}
        mock_svc.fields.list_fields.return_value = []
        MockService.return_value = mock_svc

        # Mock dropdown service
        mock_dropdown_svc = AsyncMock()
        mock_dropdown_svc.list_values_for_case.return_value = []
        MockDropdownService.return_value = mock_dropdown_svc

        # Make request
        case_id = str(mock_case.id)
        response = client.get(
            f"/cases/{case_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Verify case data
        assert data["summary"] == "Test Case Summary"
        assert data["description"] == "Test case description with details"
        assert data["status"] == "new"
        assert data["priority"] == "medium"
        assert data["severity"] == "medium"
        assert data["short_id"] == "CASE-0001"
        assert "id" in data
        assert "created_at" in data


@pytest.mark.anyio
async def test_get_case_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /cases/{id} with non-existent ID returns 404."""
    with (
        patch.object(cases_router, "CasesService") as MockService,
    ):
        mock_svc = AsyncMock()
        mock_svc.get_case.return_value = None
        MockService.return_value = mock_svc

        # Make request with non-existent ID
        fake_id = str(uuid.uuid4())
        response = client.get(
            f"/cases/{fake_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Should return 404
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_update_case_success(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    """Test PATCH /cases/{id} updates case."""
    with (
        patch.object(cases_router, "CasesService") as MockService,
    ):
        mock_svc = AsyncMock()
        mock_svc.get_case.return_value = mock_case
        mock_svc.update_case.return_value = None
        MockService.return_value = mock_svc

        # Make request
        case_id = str(mock_case.id)
        response = client.patch(
            f"/cases/{case_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={
                "summary": "Updated Summary",
                "status": "in_progress",
            },
        )

        # Assertions
        assert response.status_code == status.HTTP_204_NO_CONTENT

        # Verify service was called
        mock_svc.update_case.assert_called_once()


@pytest.mark.anyio
async def test_update_case_not_found(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test PATCH /cases/{id} with non-existent ID returns 404."""
    with (
        patch.object(cases_router, "CasesService") as MockService,
    ):
        mock_svc = AsyncMock()
        mock_svc.get_case.return_value = None
        MockService.return_value = mock_svc

        # Make request
        fake_id = str(uuid.uuid4())
        response = client.patch(
            f"/cases/{fake_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"summary": "Updated Summary"},
        )

        # Should return 404
        assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_get_case_with_tags(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
    mock_case_tag: CaseTag,
) -> None:
    """Test GET /cases/{id} properly serializes tags relationship."""
    with (
        patch.object(cases_router, "CasesService") as MockService,
        patch.object(cases_router, "CaseDropdownValuesService") as MockDropdownService,
    ):
        mock_svc = AsyncMock()

        # Add tags to case
        mock_case.tags = [mock_case_tag]

        mock_svc.get_case.return_value = mock_case
        mock_svc.fields = AsyncMock()
        mock_svc.fields.get_fields.return_value = {}
        mock_svc.fields.list_fields.return_value = []
        MockService.return_value = mock_svc

        # Mock dropdown service
        mock_dropdown_svc = AsyncMock()
        mock_dropdown_svc.list_values_for_case.return_value = []
        MockDropdownService.return_value = mock_dropdown_svc

        # Make request
        case_id = str(mock_case.id)
        response = client.get(
            f"/cases/{case_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        # Verify tags are properly serialized
        assert "tags" in data
        assert len(data["tags"]) == 1
        assert data["tags"][0]["name"] == "incident"
        assert data["tags"][0]["ref"] == "incident"  # Changed from slug to ref
        assert data["tags"][0]["color"] == "#FF0000"


@pytest.mark.anyio
async def test_get_case_with_dropdown_values(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    """Test GET /cases/{id} includes dropdown values in response."""
    with (
        patch.object(cases_router, "CasesService") as MockService,
        patch.object(cases_router, "CaseDropdownValuesService") as MockDropdownService,
    ):
        mock_svc = AsyncMock()
        mock_svc.get_case.return_value = mock_case
        mock_svc.fields = AsyncMock()
        mock_svc.fields.get_fields.return_value = {}
        mock_svc.fields.list_fields.return_value = []
        MockService.return_value = mock_svc

        # Mock dropdown values
        defn_id = uuid.uuid4()
        option_id = uuid.uuid4()
        value_id = uuid.uuid4()
        mock_dropdown_reads = [
            CaseDropdownValueRead(
                id=value_id,
                definition_id=defn_id,
                definition_ref="environment",
                definition_name="Environment",
                option_id=option_id,
                option_label="Production",
                option_ref="production",
                option_icon_name="server",
                option_color="#00FF00",
            )
        ]
        mock_dropdown_svc = AsyncMock()
        mock_dropdown_svc.list_values_for_case.return_value = mock_dropdown_reads
        MockDropdownService.return_value = mock_dropdown_svc

        case_id = str(mock_case.id)
        response = client.get(
            f"/cases/{case_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()

        assert "dropdown_values" in data
        assert len(data["dropdown_values"]) == 1
        dv = data["dropdown_values"][0]
        assert dv["id"] == str(value_id)
        assert dv["definition_id"] == str(defn_id)
        assert dv["definition_ref"] == "environment"
        assert dv["definition_name"] == "Environment"
        assert dv["option_id"] == str(option_id)
        assert dv["option_label"] == "Production"
        assert dv["option_ref"] == "production"
        assert dv["option_icon_name"] == "server"
        assert dv["option_color"] == "#00FF00"


@pytest.mark.anyio
async def test_search_cases_success(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    """Test GET /cases/search delegates to list_cases shape/response."""
    with (
        patch.object(cases_router, "CasesService") as MockService,
    ):
        mock_svc = AsyncMock()

        mock_case_read = CaseReadMinimal(
            id=mock_case.id,
            created_at=mock_case.created_at,
            updated_at=mock_case.updated_at,
            short_id=mock_case.short_id,
            summary=mock_case.summary,
            status=mock_case.status,
            priority=mock_case.priority,
            severity=mock_case.severity,
            assignee=None,
            tags=[],
            num_tasks_completed=0,
            num_tasks_total=0,
        )
        mock_svc.list_cases.return_value = CursorPaginatedResponse(
            items=[mock_case_read],
            next_cursor=None,
            prev_cursor=None,
            has_more=False,
            has_previous=False,
        )
        MockService.return_value = mock_svc

        # Make request
        response = client.get(
            "/cases/search",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "search_term": "Test",
                "limit": 10,
            },
        )

        # Assertions
        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data["items"]) == 1
        assert data["items"][0]["summary"] == "Test Case Summary"
        mock_svc.list_cases.assert_called_once()


@pytest.mark.anyio
async def test_search_cases_forwards_date_filters(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    """Test GET /cases/search forwards date filters through the list alias."""
    with (
        patch.object(cases_router, "CasesService") as MockService,
    ):
        mock_svc = AsyncMock()

        mock_case_read = CaseReadMinimal(
            id=mock_case.id,
            created_at=mock_case.created_at,
            updated_at=mock_case.updated_at,
            short_id=mock_case.short_id,
            summary=mock_case.summary,
            status=mock_case.status,
            priority=mock_case.priority,
            severity=mock_case.severity,
            assignee=None,
            tags=[],
            num_tasks_completed=0,
            num_tasks_total=0,
        )
        mock_svc.list_cases.return_value = CursorPaginatedResponse(
            items=[mock_case_read],
            next_cursor=None,
            prev_cursor=None,
            has_more=False,
            has_previous=False,
        )
        MockService.return_value = mock_svc

        response = client.get(
            "/cases/search",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "start_time": "2024-01-01T00:00:00Z",
                "end_time": "2024-01-31T23:59:59Z",
                "updated_after": "2024-01-15T00:00:00Z",
                "updated_before": "2024-01-31T23:59:59Z",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        mock_svc.list_cases.assert_called_once()
