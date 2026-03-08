import uuid
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import status
from fastapi.testclient import TestClient

from tracecat.auth.types import Role
from tracecat.cases import internal_router as internal_cases_router
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.rows.schemas import CaseTableRowRead
from tracecat.cases.schemas import CaseCommentRead, CaseCommentThreadRead
from tracecat.db.models import Case, Workspace
from tracecat.exceptions import TracecatValidationError


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


def _build_comment_read(
    *,
    content: str,
    parent_id: uuid.UUID | None = None,
    is_deleted: bool = False,
) -> CaseCommentRead:
    now = datetime(2026, 1, 1, tzinfo=UTC)
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
async def test_internal_get_case_include_rows_hydrates_rows(
    client: TestClient, test_admin_role: Role, mock_internal_case: Case
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

        response = client.get(
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
    client: TestClient, test_admin_role: Role, mock_internal_case: Case
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

        response = client.patch(
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
async def test_internal_list_comment_threads_success(
    client: TestClient,
    test_admin_role: Role,
    mock_internal_case: Case,
) -> None:
    """Internal threaded reads should preserve tombstone payloads."""
    with (
        patch.object(internal_cases_router, "CasesService") as mock_cases_service_cls,
        patch.object(
            internal_cases_router, "CaseCommentsService"
        ) as mock_comments_service_cls,
    ):
        top_level = _build_comment_read(content="Comment deleted", is_deleted=True)
        reply = _build_comment_read(
            content="Reply",
            parent_id=top_level.id,
        )
        thread = CaseCommentThreadRead(
            comment=top_level,
            replies=[reply],
            reply_count=1,
            last_activity_at=reply.updated_at,
        )

        mock_cases_service = AsyncMock()
        mock_cases_service.get_case.return_value = mock_internal_case
        mock_cases_service_cls.return_value = mock_cases_service

        mock_comments_service = AsyncMock()
        mock_comments_service.list_comment_threads.return_value = [thread]
        mock_comments_service_cls.return_value = mock_comments_service

        response = client.get(
            f"/internal/cases/{mock_internal_case.id}/comments/threads",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data[0]["comment"]["content"] == "Comment deleted"
        assert data[0]["comment"]["is_deleted"] is True
        assert data[0]["reply_count"] == 1


@pytest.mark.anyio
async def test_internal_get_comment_thread_success(
    client: TestClient,
    test_admin_role: Role,
) -> None:
    """Internal comment-id thread lookups should return the full thread."""
    with patch.object(
        internal_cases_router, "CaseCommentsService"
    ) as mock_comments_service_cls:
        top_level = _build_comment_read(content="Parent")
        reply = _build_comment_read(content="Reply", parent_id=top_level.id)
        thread = CaseCommentThreadRead(
            comment=top_level,
            replies=[reply],
            reply_count=1,
            last_activity_at=reply.updated_at,
        )

        mock_comments_service = AsyncMock()
        mock_comments_service.get_comment_thread.return_value = thread
        mock_comments_service_cls.return_value = mock_comments_service

        response = client.get(
            f"/internal/comments/{reply.id}/thread",
            params={"workspace_id": str(test_admin_role.workspace_id)},
        )

        assert response.status_code == status.HTTP_200_OK
        data = response.json()
        assert data["comment"]["id"] == str(top_level.id)
        assert data["replies"][0]["id"] == str(reply.id)


@pytest.mark.anyio
async def test_internal_update_comment_wrong_case_returns_not_found(
    client: TestClient,
    test_admin_role: Role,
    mock_internal_case: Case,
) -> None:
    """Case-scoped internal updates should return 404 for wrong-case comments."""
    with (
        patch.object(internal_cases_router, "CasesService") as mock_cases_service_cls,
        patch.object(
            internal_cases_router, "CaseCommentsService"
        ) as mock_comments_service_cls,
    ):
        mock_cases_service = AsyncMock()
        mock_cases_service.get_case.return_value = mock_internal_case
        mock_cases_service_cls.return_value = mock_cases_service

        mock_comments_service = AsyncMock()
        mock_comments_service.get_comment_in_case.return_value = None
        mock_comments_service_cls.return_value = mock_comments_service

        response = client.patch(
            f"/internal/cases/{mock_internal_case.id}/comments/{uuid.uuid4()}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"content": "Updated"},
        )

        assert response.status_code == status.HTTP_404_NOT_FOUND
        mock_comments_service.update_comment.assert_not_called()


@pytest.mark.anyio
async def test_internal_update_comment_reparenting_returns_bad_request(
    client: TestClient,
    test_admin_role: Role,
    mock_internal_case: Case,
) -> None:
    """Internal updates should surface reparent validation failures."""
    with (
        patch.object(internal_cases_router, "CasesService") as mock_cases_service_cls,
        patch.object(
            internal_cases_router, "CaseCommentsService"
        ) as mock_comments_service_cls,
    ):
        mock_cases_service = AsyncMock()
        mock_cases_service.get_case.return_value = mock_internal_case
        mock_cases_service_cls.return_value = mock_cases_service

        mock_comments_service = AsyncMock()
        mock_comments_service.get_comment_in_case.return_value = object()
        mock_comments_service.update_comment.side_effect = TracecatValidationError(
            "Changing a comment parent is not supported"
        )
        mock_comments_service_cls.return_value = mock_comments_service

        response = client.patch(
            f"/internal/cases/{mock_internal_case.id}/comments/{uuid.uuid4()}",
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={"parent_id": str(uuid.uuid4())},
        )

        assert response.status_code == status.HTTP_400_BAD_REQUEST
        assert response.json()["detail"] == "Changing a comment parent is not supported"
