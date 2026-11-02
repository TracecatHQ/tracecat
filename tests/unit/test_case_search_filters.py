import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.filters import resolve_tag_ids
from tracecat.cases.search_schemas import CaseQuery
from tracecat.cases.search_service import case_identity_predicates
from tracecat.cases.types import ResolvedCaseTag
from tracecat.db.models import Case
from tracecat.exceptions import TracecatValidationError


@pytest.fixture
def search_role() -> Role:
    return Role(
        type="service",
        organization_id=uuid.UUID("00000000-0000-4000-8000-000000000002"),
        workspace_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
        service_id="tracecat-service",
    )


@pytest.mark.parametrize(
    ("term", "escaped_term"),
    [("%", r"\%"), ("_", r"\_"), ("\\", "\\\\"), (r"\%_", r"\\\%\_")],
)
def test_search_predicates_escape_literal_metacharacters(
    search_role: Role, term: str, escaped_term: str
) -> None:
    statement = select(Case.id).where(
        Case.workspace_id == search_role.workspace_id,
        *case_identity_predicates(CaseQuery(search_term=term)),
    )
    compiled = statement.compile(dialect=postgresql.dialect())

    assert str(compiled).count("ILIKE") == 3
    assert str(compiled).count("ESCAPE") == 3
    assert list(compiled.params.values()).count(escaped_term) == 3
    assert search_role.workspace_id in compiled.params.values()


@pytest.mark.anyio
@pytest.mark.parametrize("tags", [None, [], [""], ["", "  "]])
async def test_empty_tag_filters_remain_unfiltered(
    search_role: Role, tags: list[str] | None
) -> None:
    with patch(
        "tracecat.cases.filters.resolve_case_tag_filters", new_callable=AsyncMock
    ) as resolve:
        result = await resolve_tag_ids(
            tags=tags, role=search_role, session=AsyncMock(spec=AsyncSession)
        )

    assert result is None
    resolve.assert_not_awaited()


@pytest.mark.anyio
@pytest.mark.parametrize("include_known", [False, True])
async def test_unresolved_tag_filters_are_rejected(
    search_role: Role, include_known: bool
) -> None:
    tags = ["known-tag", "missing-tag"] if include_known else ["missing-tag"]
    session = AsyncMock(spec=AsyncSession)
    with patch(
        "tracecat.cases.filters.resolve_case_tag_filters", new_callable=AsyncMock
    ) as resolve:
        resolve.side_effect = TracecatValidationError(
            "Unknown case tag filter: 'missing-tag'"
        )
        with pytest.raises(HTTPException) as error:
            await resolve_tag_ids(tags=tags, role=search_role, session=session)
    resolve.assert_awaited_once_with(
        session=session, workspace_id=search_role.workspace_id, tags=tags
    )

    assert error.value.status_code == 400
    assert error.value.detail == "Unknown case tag filter: 'missing-tag'"


@pytest.mark.anyio
async def test_tag_filters_preserve_ref_and_uuid_resolution(search_role: Role) -> None:
    first = ResolvedCaseTag(id=uuid.uuid4(), ref="first-tag")
    second = ResolvedCaseTag(id=uuid.uuid4(), ref="second-tag")
    session = AsyncMock(spec=AsyncSession)
    with patch(
        "tracecat.cases.filters.resolve_case_tag_filters", new_callable=AsyncMock
    ) as resolve:
        resolve.return_value = [first, second]
        result = await resolve_tag_ids(
            tags=["first-tag", "", str(second.id)], role=search_role, session=session
        )
    assert result == [first.id, second.id]
    resolve.assert_awaited_once_with(
        session=session,
        workspace_id=search_role.workspace_id,
        tags=["first-tag", str(second.id)],
    )
