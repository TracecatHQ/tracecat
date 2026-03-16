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
from tracecat.cases.enums import CaseEventType, CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.schemas import (
    CaseCommentRead,
    CaseCommentThreadRead,
    CaseReadMinimal,
    CaseSearchAggregateRead,
    CaseStatusGroupCounts,
)
from tracecat.db.models import Case, CaseTag, Workspace
from tracecat.exceptions import EntitlementRequired, TracecatValidationError
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


def _build_comment_read(
    *,
    content: str,
    parent_id: uuid.UUID | None = None,
    is_deleted: bool = False,
) -> CaseCommentRead:
    now = datetime(2024, 1, 1, tzinfo=UTC)
    return CaseCommentRead(
        id=uuid.uuid4(),
        created_at=now,
        updated_at=now,
        content=content,
        parent_id=parent_id,
        user=None,
        last_edited_at=None,
        deleted_at=now if is_deleted else None,
        is_deleted=is_deleted,
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
            dropdown_values=[],
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
            dropdown_values=[],
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
async def test_list_case_events_includes_comment_activity(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    db_event = AsyncMock()
    db_event.type = CaseEventType.COMMENT_REPLY_DELETED
    db_event.user_id = test_admin_role.user_id
    db_event.created_at = datetime(2024, 1, 1, tzinfo=UTC)
    db_event.data = {
        "comment_id": str(uuid.uuid4()),
        "parent_id": str(uuid.uuid4()),
        "thread_root_id": str(uuid.uuid4()),
        "delete_mode": "hard",
        "wf_exec_id": None,
    }

    with (
        patch.object(cases_router, "CasesService") as mock_service_cls,
        patch.object(cases_router, "search_users", new=AsyncMock(return_value=[])),
    ):
        mock_service = AsyncMock()
        mock_service.get_case.return_value = mock_case
        mock_service.events = AsyncMock()
        mock_service.events.list_events.return_value = [db_event]
        mock_service_cls.return_value = mock_service

        response = client.get(
            f"/cases/{mock_case.id}/events",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["events"][0]["type"] == "comment_reply_deleted"
    assert data["events"][0]["delete_mode"] == "hard"


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
async def test_create_case_with_dropdown_values(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    """Test POST /cases accepts dropdown value inputs."""
    with (
        patch.object(cases_router, "CasesService") as MockService,
    ):
        mock_svc = AsyncMock()
        mock_svc.create_case.return_value = mock_case
        MockService.return_value = mock_svc

        response = client.post(
            "/cases",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={
                "summary": "Test Case Summary",
                "description": "Test case description with details",
                "priority": "medium",
                "severity": "medium",
                "status": "new",
                "dropdown_values": [
                    {"definition_ref": "environment", "option_ref": "prod"}
                ],
            },
        )

        assert response.status_code == status.HTTP_201_CREATED
        mock_svc.create_case.assert_called_once()
        params = mock_svc.create_case.call_args.args[0]
        assert params.dropdown_values is not None
        assert len(params.dropdown_values) == 1
        assert params.dropdown_values[0].definition_ref == "environment"
        assert params.dropdown_values[0].option_ref == "prod"


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
async def test_update_case_field_invalid_identifier_returns_400(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch.object(cases_router, "CaseFieldsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.update_field.side_effect = ValueError(
            "Identifier must contain only letters, numbers, and underscores"
        )
        mock_service_cls.return_value = mock_service

        response = client.patch(
            "/case-fields/bad-field",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"default": "value"},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Identifier must" in response.json()["detail"]


@pytest.mark.anyio
async def test_delete_case_field_invalid_identifier_returns_400(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch.object(cases_router, "CaseFieldsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.delete_field.side_effect = ValueError(
            "Identifier must contain only letters, numbers, and underscores"
        )
        mock_service_cls.return_value = mock_service

        response = client.delete(
            "/case-fields/bad-field",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Identifier must" in response.json()["detail"]


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
        mock_dropdown_svc.has_entitlement.return_value = True
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
async def test_update_case_with_dropdown_values(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    """Test PATCH /cases/{id} accepts dropdown value inputs."""
    with (
        patch.object(cases_router, "CasesService") as MockService,
    ):
        mock_svc = AsyncMock()
        mock_svc.get_case.return_value = mock_case
        mock_svc.update_case.return_value = mock_case
        MockService.return_value = mock_svc

        case_id = str(mock_case.id)
        response = client.patch(
            f"/cases/{case_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={
                "dropdown_values": [
                    {"definition_ref": "environment", "option_ref": "staging"}
                ]
            },
        )

        assert response.status_code == status.HTTP_204_NO_CONTENT
        mock_svc.update_case.assert_called_once()
        params = mock_svc.update_case.call_args.args[1]
        assert params.dropdown_values is not None
        assert len(params.dropdown_values) == 1
        assert params.dropdown_values[0].definition_ref == "environment"
        assert params.dropdown_values[0].option_ref == "staging"


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
        mock_dropdown_svc.has_entitlement.return_value = True
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
        mock_dropdown_svc.has_entitlement.return_value = True
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
async def test_get_case_hides_dropdown_values_without_case_addons(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    """Test GET /cases/{id} omits dropdown values when entitlement is absent."""
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

        mock_dropdown_svc = AsyncMock()
        mock_dropdown_svc.has_entitlement.return_value = False
        mock_dropdown_svc.list_values_for_case.return_value = [
            CaseDropdownValueRead(
                id=uuid.uuid4(),
                definition_id=uuid.uuid4(),
                definition_ref="environment",
                definition_name="Environment",
                option_id=uuid.uuid4(),
                option_label="Production",
                option_ref="production",
                option_icon_name="server",
                option_color="#00FF00",
            )
        ]
        MockDropdownService.return_value = mock_dropdown_svc

        case_id = str(mock_case.id)
        response = client.get(
            f"/cases/{case_id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["dropdown_values"] == []
        mock_dropdown_svc.list_values_for_case.assert_not_called()


@pytest.mark.anyio
async def test_search_cases_success(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    """Test GET /cases/search delegates to search_cases shape/response."""
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
            dropdown_values=[],
            num_tasks_completed=0,
            num_tasks_total=0,
        )
        mock_svc.search_cases.return_value = CursorPaginatedResponse(
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
        mock_svc.search_cases.assert_called_once()


@pytest.mark.anyio
async def test_search_cases_accepts_frontend_unassigned_sentinel(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /cases/search accepts frontend unassigned filter sentinel."""
    with (
        patch.object(cases_router, "CasesService") as MockService,
    ):
        mock_svc = AsyncMock()
        mock_svc.search_cases.return_value = CursorPaginatedResponse[CaseReadMinimal](
            items=[],
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
                "assignee_id": "__UNASSIGNED__",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        mock_svc.search_cases.assert_called_once()
        assert mock_svc.search_cases.call_args.kwargs["assignee_ids"] is None
        assert mock_svc.search_cases.call_args.kwargs["include_unassigned"] is True


@pytest.mark.anyio
async def test_search_case_aggregates_accepts_frontend_unassigned_sentinel(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /cases/search/aggregate accepts frontend unassigned sentinel."""
    with (
        patch.object(cases_router, "CasesService") as MockService,
    ):
        mock_svc = AsyncMock()
        mock_svc.get_search_case_aggregates.return_value = CaseSearchAggregateRead(
            total=0,
            status_groups=CaseStatusGroupCounts(),
        )
        MockService.return_value = mock_svc

        response = client.get(
            "/cases/search/aggregate",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "assignee_id": "__UNASSIGNED__",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        mock_svc.get_search_case_aggregates.assert_called_once()
        assert (
            mock_svc.get_search_case_aggregates.call_args.kwargs["assignee_ids"] is None
        )
        assert (
            mock_svc.get_search_case_aggregates.call_args.kwargs["include_unassigned"]
            is True
        )


@pytest.mark.anyio
async def test_search_cases_forwards_date_filters(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    """Test GET /cases/search forwards date filters to search_cases."""
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
            dropdown_values=[],
            num_tasks_completed=0,
            num_tasks_total=0,
        )
        mock_svc.search_cases.return_value = CursorPaginatedResponse(
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
        mock_svc.search_cases.assert_called_once()


@pytest.mark.anyio
async def test_search_case_aggregates_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Test GET /cases/search/aggregate returns grouped global counts."""
    with (
        patch.object(cases_router, "CasesService") as MockService,
    ):
        mock_svc = AsyncMock()
        mock_svc.get_search_case_aggregates.return_value = CaseSearchAggregateRead(
            total=42,
            status_groups=CaseStatusGroupCounts(
                new=10,
                in_progress=8,
                on_hold=4,
                resolved=18,
                closed=4,
                unknown=6,
                other=2,
            ),
        )
        MockService.return_value = mock_svc

        response = client.get(
            "/cases/search/aggregate",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "status": "new",
                "priority": "medium",
                "severity": "high",
                "search_term": "incident",
                "start_time": "2024-01-01T00:00:00Z",
                "updated_after": "2024-01-15T00:00:00Z",
            },
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["total"] == 42
        assert data["status_groups"]["new"] == 10
        assert data["status_groups"]["resolved"] == 18
        assert data["status_groups"]["closed"] == 4
        assert data["status_groups"]["unknown"] == 6
        mock_svc.get_search_case_aggregates.assert_called_once()


@pytest.mark.anyio
async def test_list_comment_threads_success(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    """Threaded comment reads should return grouped threads with tombstone fields."""
    with (
        patch.object(cases_router, "CasesService") as mock_cases_service_cls,
        patch.object(cases_router, "CaseCommentsService") as mock_comments_service_cls,
    ):
        top_level = _build_comment_read(content="Comment deleted", is_deleted=True)
        reply = _build_comment_read(
            content="Reply content",
            parent_id=top_level.id,
        )
        thread = CaseCommentThreadRead(
            comment=top_level,
            replies=[reply],
            reply_count=1,
            last_activity_at=reply.updated_at,
        )

        mock_cases_service = AsyncMock()
        mock_cases_service.get_case.return_value = mock_case
        mock_cases_service_cls.return_value = mock_cases_service

        mock_comments_service = AsyncMock()
        mock_comments_service.list_comment_threads.return_value = [thread]
        mock_comments_service_cls.return_value = mock_comments_service

        response = client.get(
            f"/cases/{mock_case.id}/comments/threads",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert len(data) == 1
        assert data[0]["comment"]["content"] == "Comment deleted"
        assert data[0]["comment"]["is_deleted"] is True
        assert data[0]["reply_count"] == 1
        assert data[0]["replies"][0]["content"] == "Reply content"
        assert data[0]["replies"][0]["parent_id"] == str(top_level.id)


@pytest.mark.anyio
async def test_list_comment_threads_requires_case_addons(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    with (
        patch.object(cases_router, "CasesService") as mock_cases_service_cls,
        patch.object(cases_router, "CaseCommentsService") as mock_comments_service_cls,
    ):
        mock_cases_service = AsyncMock()
        mock_cases_service.get_case.return_value = mock_case
        mock_cases_service_cls.return_value = mock_cases_service

        mock_comments_service = AsyncMock()
        mock_comments_service.list_comment_threads.side_effect = EntitlementRequired(
            "case_addons"
        )
        mock_comments_service_cls.return_value = mock_comments_service

        response = client.get(
            f"/cases/{mock_case.id}/comments/threads",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["type"] == "EntitlementRequired"


@pytest.mark.anyio
async def test_create_reply_requires_case_addons(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    with (
        patch.object(cases_router, "CasesService") as mock_cases_service_cls,
        patch.object(cases_router, "CaseCommentsService") as mock_comments_service_cls,
    ):
        mock_cases_service = AsyncMock()
        mock_cases_service.get_case.return_value = mock_case
        mock_cases_service_cls.return_value = mock_cases_service

        mock_comments_service = AsyncMock()
        mock_comments_service.create_comment.side_effect = EntitlementRequired(
            "case_addons"
        )
        mock_comments_service_cls.return_value = mock_comments_service

        response = client.post(
            f"/cases/{mock_case.id}/comments",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"content": "Reply", "parent_id": str(uuid.uuid4())},
        )

        assert response.status_code == status.HTTP_403_FORBIDDEN
        assert response.json()["type"] == "EntitlementRequired"


@pytest.mark.anyio
async def test_update_comment_wrong_case_returns_not_found(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    """Comment updates should be case scoped."""
    with (
        patch.object(cases_router, "CasesService") as mock_cases_service_cls,
        patch.object(cases_router, "CaseCommentsService") as mock_comments_service_cls,
    ):
        mock_cases_service = AsyncMock()
        mock_cases_service.get_case.return_value = mock_case
        mock_cases_service_cls.return_value = mock_cases_service

        mock_comments_service = AsyncMock()
        mock_comments_service.get_comment_in_case.return_value = None
        mock_comments_service_cls.return_value = mock_comments_service

        response = client.patch(
            f"/cases/{mock_case.id}/comments/{uuid.uuid4()}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"content": "Updated"},
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        mock_comments_service.update_comment.assert_not_called()


@pytest.mark.anyio
async def test_update_comment_reparenting_returns_bad_request(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    """Reparent attempts should return a validation error."""
    with (
        patch.object(cases_router, "CasesService") as mock_cases_service_cls,
        patch.object(cases_router, "CaseCommentsService") as mock_comments_service_cls,
    ):
        mock_cases_service = AsyncMock()
        mock_cases_service.get_case.return_value = mock_case
        mock_cases_service_cls.return_value = mock_cases_service

        mock_comments_service = AsyncMock()
        mock_comments_service.get_comment_in_case.return_value = object()
        mock_comments_service.update_comment.side_effect = TracecatValidationError(
            "Changing a comment parent is not supported"
        )
        mock_comments_service_cls.return_value = mock_comments_service

        response = client.patch(
            f"/cases/{mock_case.id}/comments/{uuid.uuid4()}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"parent_id": str(uuid.uuid4())},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["detail"] == "Changing a comment parent is not supported"
