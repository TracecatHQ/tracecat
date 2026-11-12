import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.cases import dependencies as case_dependencies
from tracecat.cases import internal_router as internal_cases_router
from tracecat.cases.enums import CaseEventType, CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.hierarchy import CaseHierarchyService
from tracecat.cases.rows.schemas import CaseTableRowRead
from tracecat.cases.schemas import CaseBatchItemResult, CaseBatchResponse
from tracecat.db.models import Case, CaseTag, Workspace
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)


@pytest.fixture(autouse=True)
def stub_case_hierarchy():
    """Routes hydrate hierarchy with real queries; the session here is mocked."""
    with patch.object(
        CaseHierarchyService, "get_hierarchy", new=AsyncMock(return_value={})
    ):
        yield


@pytest.fixture
def mock_internal_case(test_workspace: Workspace) -> Case:
    case = Case(
        workspace_id=test_workspace.id,
        summary="Internal case summary",
        description="Internal case description",
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.LOW,
        status=CaseStatus.NEW,
        payload={"source": "test"},
        assignee_id=None,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    case.id = uuid.uuid4()
    case.case_number = 1
    case.tags = []
    case.assignee = None
    case.dropdown_values = []
    return case


def _build_case_row(case_id: uuid.UUID) -> CaseTableRowRead:
    return CaseTableRowRead(
        id=uuid.uuid4(),
        case_id=case_id,
        table_id=uuid.uuid4(),
        table_name="table_name",
        row_id=uuid.uuid4(),
        row_data={"value": "row data"},
        is_row_available=True,
        created_at=datetime(2026, 1, 1, tzinfo=UTC),
        updated_at=datetime(2026, 1, 1, tzinfo=UTC),
    )


@pytest.mark.anyio
async def test_internal_get_case_include_rows_hydrates_rows(
    action_gateway_client: TestClient, test_admin_role: Role, mock_internal_case: Case
) -> None:
    row = _build_case_row(mock_internal_case.id)
    with (
        patch.object(internal_cases_router, "CasesService") as mock_service_cls,
        patch.object(
            internal_cases_router,
            "_list_case_dropdown_values",
            new=AsyncMock(return_value=[]),
        ),
        patch.object(
            internal_cases_router,
            "_list_case_rows",
            new=AsyncMock(return_value=[row]),
        ) as mock_list_rows,
    ):
        mock_service = AsyncMock()
        mock_service.get_case.return_value = mock_internal_case
        mock_service.fields = AsyncMock()
        mock_service.fields.get_fields.return_value = {}
        mock_service.fields.list_fields.return_value = []
        mock_service_cls.return_value = mock_service

        response = action_gateway_client.get(
            f"/internal/cases/{mock_internal_case.id}",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "include_rows": "true",
            },
        )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert len(data["rows"]) == 1
    assert data["rows"][0]["id"] == str(row.id)
    assert mock_list_rows.await_count == 1


@pytest.mark.anyio
async def test_internal_update_case_include_rows_hydrates_rows(
    action_gateway_client: TestClient, test_admin_role: Role, mock_internal_case: Case
) -> None:
    row = _build_case_row(mock_internal_case.id)
    with (
        patch.object(internal_cases_router, "CasesService") as mock_service_cls,
        patch.object(
            internal_cases_router,
            "_list_case_dropdown_values",
            new=AsyncMock(return_value=[]),
        ),
        patch.object(
            internal_cases_router,
            "_list_case_rows",
            new=AsyncMock(return_value=[row]),
        ) as mock_list_rows,
    ):
        mock_service = AsyncMock()
        mock_service.get_case.return_value = mock_internal_case
        updated_case = mock_internal_case
        updated_case.summary = "Updated summary"
        mock_service.update_case.return_value = updated_case
        mock_service.fields = AsyncMock()
        mock_service.fields.get_fields.return_value = {}
        mock_service.fields.list_fields.return_value = []
        mock_service_cls.return_value = mock_service

        response = action_gateway_client.patch(
            f"/internal/cases/{mock_internal_case.id}",
            params={
                "workspace_id": str(test_admin_role.workspace_id),
                "include_rows": "true",
            },
            json={"summary": "Updated summary"},
        )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["summary"] == "Updated summary"
    assert len(data["rows"]) == 1
    assert data["rows"][0]["id"] == str(row.id)
    assert mock_list_rows.await_count == 1


@pytest.mark.anyio
async def test_internal_create_case_simple_invalid_numeric_fields_returns_400(
    action_gateway_client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch.object(internal_cases_router, "CasesService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.create_case.side_effect = ValueError(
            "Invalid numeric value: 'abc'"
        )
        mock_service_cls.return_value = mock_service

        response = action_gateway_client.post(
            "/internal/cases/simple",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={
                "summary": "Internal case summary",
                "description": "Internal case description",
                "priority": "medium",
                "severity": "low",
                "status": "new",
                "fields": {"score": "abc"},
            },
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Invalid numeric value: 'abc'"


@pytest.mark.anyio
async def test_internal_update_case_simple_invalid_integer_fields_returns_400(
    action_gateway_client: TestClient,
    test_admin_role: Role,
    mock_internal_case: Case,
) -> None:
    with patch.object(internal_cases_router, "CasesService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_case.return_value = mock_internal_case
        mock_service.update_case.side_effect = ValueError(
            "Invalid integer value: '1.5'"
        )
        mock_service_cls.return_value = mock_service

        response = action_gateway_client.patch(
            f"/internal/cases/{mock_internal_case.id}/simple",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"fields": {"attempts": "1.5"}},
        )

    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == "Invalid integer value: '1.5'"


@pytest.mark.anyio
async def test_internal_update_case_simple_removed_tag_returns_400(
    action_gateway_client: TestClient,
    test_admin_role: Role,
    mock_internal_case: Case,
) -> None:
    """A tag removed after listing remains a client error during replacement."""
    tag = CaseTag(id=uuid.uuid4(), name="Escalated", ref="escalated")
    message = f"Tag {tag.ref} not found on case {mock_internal_case.id}"
    with patch.object(internal_cases_router, "CasesService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_case.return_value = mock_internal_case
        mock_service.update_case.return_value = mock_internal_case
        mock_service.tags.list_tags_for_case.return_value = [tag]
        mock_service.tags.remove_case_tag.side_effect = TracecatNotFoundError(message)
        mock_service_cls.return_value = mock_service

        response = action_gateway_client.patch(
            f"/internal/cases/{mock_internal_case.id}/simple",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"tags": []},
        )

    mock_service.tags.remove_case_tag.assert_awaited_once_with(
        mock_internal_case.id, tag.ref
    )
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert response.json()["detail"] == message


@pytest.mark.anyio
async def test_internal_list_case_events_includes_comment_activity(
    action_gateway_client: TestClient,
    test_admin_role: Role,
    mock_internal_case: Case,
) -> None:
    db_event = AsyncMock()
    db_event.type = CaseEventType.COMMENT_CREATED
    db_event.user_id = test_admin_role.user_id
    db_event.created_at = datetime(2026, 1, 1, tzinfo=UTC)
    comment_id = uuid.uuid4()
    db_event.data = {
        "comment_id": str(comment_id),
        "parent_id": None,
        "thread_root_id": str(comment_id),
        "wf_exec_id": None,
    }

    with (
        patch.object(internal_cases_router, "CasesService") as mock_service_cls,
        patch.object(
            internal_cases_router, "search_users", new=AsyncMock(return_value=[])
        ),
    ):
        mock_service = AsyncMock()
        mock_service.get_case.return_value = mock_internal_case
        mock_service.events = AsyncMock()
        mock_service.events.list_events.return_value = [db_event]
        mock_service_cls.return_value = mock_service

        response = action_gateway_client.get(
            f"/internal/cases/{mock_internal_case.id}/events",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["events"][0]["type"] == "comment_created"
    assert data["events"][0]["comment_id"] == str(comment_id)


@pytest.mark.anyio
@pytest.mark.parametrize("case_ref", ["CASE-0001", "1"])
async def test_internal_get_case_resolves_short_id(
    action_gateway_client: TestClient,
    test_admin_role: Role,
    mock_internal_case: Case,
    case_ref: str,
) -> None:
    with (
        patch.object(case_dependencies, "CasesService") as mock_resolver_cls,
        patch.object(internal_cases_router, "CasesService") as mock_service_cls,
        patch.object(
            internal_cases_router,
            "_list_case_dropdown_values",
            new=AsyncMock(return_value=[]),
        ),
    ):
        mock_resolver = AsyncMock()
        mock_resolver.resolve_case_id.return_value = mock_internal_case.id
        mock_resolver_cls.return_value = mock_resolver
        mock_service = AsyncMock()
        mock_service.get_case.return_value = mock_internal_case
        mock_service.fields = AsyncMock()
        mock_service.fields.get_fields.return_value = {}
        mock_service.fields.list_fields.return_value = []
        mock_service_cls.return_value = mock_service

        response = action_gateway_client.get(
            f"/internal/cases/{case_ref}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["id"] == str(mock_internal_case.id)
    mock_resolver.resolve_case_id.assert_awaited_once_with(case_ref)
    mock_service.get_case.assert_awaited_once_with(
        mock_internal_case.id, track_view=True
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (TracecatNotFoundError("Case CASE-9999 not found"), 404),
        (TracecatValidationError("Case ID must be a UUID"), 422),
    ],
)
async def test_internal_case_id_resolution_errors(
    action_gateway_client: TestClient,
    test_admin_role: Role,
    error: Exception,
    expected_status: int,
) -> None:
    with (
        patch.object(case_dependencies, "CasesService") as mock_resolver_cls,
        patch.object(internal_cases_router, "CasesService") as mock_service_cls,
    ):
        mock_resolver = AsyncMock()
        mock_resolver.resolve_case_id.side_effect = error
        mock_resolver_cls.return_value = mock_resolver

        response = action_gateway_client.get(
            "/internal/cases/CASE-9999/comments",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

    assert response.status_code == expected_status
    mock_service_cls.assert_not_called()


@pytest.mark.anyio
async def test_internal_case_id_uuid_skips_lookup(
    action_gateway_client: TestClient,
    test_admin_role: Role,
    mock_internal_case: Case,
) -> None:
    with patch.object(internal_cases_router, "CasesService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.get_case.return_value = mock_internal_case
        mock_service_cls.return_value = mock_service
        with patch.object(
            internal_cases_router, "CaseCommentsService"
        ) as mock_comments_cls:
            mock_comments_cls.return_value.list_comments = AsyncMock(return_value=[])
            response = action_gateway_client.get(
                f"/internal/cases/{mock_internal_case.id}/comments",
                params={"workspace_id": str(test_admin_role.workspace_id)},
            )

    assert response.status_code == status.HTTP_200_OK
    mock_service.get_case.assert_awaited_once_with(mock_internal_case.id)


def _batch_response(case_ids: list[uuid.UUID]) -> CaseBatchResponse:
    return CaseBatchResponse(
        results=[CaseBatchItemResult(case_id=i, success=True) for i in case_ids],
        succeeded=len(case_ids),
        failed=0,
    )


@pytest.mark.anyio
async def test_internal_batch_set_parent_resolves_short_ids(
    action_gateway_client: TestClient,
    test_admin_role: Role,
) -> None:
    child_id, short_child_id, parent_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    resolved = {
        str(child_id): child_id,
        "CASE-0043": short_child_id,
        "CASE-0042": parent_id,
    }
    with patch.object(internal_cases_router, "CasesService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.resolve_case_id.side_effect = lambda ref: resolved[ref]
        mock_service.batch_set_parent.return_value = _batch_response(
            [child_id, short_child_id]
        )
        mock_service_cls.return_value = mock_service

        response = action_gateway_client.post(
            "/internal/cases/batch-set-parent",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"case_ids": [str(child_id), "CASE-0043"], "parent_id": "CASE-0042"},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["succeeded"] == 2
    mock_service.batch_set_parent.assert_awaited_once_with(
        [child_id, short_child_id], parent_id
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "expected_status"),
    [
        (TracecatNotFoundError("Case CASE-0042 not found"), status.HTTP_404_NOT_FOUND),
        (
            TracecatValidationError("Parent case must be a top-level case"),
            status.HTTP_400_BAD_REQUEST,
        ),
        (TracecatConflictError("Cases are locked"), status.HTTP_409_CONFLICT),
    ],
)
async def test_internal_batch_set_parent_maps_errors(
    action_gateway_client: TestClient,
    test_admin_role: Role,
    error: Exception,
    expected_status: int,
) -> None:
    with patch.object(internal_cases_router, "CasesService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.resolve_case_id.side_effect = lambda ref: uuid.UUID(ref)
        mock_service.batch_set_parent.side_effect = error
        mock_service_cls.return_value = mock_service

        response = action_gateway_client.post(
            "/internal/cases/batch-set-parent",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"case_ids": [str(uuid.uuid4())], "parent_id": str(uuid.uuid4())},
        )

    assert response.status_code == expected_status
    assert response.json()["detail"] == str(error)


@pytest.mark.anyio
async def test_internal_batch_set_parent_unknown_short_id_returns_404(
    action_gateway_client: TestClient,
    test_admin_role: Role,
) -> None:
    with patch.object(internal_cases_router, "CasesService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.resolve_case_id.side_effect = TracecatNotFoundError(
            "Case CASE-9999 not found"
        )
        mock_service_cls.return_value = mock_service

        response = action_gateway_client.post(
            "/internal/cases/batch-set-parent",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"case_ids": ["CASE-9999"], "parent_id": "CASE-0042"},
        )

    assert response.status_code == status.HTTP_404_NOT_FOUND
    mock_service.batch_set_parent.assert_not_called()


@pytest.mark.anyio
async def test_internal_batch_clear_parent(
    action_gateway_client: TestClient,
    test_admin_role: Role,
) -> None:
    child_id = uuid.uuid4()
    with patch.object(internal_cases_router, "CasesService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.resolve_case_id.side_effect = lambda ref: child_id
        mock_service.batch_clear_parent.return_value = _batch_response([child_id])
        mock_service_cls.return_value = mock_service

        response = action_gateway_client.post(
            "/internal/cases/batch-clear-parent",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"case_ids": ["CASE-0043"]},
        )

    assert response.status_code == status.HTTP_200_OK
    assert response.json()["results"][0]["case_id"] == str(child_id)
    mock_service.batch_clear_parent.assert_awaited_once_with([child_id])


@pytest.mark.anyio
async def test_internal_batch_clear_parent_rejects_empty_case_ids(
    action_gateway_client: TestClient,
    test_admin_role: Role,
) -> None:
    response = action_gateway_client.post(
        "/internal/cases/batch-clear-parent",
        params={"workspace_id": str(test_admin_role.workspace_id)},
        json={"case_ids": []},
    )

    assert response.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT


@pytest.mark.anyio
async def test_internal_create_case_simple_resolves_parent_short_id(
    action_gateway_client: TestClient,
    test_admin_role: Role,
    mock_internal_case: Case,
) -> None:
    parent_id = uuid.uuid4()
    with patch.object(internal_cases_router, "CasesService") as mock_service_cls:
        mock_service = AsyncMock()
        mock_service.resolve_case_id.return_value = parent_id
        mock_service.create_case.return_value = mock_internal_case
        mock_service_cls.return_value = mock_service

        response = action_gateway_client.post(
            "/internal/cases/simple",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={
                "summary": "Internal case summary",
                "description": "Internal case description",
                "priority": "medium",
                "severity": "low",
                "status": "new",
                "parent_id": "CASE-0042",
            },
        )

    assert response.status_code == status.HTTP_201_CREATED
    mock_service.resolve_case_id.assert_awaited_once_with("CASE-0042")
    assert mock_service.create_case.await_args.args[0].parent_id == parent_id
