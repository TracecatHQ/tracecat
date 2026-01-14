import uuid
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from tracecat.auth.types import AccessLevel, Role
from tracecat.cases import internal_router
from tracecat.cases.schemas import CaseCommentUpdate


@pytest.mark.anyio
async def test_update_comment_simple_loads_case_context() -> None:
    session = AsyncMock()
    role = Role(
        type="user",
        access_level=AccessLevel.BASIC,
        workspace_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )

    comment_id = uuid.uuid4()
    case_id = uuid.uuid4()
    params = CaseCommentUpdate(content="Updated comment")

    comment = SimpleNamespace(id=comment_id, case_id=case_id)
    case = SimpleNamespace(id=case_id)
    updated_comment = SimpleNamespace(
        id=comment_id,
        created_at=datetime.now(UTC),
        updated_at=datetime.now(UTC),
        content=params.content,
        parent_id=None,
        case_id=case_id,
        workspace_id=role.workspace_id,
        user_id=role.user_id,
        last_edited_at=datetime.now(UTC),
    )

    comments_svc = AsyncMock()
    comments_svc.get_comment.return_value = comment
    comments_svc.update_comment.return_value = updated_comment

    cases_svc = AsyncMock()
    cases_svc.get_case.return_value = case

    with (
        patch.object(
            internal_router, "CaseCommentsService", return_value=comments_svc
        ),
        patch.object(internal_router, "CasesService", return_value=cases_svc),
    ):
        result = await internal_router.update_comment_simple(
            role=role,
            session=session,
            comment_id=comment_id,
            params=params,
        )

    cases_svc.get_case.assert_awaited_once_with(case_id)
    comments_svc.update_comment.assert_awaited_once_with(case, comment, params)
    assert result.id == updated_comment.id
    assert result.case_id == updated_comment.case_id
