"""HTTP-level tests for cases API endpoints."""

import uuid
from datetime import UTC, datetime
from types import ModuleType
from unittest.mock import AsyncMock, patch

import pytest
from asyncpg import DuplicateColumnError
from fastapi import status
from fastapi.testclient import TestClient
from sqlalchemy.exc import NoResultFound, ProgrammingError

from tracecat.auth.types import Role
from tracecat.cases import internal_router as internal_cases_router
from tracecat.cases import router as cases_router
from tracecat.cases.dropdowns.schemas import CaseDropdownValueRead
from tracecat.cases.enums import (
    CaseEventType,
    CaseFieldKind,
    CasePriority,
    CaseSeverity,
    CaseStatus,
    CaseVersionField,
)
from tracecat.cases.hierarchy import CaseHierarchyService
from tracecat.cases.schemas import (
    CaseBatchItemResult,
    CaseBatchResponse,
    CaseReadMinimal,
    CaseSearchAggregateRead,
    CaseStatusGroupCounts,
)
from tracecat.cases.tags.service import CaseTagsService
from tracecat.cases.versions import router as case_versions_router
from tracecat.cases.versions.schemas import (
    CaseVersionCompareRead,
    CaseVersionContentRead,
    CaseVersionRestoreRead,
)
from tracecat.contexts import ctx_role
from tracecat.db.models import Case, CaseTag, Workspace
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.pagination import CursorPaginatedResponse


@pytest.fixture(autouse=True)
def stub_case_hierarchy():
    """Case services are mocked wholesale, so stub the sub-case lookups."""
    with patch.object(
        CaseHierarchyService,
        "get_hierarchy",
        new=AsyncMock(return_value={}),
    ) as mock_hierarchy:
        yield mock_hierarchy


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


def _mock_case_read_services(mock_svc: AsyncMock) -> AsyncMock:
    """Configure the mocks needed to serialize a CaseRead response."""
    mock_svc.fields = AsyncMock()
    mock_svc.fields.get_fields.return_value = {}
    mock_svc.fields.list_fields.return_value = []
    mock_svc.fields.get_field_schema.return_value = {}
    mock_dropdown_svc = AsyncMock()
    mock_dropdown_svc.has_entitlement.return_value = False
    return mock_dropdown_svc


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
async def test_create_case_invalid_numeric_fields_returns_400(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Invalid custom-field numeric input should be surfaced as a client error."""
    with patch.object(cases_router, "CasesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.create_case.side_effect = ValueError("Invalid numeric value: 'abc'")
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
                "fields": {"score": "abc"},
            },
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Invalid numeric value: 'abc'"


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
async def test_create_case_field_accepts_long_text_kind(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch.object(cases_router, "CaseFieldsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.list_fields.return_value = [
            {
                "name": "details",
                "type": "TEXT",
                "nullable": True,
                "default": None,
                "comment": None,
            }
        ]
        mock_service.get_field_schema.return_value = {
            "details": {"type": "TEXT", "kind": "LONG_TEXT"}
        }
        mock_service_cls.return_value = mock_service

        response = client.post(
            "/case-fields",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"name": "details", "type": "TEXT", "kind": "LONG_TEXT"},
        )

    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["id"] == "details"
    assert data["kind"] == "LONG_TEXT"
    mock_service.create_field.assert_awaited_once()
    params = mock_service.create_field.await_args.args[0]
    assert params.type == "TEXT"
    assert params.kind is CaseFieldKind.LONG_TEXT


@pytest.mark.anyio
async def test_list_case_fields_preserves_reserved_uuid_type(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch.object(cases_router, "CaseFieldsService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.list_fields.return_value = [
            {
                "name": "case_id",
                "type": "UUID",
                "nullable": False,
                "default": None,
                "comment": "Case UUID",
            },
            {
                "name": "details",
                "type": "TEXT",
                "nullable": True,
                "default": None,
                "comment": "Case details",
            },
        ]
        mock_service.get_field_schema.return_value = {
            "details": {"type": "TEXT", "kind": "LONG_TEXT"}
        }
        mock_service_cls.return_value = mock_service

        response = client.get(
            "/case-fields",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data[0]["id"] == "case_id"
    assert data[0]["type"] == "UUID"
    assert data[0]["reserved"] is True
    assert data[1]["id"] == "details"
    assert data[1]["type"] == "TEXT"
    assert data[1]["kind"] == "LONG_TEXT"


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "payload"),
    [
        pytest.param(
            "POST",
            "/case-fields",
            {"name": "details", "type": "INTEGER", "kind": "LONG_TEXT"},
            id="create-invalid-kind-pair",
        ),
        pytest.param(
            "PATCH", "/case-fields/details", {"kind": "URL"}, id="update-kind-input"
        ),
    ],
)
async def test_case_field_kind_validation_rejects_before_service(
    client: TestClient,
    test_admin_role: Role,
    method: str,
    path: str,
    payload: dict[str, str],
) -> None:
    with patch.object(cases_router, "CaseFieldsService") as mock_service_cls:
        response = client.request(
            method,
            path,
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json=payload,
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    mock_service_cls.assert_not_called()


def _duplicate_column_error() -> ProgrammingError:
    duplicate_error = DuplicateColumnError("Column already exists")
    programming_error = ProgrammingError("", {}, duplicate_error)
    programming_error.__cause__ = duplicate_error
    return programming_error


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "path", "payload", "service_method", "error", "status_code", "detail"),
    [
        pytest.param(
            "PATCH",
            "/case-fields/bad-field",
            {"default": "value"},
            "update_field",
            ValueError(
                "Identifier must contain only letters, numbers, and underscores"
            ),
            status.HTTP_400_BAD_REQUEST,
            "Identifier must contain only letters, numbers, and underscores",
            id="update-value-error",
        ),
        pytest.param(
            "POST",
            "/case-fields",
            {"name": "__tc_workspace_id", "type": "TEXT"},
            "create_field",
            ValueError("Field __tc_workspace_id is reserved for internal use"),
            status.HTTP_400_BAD_REQUEST,
            "Field __tc_workspace_id is reserved for internal use",
            id="create-value-error",
        ),
        pytest.param(
            "DELETE",
            "/case-fields/bad-field",
            None,
            "delete_field",
            ValueError(
                "Identifier must contain only letters, numbers, and underscores"
            ),
            status.HTTP_400_BAD_REQUEST,
            "Identifier must contain only letters, numbers, and underscores",
            id="delete-value-error",
        ),
        pytest.param(
            "PATCH",
            "/case-fields/bad-field",
            {"default": "value"},
            "update_field",
            TracecatValidationError("Field nickname is reserved"),
            status.HTTP_400_BAD_REQUEST,
            "Field nickname is reserved",
            id="update-tracecat-validation-error",
        ),
        pytest.param(
            "DELETE",
            "/case-fields/bad-field",
            None,
            "delete_field",
            TracecatValidationError("Field nickname is reserved"),
            status.HTTP_400_BAD_REQUEST,
            "Field nickname is reserved",
            id="delete-tracecat-validation-error",
        ),
        pytest.param(
            "PATCH",
            "/case-fields/existing_field",
            {"name": "duplicate_name"},
            "update_field",
            _duplicate_column_error(),
            status.HTTP_409_CONFLICT,
            "A field with the name 'duplicate_name' already exists",
            id="update-duplicate-name",
        ),
    ],
)
async def test_case_field_errors_map_to_http_status(
    client: TestClient,
    test_admin_role: Role,
    method: str,
    path: str,
    payload: dict[str, str] | None,
    service_method: str,
    error: Exception,
    status_code: int,
    detail: str,
) -> None:
    with patch.object(cases_router, "CaseFieldsService") as mock_service_cls:
        mock_service = AsyncMock()
        getattr(mock_service, service_method).side_effect = error
        mock_service_cls.return_value = mock_service

        response = client.request(
            method,
            path,
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json=payload,
        )

    assert response.status_code == status_code
    assert response.json()["detail"] == detail


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("method", "payload"),
    [("GET", None), ("PATCH", {"summary": "Updated Summary"})],
)
async def test_case_not_found_returns_404(
    client: TestClient,
    test_admin_role: Role,
    method: str,
    payload: dict[str, str] | None,
) -> None:
    with patch.object(cases_router, "CasesService") as mock_service_cls:
        mock_svc = AsyncMock()
        mock_svc.get_case.return_value = None
        mock_service_cls.return_value = mock_svc

        response = client.request(
            method,
            f"/cases/{uuid.uuid4()}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json=payload,
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_list_case_versions_rejects_oversized_cursor(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Oversized cursors are rejected before reaching the service."""
    with patch.object(case_versions_router, "CasesService") as mock_service_cls:
        response = client.get(
            f"/cases/{uuid.uuid4()}/versions",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "cursor": "x" * 8193,
            },
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    mock_service_cls.assert_not_called()


@pytest.mark.anyio
async def test_list_case_versions_missing_case_returns_404(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Version history does not treat a missing case as empty history."""
    case_id = uuid.uuid4()
    with patch.object(case_versions_router, "CasesService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.case_exists.return_value = False
        mock_service_cls.return_value = mock_service

        response = client.get(
            f"/cases/{case_id}/versions",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    mock_service.get_case.assert_not_called()
    mock_service.versions.list_versions.assert_not_awaited()


@pytest.mark.anyio
async def test_compare_case_version_success_and_mismatch_404(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    """Compare returns raw snapshots and hides mismatched versions."""
    selected_id = uuid.uuid4()
    predecessor_id = uuid.uuid4()
    comparison = CaseVersionCompareRead(
        selected=CaseVersionContentRead(
            id=selected_id,
            field=CaseVersionField.DESCRIPTION,
            version=2,
            content="New body",
        ),
        predecessor=CaseVersionContentRead(
            id=predecessor_id,
            field=CaseVersionField.DESCRIPTION,
            version=1,
            content="Old body",
        ),
    )
    with patch.object(case_versions_router, "CasesService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.versions.compare_with_predecessor.side_effect = [
            comparison,
            None,
        ]
        mock_service_cls.return_value = mock_service

        success = client.get(
            f"/cases/{mock_case.id}/versions/{selected_id}/compare",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )
        missing = client.get(
            f"/cases/{mock_case.id}/versions/{uuid.uuid4()}/compare",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert success.status_code == status.HTTP_200_OK
    assert success.json()["predecessor"]["content"] == "Old body"
    assert "diff" not in success.json()
    assert missing.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_restore_case_version_success_and_mismatch_404(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    """Restore returns a typed confirmation and maps scoped misses to 404."""
    version_id = uuid.uuid4()
    restored = CaseVersionRestoreRead(
        case_id=mock_case.id,
        restored_from_version_id=version_id,
        field=CaseVersionField.SUMMARY,
    )
    with patch.object(case_versions_router, "CasesService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.restore_version.side_effect = [
            restored,
            TracecatNotFoundError("Case version not found"),
        ]
        mock_service_cls.return_value = mock_service

        success = client.post(
            f"/cases/{mock_case.id}/versions/{version_id}/restore",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )
        missing = client.post(
            f"/cases/{mock_case.id}/versions/{uuid.uuid4()}/restore",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert success.status_code == status.HTTP_200_OK
    assert success.json() == {
        "restored": True,
        "case_id": str(mock_case.id),
        "restored_from_version_id": str(version_id),
        "field": "summary",
    }
    assert missing.status_code == status.HTTP_404_NOT_FOUND


@pytest.mark.anyio
async def test_batch_update_cases_success_and_route_non_collision(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    """The literal batch route and UUID single-case route dispatch separately."""
    second_case_id = uuid.uuid4()
    batch_response = CaseBatchResponse(
        results=[
            CaseBatchItemResult(case_id=mock_case.id, success=True),
            CaseBatchItemResult(
                case_id=second_case_id,
                success=False,
                error="Case not found",
            ),
        ],
        succeeded=1,
        failed=1,
    )

    with (
        patch.object(cases_router, "CasesService") as mock_service_cls,
        patch.object(cases_router, "CaseDropdownValuesService") as MockDropdownService,
    ):
        mock_svc = AsyncMock()
        mock_svc.batch_update_cases.return_value = batch_response
        mock_svc.get_case.return_value = mock_case
        mock_svc.update_case.return_value = mock_case
        MockDropdownService.return_value = _mock_case_read_services(mock_svc)
        mock_service_cls.return_value = mock_svc

        batch_result = client.post(
            "/cases/batch-update",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={
                "case_ids": [str(mock_case.id), str(second_case_id)],
                "update": {"summary": "Updated Summary"},
            },
        )
        single_result = client.patch(
            f"/cases/{mock_case.id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"summary": "Single update"},
        )

    assert batch_result.status_code == status.HTTP_200_OK
    assert batch_result.json() == {
        "results": [
            {"case_id": str(mock_case.id), "success": True, "error": None},
            {
                "case_id": str(second_case_id),
                "success": False,
                "error": "Case not found",
            },
        ],
        "succeeded": 1,
        "failed": 1,
    }
    assert single_result.status_code == status.HTTP_200_OK
    assert single_result.json()["id"] == str(mock_case.id)
    mock_svc.batch_update_cases.assert_awaited_once()
    mock_svc.update_case.assert_awaited_once()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "payload", "service_method"),
    [
        (
            "/cases/batch-update",
            {"case_ids": [str(uuid.uuid4())], "update": {"summary": "x"}},
            "batch_update_cases",
        ),
        (
            "/cases/batch-delete",
            {"case_ids": [str(uuid.uuid4())]},
            "batch_delete_cases",
        ),
        (
            "/cases/batch-set-parent",
            {"case_ids": [str(uuid.uuid4())], "parent_id": str(uuid.uuid4())},
            "batch_set_parent",
        ),
        (
            "/cases/batch-clear-parent",
            {"case_ids": [str(uuid.uuid4())]},
            "batch_clear_parent",
        ),
    ],
)
async def test_batch_case_lock_conflict_returns_409(
    client: TestClient,
    test_admin_role: Role,
    path: str,
    payload: dict[str, object],
    service_method: str,
) -> None:
    """Batch lock timeouts map the service conflict to HTTP 409."""
    with patch.object(cases_router, "CasesService") as mock_service_cls:
        mock_svc = AsyncMock()
        getattr(mock_svc, service_method).side_effect = TracecatConflictError(
            "Timed out waiting to lock cases"
        )
        mock_service_cls.return_value = mock_svc

        response = client.post(
            path,
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json=payload,
        )

    assert response.status_code == status.HTTP_409_CONFLICT
    assert response.json() == {"detail": "Timed out waiting to lock cases"}


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/cases/batch-update", {"case_ids": [], "update": {"summary": "x"}}),
        ("/cases/batch-delete", {"case_ids": []}),
        (
            "/cases/batch-set-parent",
            {"case_ids": [], "parent_id": str(uuid.uuid4())},
        ),
        ("/cases/batch-clear-parent", {"case_ids": []}),
    ],
)
async def test_batch_case_routes_reject_empty_case_ids(
    client: TestClient,
    test_admin_role: Role,
    path: str,
    payload: dict[str, object],
) -> None:
    """Batch routes reject empty case ID lists before calling the service."""
    with patch.object(cases_router, "CasesService") as mock_service_cls:
        response = client.post(
            path,
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json=payload,
        )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    mock_service_cls.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "payload"),
    [
        (
            "/cases/batch-update",
            {"case_ids": [str(uuid.uuid4())], "update": {"summary": "x"}},
        ),
        ("/cases/batch-delete", {"case_ids": [str(uuid.uuid4())]}),
    ],
)
async def test_batch_case_routes_enforce_scopes(
    client: TestClient,
    test_admin_role: Role,
    path: str,
    payload: dict[str, object],
) -> None:
    """Batch routes require their corresponding case mutation scope."""
    role = test_admin_role.model_copy(update={"scopes": frozenset({"case:read"})})
    token = ctx_role.set(role)
    try:
        with patch.object(cases_router, "CasesService") as mock_service_cls:
            response = client.post(
                path,
                params={"workspace_id": str(test_admin_role.workspace_id)},
                json=payload,
            )
    finally:
        ctx_role.reset(token)

    assert response.status_code == status.HTTP_403_FORBIDDEN
    mock_service_cls.assert_not_called()


@pytest.mark.anyio
async def test_update_case_invalid_integer_fields_returns_400(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    """Invalid custom-field integer input should be surfaced as a client error."""
    with patch.object(cases_router, "CasesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.get_case.return_value = mock_case
        mock_svc.update_case.side_effect = ValueError("Invalid integer value: '1.5'")
        MockService.return_value = mock_svc

        response = client.patch(
            f"/cases/{mock_case.id}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"fields": {"attempts": "1.5"}},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Invalid integer value: '1.5'"


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


_PUBLIC = ("client", cases_router, "")
_INTERNAL = ("action_gateway_client", internal_cases_router, "/internal")
_SURFACE_PARAMS = [
    pytest.param(*_PUBLIC, id="public"),
    pytest.param(*_INTERNAL, id="internal"),
]
_SEARCH_PATH_PARAMS = [
    pytest.param(*_PUBLIC, "/cases/search", id="public-search"),
    pytest.param(*_PUBLIC, "/cases/search/aggregate", id="public-aggregate"),
    pytest.param(*_INTERNAL, "/cases/search", id="internal-search"),
]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("client_fixture", "router", "prefix", "path", "detail"),
    [
        pytest.param(
            *_PUBLIC, "/cases", "Field case_id is a reserved field", id="public-list"
        ),
        pytest.param(
            *_PUBLIC,
            "/cases/search",
            "Field case_id is a reserved field",
            id="public-search",
        ),
        pytest.param(
            *_INTERNAL,
            "/cases",
            "Invalid request for case field hydration",
            id="internal-list",
        ),
        pytest.param(
            *_INTERNAL,
            "/cases/search",
            "Invalid request for case field hydration",
            id="internal-search",
        ),
    ],
)
async def test_case_listing_validates_field_ids_even_when_page_is_empty(
    request: pytest.FixtureRequest,
    test_admin_role: Role,
    client_fixture: str,
    router: ModuleType,
    prefix: str,
    path: str,
    detail: str,
) -> None:
    """Invalid field hydration requests should not depend on the page containing rows."""
    http: TestClient = request.getfixturevalue(client_fixture)
    empty_page = CursorPaginatedResponse[CaseReadMinimal](
        items=[],
        next_cursor=None,
        prev_cursor=None,
        has_more=False,
        has_previous=False,
    )
    with (
        patch.object(router, "CasesService") as MockService,
        patch.object(router, "CaseFieldsService") as MockFieldsService,
    ):
        mock_svc = AsyncMock()
        mock_svc.list_cases.return_value = empty_page
        mock_svc.search_cases.return_value = empty_page
        mock_fields_svc = AsyncMock()
        mock_fields_svc.batch_get_fields.side_effect = ValueError(
            "Field case_id is a reserved field"
        )
        MockService.return_value = mock_svc
        MockFieldsService.return_value = mock_fields_svc

        response = http.get(
            f"{prefix}{path}",
            params=[
                ("workspace_id", str(test_admin_role.workspace_id)),
                ("field_ids", "case_id"),
            ],
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == detail
    mock_fields_svc.batch_get_fields.assert_awaited_once_with(
        case_ids=[],
        field_ids=["case_id"],
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("client_fixture", "router", "prefix", "path"), _SEARCH_PATH_PARAMS
)
@pytest.mark.parametrize("include_known", [False, True])
async def test_case_search_rejects_unresolved_tags(
    request: pytest.FixtureRequest,
    test_admin_role: Role,
    mock_case_tag: CaseTag,
    client_fixture: str,
    router: ModuleType,
    prefix: str,
    path: str,
    include_known: bool,
) -> None:
    http: TestClient = request.getfixturevalue(client_fixture)
    tags = [mock_case_tag.ref, "missing-tag"] if include_known else ["missing-tag"]
    results = [mock_case_tag, NoResultFound()] if include_known else [NoResultFound()]
    with (
        patch.object(router, "CasesService") as service_cls,
        patch.object(
            CaseTagsService, "get_tag_by_ref_or_id", new=AsyncMock(side_effect=results)
        ),
    ):
        response = http.get(
            f"{prefix}{path}",
            params=[("workspace_id", str(test_admin_role.workspace_id))]
            + [("tags", tag) for tag in tags],
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Unknown case tag filter: 'missing-tag'"
    service_cls.return_value.search_cases.assert_not_called()
    service_cls.return_value.get_search_case_aggregates.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("client_fixture", "router", "prefix", "path"), _SEARCH_PATH_PARAMS
)
@pytest.mark.parametrize("tags", [[], [""]])
async def test_case_search_empty_tags_remain_unfiltered(
    request: pytest.FixtureRequest,
    test_admin_role: Role,
    client_fixture: str,
    router: ModuleType,
    prefix: str,
    path: str,
    tags: list[str],
) -> None:
    http: TestClient = request.getfixturevalue(client_fixture)
    with (
        patch.object(router, "CasesService") as service_cls,
        patch.object(
            CaseTagsService, "get_tag_by_ref_or_id", new=AsyncMock()
        ) as resolve,
    ):
        service = AsyncMock()
        service.search_cases.return_value = CursorPaginatedResponse[CaseReadMinimal](
            items=[], has_more=False, has_previous=False
        )
        service.get_search_case_aggregates.return_value = CaseSearchAggregateRead(
            total=0, status_groups=CaseStatusGroupCounts()
        )
        service_cls.return_value = service
        response = http.get(
            f"{prefix}{path}",
            params=[("workspace_id", str(test_admin_role.workspace_id))]
            + [("tags", tag) for tag in tags],
        )

    assert response.status_code == status.HTTP_200_OK
    resolve.assert_not_awaited()
    search = (
        service.search_cases
        if path == "/cases/search"
        else service.get_search_case_aggregates
    )
    assert search.call_args.kwargs["tag_ids"] is None


@pytest.mark.anyio
@pytest.mark.parametrize(("client_fixture", "router", "prefix"), _SURFACE_PARAMS)
async def test_update_comment_wrong_case_returns_not_found(
    request: pytest.FixtureRequest,
    test_admin_role: Role,
    mock_case: Case,
    client_fixture: str,
    router: ModuleType,
    prefix: str,
) -> None:
    """Comment updates should be case scoped."""
    http: TestClient = request.getfixturevalue(client_fixture)
    with (
        patch.object(router, "CasesService") as mock_cases_service_cls,
        patch.object(router, "CaseCommentsService") as mock_comments_service_cls,
    ):
        mock_cases_service = AsyncMock()
        mock_cases_service.get_case.return_value = mock_case
        mock_cases_service_cls.return_value = mock_cases_service

        mock_comments_service = AsyncMock()
        mock_comments_service.get_comment_in_case.return_value = None
        mock_comments_service_cls.return_value = mock_comments_service

        response = http.patch(
            f"{prefix}/cases/{mock_case.id}/comments/{uuid.uuid4()}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"content": "Updated"},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    mock_comments_service.update_comment.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(("client_fixture", "router", "prefix"), _SURFACE_PARAMS)
async def test_update_comment_reparenting_returns_bad_request(
    request: pytest.FixtureRequest,
    test_admin_role: Role,
    mock_case: Case,
    client_fixture: str,
    router: ModuleType,
    prefix: str,
) -> None:
    """Reparent attempts should return a validation error."""
    http: TestClient = request.getfixturevalue(client_fixture)
    with (
        patch.object(router, "CasesService") as mock_cases_service_cls,
        patch.object(router, "CaseCommentsService") as mock_comments_service_cls,
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

        response = http.patch(
            f"{prefix}/cases/{mock_case.id}/comments/{uuid.uuid4()}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"parent_id": str(uuid.uuid4())},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Changing a comment parent is not supported"


@pytest.mark.anyio
async def test_search_cases_forwards_exact_uuid_and_explicit_filters(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Exact UUID lookup leaves transport filters intact and does not view cases."""
    case_id = uuid.UUID("00000000-0000-4000-8000-000000000042")
    start_time = datetime(2024, 1, 1, tzinfo=UTC)
    with patch.object(cases_router, "CasesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.search_cases.return_value = CursorPaginatedResponse[CaseReadMinimal](
            items=[], has_more=False, has_previous=False
        )
        MockService.return_value = mock_svc
        response = client.get(
            "/cases/search",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "case_id": str(case_id),
                "promote_exact_match": "true",
                "match_none": "true",
                "start_time": start_time.isoformat(),
                "status": "new",
                "hierarchy": "top_level",
                "limit": 1,
            },
        )
        assert response.status_code == status.HTTP_200_OK
        kwargs = mock_svc.search_cases.call_args.kwargs
        assert kwargs["promote_exact_match"] is True
        assert kwargs["match_none"] is True
        assert kwargs["case_id"] == case_id
        assert kwargs["start_time"] == start_time
        assert kwargs["status"] == [CaseStatus.NEW]
        assert kwargs["hierarchy"] == "top_level"
        mock_svc.get_case.assert_not_called()


@pytest.mark.anyio
async def test_search_cases_rejects_invalid_exact_uuid(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Invalid exact references receive normal UUID query validation."""
    with patch.object(cases_router, "CasesService") as MockService:
        response = client.get(
            "/cases/search",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "case_id": "invalid-synthetic-uuid",
            },
        )
        assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
        MockService.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("reference", "detail"),
    [
        ("CASE-9999999999", "Short ID number cannot exceed 2147483647"),
        ("CASE-12345678901", "Short ID must match CASE-<number> or <number>"),
    ],
)
async def test_search_cases_rejects_oversized_exact_short_id(
    client: TestClient,
    test_admin_role: Role,
    reference: str,
    detail: str,
) -> None:
    """Pasted oversized short IDs receive a readable 400 from real validation."""
    with (
        patch.object(
            cases_router.CasesService,
            "has_entitlement",
            new=AsyncMock(return_value=True),
        ),
        patch.object(CaseHierarchyService, "enabled", new=AsyncMock(return_value=True)),
    ):
        response = client.get(
            "/cases/search",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "short_id": reference,
            },
        )
        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["detail"] == detail


@pytest.mark.anyio
async def test_search_cases_hydrates_requested_fields_and_durations(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    """Test GET /cases/search forwards duration flag and selected field IDs."""
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
            num_sub_cases=0,
            field_values={"priority_reason": "Synthetic impact"},
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
            params=[
                ("workspace_id", str(test_admin_role.workspace_id)),
                ("field_ids", "priority_reason"),
                ("include_durations", "true"),
            ],
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["items"][0]["field_values"] == {
            "priority_reason": "Synthetic impact"
        }
        assert mock_svc.search_cases.call_args.kwargs["include_durations"] is True
        assert mock_svc.search_cases.call_args.kwargs["field_ids"] == [
            "priority_reason"
        ]


@pytest.mark.anyio
async def test_search_cases_forwards_include_payload(
    client: TestClient,
    test_admin_role: Role,
    mock_case: Case,
) -> None:
    """Test GET /cases/search forwards the include_payload flag to the service."""
    with patch.object(cases_router, "CasesService") as MockService:
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
            payload={"alert_id": "abc-123"},
            num_tasks_completed=0,
            num_tasks_total=0,
            num_sub_cases=0,
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
            params=[
                ("workspace_id", str(test_admin_role.workspace_id)),
                ("include_payload", "true"),
            ],
        )

        assert response.status_code == status.HTTP_200_OK
        assert response.json()["items"][0]["payload"] == {"alert_id": "abc-123"}
        assert mock_svc.search_cases.call_args.kwargs["include_payload"] is True


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("path", "method"), [("/cases", "list_cases"), ("/cases/search", "search_cases")]
)
@pytest.mark.parametrize("include_count", [None, True, False])
async def test_case_pages_forward_count_control_with_legacy_default(
    client: TestClient,
    test_admin_role: Role,
    path: str,
    method: str,
    include_count: bool | None,
) -> None:
    with patch.object(cases_router, "CasesService") as MockService:
        mock_svc = AsyncMock()
        response = CursorPaginatedResponse[CaseReadMinimal](
            items=[], total_estimate=0 if include_count is not False else None
        )
        mock_svc.list_cases.return_value = response
        mock_svc.search_cases.return_value = response
        MockService.return_value = mock_svc
        params = {"workspace_id": str(test_admin_role.workspace_id)}
        if include_count is not None:
            params["include_count"] = str(include_count).lower()
        result = client.get(path, params=params)
        assert result.status_code == status.HTTP_200_OK
        called_method = (
            mock_svc.list_cases if method == "list_cases" else mock_svc.search_cases
        )
        assert called_method.call_args.kwargs["include_count"] is (
            include_count is not False
        )
        assert result.json()["total_estimate"] == (
            0 if include_count is not False else None
        )


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
            num_sub_cases=0,
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
async def test_search_cases_rejects_parent_id_with_top_level(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Contradictory hierarchy filters are a client error, not a 500."""
    with patch.object(cases_router, "CasesService") as MockService:
        mock_svc = AsyncMock()
        mock_svc.search_cases.side_effect = TracecatValidationError(
            "parent_id cannot be combined with hierarchy=top_level"
        )
        MockService.return_value = mock_svc

        response = client.get(
            "/cases/search",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "parent_id": str(uuid.uuid4()),
                "hierarchy": "top_level",
            },
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "cannot be combined" in response.json()["detail"]
