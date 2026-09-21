"""Error boundaries for table search routes; unexpected failures stay server errors."""

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.exceptions import TracecatNotFoundError
from tracecat.search.embeddings.service import WorkspaceEmbeddingService
from tracecat.tables.search.router import (
    get_table_search,
    select_table_search_column,
)
from tracecat.tables.search.schemas import (
    TableSearchConfiguration,
    TableSearchSelection,
)
from tracecat.tables.search.service import TableSearchService

pytestmark = pytest.mark.anyio


@pytest.fixture
def search_role() -> Role:
    return Role(
        type="user",
        organization_id=uuid4(),
        workspace_id=uuid4(),
        user_id=uuid4(),
        service_id="tracecat-api",
        scopes=frozenset({"table:read", "table:update", "workspace:read"}),
    )


@pytest.mark.parametrize("failure_at", ["configuration", "commit"])
async def test_unexpected_selection_response_failure_is_not_invalid_input(
    search_role: Role, monkeypatch: pytest.MonkeyPatch, failure_at: str
):
    session = AsyncMock(spec=AsyncSession)
    configuration = AsyncMock(return_value=TableSearchConfiguration())
    monkeypatch.setattr(TableSearchService, "select_column", AsyncMock())
    monkeypatch.setattr(TableSearchService, "configuration", configuration)
    failure = ValueError("Synthetic internal failure")
    if failure_at == "configuration":
        configuration.side_effect = failure
    else:
        session.commit.side_effect = failure

    with pytest.raises(ValueError) as exc:
        await select_table_search_column(
            table_id=uuid4(),
            params=TableSearchSelection(
                column_id=uuid4(), enabled=True, expected_generation=0
            ),
            role=search_role,
            session=session,
        )
    assert exc.value is failure
    if failure_at == "configuration":
        session.commit.assert_not_awaited()


async def test_invalid_column_selection_keeps_safe_422_response(
    search_role: Role, monkeypatch: pytest.MonkeyPatch
):
    session = AsyncMock(spec=AsyncSession)
    monkeypatch.setattr(
        TableSearchService,
        "select_column",
        AsyncMock(side_effect=ValueError("Only TEXT columns support semantic search")),
    )
    with pytest.raises(HTTPException) as exc:
        await select_table_search_column(
            table_id=uuid4(),
            params=TableSearchSelection(
                column_id=uuid4(), enabled=True, expected_generation=0
            ),
            role=search_role,
            session=session,
        )
    assert exc.value.status_code == 422
    assert exc.value.detail == {"code": "INVALID_SELECTION"}
    session.commit.assert_not_awaited()


async def test_missing_table_is_rejected_before_provider_lookup(
    search_role: Role, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(
        TableSearchService,
        "table",
        AsyncMock(side_effect=TracecatNotFoundError("Table not found")),
    )
    provider = AsyncMock()
    monkeypatch.setattr(WorkspaceEmbeddingService, "get", provider)
    with pytest.raises(HTTPException) as exc:
        await get_table_search(
            table_id=uuid4(), role=search_role, session=AsyncMock(spec=AsyncSession)
        )
    assert exc.value.status_code == 404
    assert exc.value.detail == {"code": "NOT_FOUND"}
    provider.assert_not_awaited()


async def test_unexpected_provider_failure_is_not_a_table_404(
    search_role: Role, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setattr(TableSearchService, "table", AsyncMock())
    failure = TracecatNotFoundError("Synthetic provider resource missing")
    monkeypatch.setattr(
        WorkspaceEmbeddingService, "get", AsyncMock(side_effect=failure)
    )
    with pytest.raises(TracecatNotFoundError) as exc:
        await get_table_search(
            table_id=uuid4(), role=search_role, session=AsyncMock(spec=AsyncSession)
        )
    assert exc.value is failure
