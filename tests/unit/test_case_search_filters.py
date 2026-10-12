import uuid
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.dialects import postgresql
from sqlalchemy.exc import NoResultFound
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.filters import resolve_tag_ids
from tracecat.cases.service import CasesService
from tracecat.db.models import Case, CaseTag


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
    service = CasesService(AsyncMock(spec=AsyncSession), search_role)
    statement = select(Case.id).where(*service._build_search_filters(search_term=term))
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
    with patch("tracecat.cases.filters.CaseTagsService") as tags_service_cls:
        result = await resolve_tag_ids(
            tags=tags, role=search_role, session=AsyncMock(spec=AsyncSession)
        )

    assert result is None
    tags_service_cls.assert_not_called()


@pytest.mark.anyio
@pytest.mark.parametrize("include_known", [False, True])
async def test_unresolved_tag_filters_are_rejected(
    search_role: Role, include_known: bool
) -> None:
    known_tag = CaseTag(id=uuid.uuid4(), workspace_id=search_role.workspace_id)
    tags = ["known-tag", "missing-tag"] if include_known else ["missing-tag"]
    results = [known_tag, NoResultFound()] if include_known else [NoResultFound()]
    with patch("tracecat.cases.filters.CaseTagsService") as tags_service_cls:
        tags_service_cls.return_value.get_tag_by_ref_or_id = AsyncMock(
            side_effect=results
        )
        with pytest.raises(HTTPException) as error:
            await resolve_tag_ids(
                tags=tags, role=search_role, session=AsyncMock(spec=AsyncSession)
            )

    assert error.value.status_code == 400
    assert error.value.detail == "Unknown case tag filter: 'missing-tag'"


@pytest.mark.anyio
async def test_tag_filters_preserve_ref_and_uuid_resolution(search_role: Role) -> None:
    first = CaseTag(id=uuid.uuid4(), workspace_id=search_role.workspace_id)
    second = CaseTag(id=uuid.uuid4(), workspace_id=search_role.workspace_id)
    session = AsyncMock(spec=AsyncSession)
    with patch("tracecat.cases.filters.CaseTagsService") as tags_service_cls:
        resolve = AsyncMock(side_effect=[first, second])
        tags_service_cls.return_value.get_tag_by_ref_or_id = resolve
        result = await resolve_tag_ids(
            tags=["first-tag", "", str(second.id)], role=search_role, session=session
        )

    assert result == [first.id, second.id]
    tags_service_cls.assert_called_once_with(session, search_role)
    assert [call.args[0] for call in resolve.await_args_list] == [
        "first-tag",
        str(second.id),
    ]
