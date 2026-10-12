"""Real database coverage for global case relationship sort keys and cursors."""

import uuid
from dataclasses import dataclass
from typing import get_args

import httpx
import pytest
import sqlalchemy as sa
from fastapi import FastAPI
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.dependencies import ExecutorWorkspaceRole, WorkspaceActorRouteRole
from tracecat.auth.types import Role
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.internal_router import router as internal_cases_router
from tracecat.cases.router import cases_router
from tracecat.cases.schemas import CaseReadMinimal
from tracecat.cases.service import CasesService
from tracecat.cases.sorting import (
    CaseRelationshipSort,
    CaseSortDirection,
    case_relationship_ordering,
    case_relationship_sort_key,
)
from tracecat.contexts import ctx_role
from tracecat.db.engine import get_async_session
from tracecat.db.models import Case, CaseTag, CaseTagLink, User, Workspace
from tracecat.pagination import (
    CursorPaginatedResponse,
    CursorPaginationParams,
    Page,
    PageParams,
    PaginationError,
    PaginationErrorCode,
    paginate,
)

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db")]


@dataclass(frozen=True, slots=True)
class SortDataset:
    workspace_id: uuid.UUID
    other_workspace_id: uuid.UUID
    keys: dict[CaseRelationshipSort, dict[uuid.UUID, str | None]]
    foreign_case_id: uuid.UUID


@pytest.fixture
async def dataset(session: AsyncSession, svc_workspace: Workspace) -> SortDataset:
    other_workspace = Workspace(
        name="synthetic-sort-workspace",
        organization_id=svc_workspace.organization_id,
    )
    users = [
        User(email=email, hashed_password="synthetic-unused-password-hash")
        for email in (
            "Zulu@sorting.example.com",
            "Alpha@sorting.example.com",
            "alpha@sorting.example.com",
            "bravo@sorting.example.com",
            "former@sorting.example.com",
        )
    ]
    session.add_all([other_workspace, *users])
    await session.flush()
    assignees = [
        users[0].id,
        users[1].id,
        users[2].id,
        users[3].id,
        None,
        None,
        users[4].id,
    ]
    cases = [
        Case(
            id=uuid.UUID(f"00000000-0000-4000-8000-{number:012d}"),
            workspace_id=svc_workspace.id,
            case_number=number,
            summary=f"Synthetic sorted case {number}",
            description="Synthetic sorted case",
            assignee_id=assignee,
            status=CaseStatus.NEW,
            priority=CasePriority.LOW,
            severity=CaseSeverity.LOW,
        )
        for number, assignee in enumerate(assignees, start=1)
    ]
    foreign_case = Case(
        workspace_id=other_workspace.id,
        case_number=1,
        summary="Synthetic foreign workspace case",
        description="Synthetic foreign workspace case",
        assignee_id=users[0].id,
        status=CaseStatus.NEW,
        priority=CasePriority.LOW,
        severity=CaseSeverity.LOW,
    )
    tags = [
        CaseTag(workspace_id=svc_workspace.id, name=name, ref=f"synthetic-tag-{index}")
        for index, name in enumerate(
            ("Zulu", " alpha ", "ALPHA", "2024-01-01", "bravo")
        )
    ]
    foreign_tag = CaseTag(
        workspace_id=other_workspace.id,
        name="aaa foreign tag",
        ref="synthetic-foreign-tag",
    )
    session.add_all([*cases, foreign_case, *tags, foreign_tag])
    await session.flush()
    links = [
        (cases[0], tags[0]),
        (cases[0], tags[1]),
        (cases[1], tags[2]),
        (cases[2], tags[3]),
        (cases[3], tags[4]),
        (cases[6], tags[1]),
        (cases[6], tags[0]),
        # The FK permits a malformed cross-workspace link; the sort key must
        # still ignore that workspace's tag names.
        (cases[5], foreign_tag),
        (foreign_case, foreign_tag),
    ]
    session.add_all(
        [CaseTagLink(case_id=case.id, tag_id=tag.id) for case, tag in links]
    )
    await session.flush()
    return SortDataset(
        workspace_id=svc_workspace.id,
        other_workspace_id=other_workspace.id,
        keys={
            "assignee": dict(
                zip(
                    [case.id for case in cases],
                    [
                        "zulu@sorting.example.com",
                        "alpha@sorting.example.com",
                        "alpha@sorting.example.com",
                        "bravo@sorting.example.com",
                        None,
                        None,
                        "former@sorting.example.com",
                    ],
                    strict=True,
                )
            ),
            "tags": dict(
                zip(
                    [case.id for case in cases],
                    ["alpha", "alpha", "2024-01-01", "bravo", None, None, "alpha"],
                    strict=True,
                )
            ),
        },
        foreign_case_id=foreign_case.id,
    )


@pytest.fixture(autouse=True)
def signing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        config, "TRACECAT__SIGNING_SECRET", "synthetic-sort-signing-secret"
    )


async def get_page(
    session: AsyncSession,
    dataset: SortDataset,
    field: CaseRelationshipSort,
    direction: CaseSortDirection,
    page_size: int,
    cursor: str | None = None,
) -> Page[uuid.UUID]:
    # Rebuild keys for each request to exercise stateless cursor fingerprints.
    return await paginate(
        session,
        sa.select(Case.id).where(Case.workspace_id == dataset.workspace_id),
        page=PageParams(limit=page_size, cursor=cursor),
        order_by=case_relationship_ordering(dataset.workspace_id, field, direction),
    )


def expected_ids(
    dataset: SortDataset, field: CaseRelationshipSort, direction: CaseSortDirection
) -> list[uuid.UUID]:
    keys = dataset.keys[field]
    reverse = direction == "desc"
    assigned = sorted(
        [case_id for case_id, key in keys.items() if key is not None],
        key=lambda case_id: (keys[case_id], case_id),
        reverse=reverse,
    )
    missing = sorted(
        [case_id for case_id, key in keys.items() if key is None], reverse=reverse
    )
    return [*assigned, *missing]


@pytest.mark.parametrize("field", ["assignee", "tags"])
@pytest.mark.parametrize("direction", ["asc", "desc"])
@pytest.mark.parametrize("page_size", [1, 2, 3, 20])
async def test_global_relationship_sort_forward_and_backward(
    session: AsyncSession,
    dataset: SortDataset,
    field: CaseRelationshipSort,
    direction: CaseSortDirection,
    page_size: int,
) -> None:
    """Page size, duplicate keys, null boundaries, and reverse cursors preserve order."""
    pages: list[Page[uuid.UUID]] = []
    cursor = None
    for _ in range(len(dataset.keys[field]) + 1):
        page = await get_page(session, dataset, field, direction, page_size, cursor)
        pages.append(page)
        if not page.next_cursor:
            break
        cursor = page.next_cursor
    else:
        pytest.fail("Case sorting did not terminate its forward traversal")
    assert [case_id for page in pages for case_id in page.items] == expected_ids(
        dataset, field, direction
    )
    assert pages[0].prev_cursor is None
    assert pages[-1].next_cursor is None
    current = pages[-1]
    for expected in reversed(pages[:-1]):
        assert current.prev_cursor is not None
        current = await get_page(
            session, dataset, field, direction, page_size, current.prev_cursor
        )
        assert current.items == expected.items
    assert current.prev_cursor is None


@pytest.mark.parametrize("field", ["assignee", "tags"])
async def test_relationship_keys_are_typed_and_workspace_anchored(
    session: AsyncSession, dataset: SortDataset, field: CaseRelationshipSort
) -> None:
    """Scalar values are typed strings and cannot read foreign-case relations."""
    key = case_relationship_sort_key(dataset.workspace_id, field)
    assert key.type.python_type is str
    result = await session.execute(sa.select(Case.id, key))
    values = dict(result.tuples().all())
    assert values[dataset.foreign_case_id] is None
    for case_id, expected in dataset.keys[field].items():
        assert values[case_id] == expected


@pytest.mark.parametrize("field", ["assignee", "tags"])
async def test_relationship_cursor_cannot_cross_workspace_or_sort_direction(
    session: AsyncSession, dataset: SortDataset, field: CaseRelationshipSort
) -> None:
    """Relationship sort cursors preserve the paginator's query-scope checks."""
    first = await get_page(session, dataset, field, "asc", 1)
    assert first.next_cursor is not None
    with pytest.raises(PaginationError) as wrong_direction:
        await get_page(session, dataset, field, "desc", 1, first.next_cursor)
    assert wrong_direction.value.code == PaginationErrorCode.CURSOR_MISMATCH
    with pytest.raises(PaginationError) as wrong_workspace:
        await paginate(
            session,
            sa.select(Case.id).where(Case.workspace_id == dataset.other_workspace_id),
            page=PageParams(limit=1, cursor=first.next_cursor),
            order_by=case_relationship_ordering(
                dataset.other_workspace_id, field, "asc"
            ),
        )
    assert wrong_workspace.value.code == PaginationErrorCode.CURSOR_MISMATCH


@pytest.mark.parametrize("field", ["assignee", "tags"])
@pytest.mark.parametrize("direction", ["asc", "desc"])
@pytest.mark.parametrize("page_size", [1, 3])
async def test_service_relationship_ordering_survives_page_hydration(
    session: AsyncSession,
    dataset: SortDataset,
    svc_role: Role,
    field: CaseRelationshipSort,
    direction: CaseSortDirection,
    page_size: int,
) -> None:
    """The real projected service preserves SQL order through detail hydration."""
    service = CasesService(session, svc_role)
    pages: list[CursorPaginatedResponse[CaseReadMinimal]] = []
    cursor = None
    for _ in range(len(dataset.keys[field]) + 1):
        page = await service.search_cases(
            CursorPaginationParams(limit=page_size, cursor=cursor),
            order_by=field,
            sort=direction,
            include_count=False,
        )
        assert page.total_estimate is None
        pages.append(page)
        if page.next_cursor is None:
            break
        cursor = page.next_cursor
    else:
        pytest.fail("Service sorting did not terminate its forward traversal")
    assert [case.id for page in pages for case in page.items] == expected_ids(
        dataset, field, direction
    )
    current = pages[-1]
    for expected in reversed(pages[:-1]):
        current = await service.search_cases(
            CursorPaginationParams(limit=page_size, cursor=current.prev_cursor),
            order_by=field,
            sort=direction,
            include_count=False,
        )
        assert [case.id for case in current.items] == [
            case.id for case in expected.items
        ]


@pytest.mark.parametrize(
    "path", ["/cases", "/cases/search", "/internal/cases", "/internal/cases/search"]
)
@pytest.mark.parametrize("field", ["assignee", "tags"])
@pytest.mark.parametrize("direction", ["asc", "desc"])
async def test_public_and_internal_api_relationship_sort_pages(
    session: AsyncSession,
    dataset: SortDataset,
    svc_role: Role,
    path: str,
    field: CaseRelationshipSort,
    direction: CaseSortDirection,
) -> None:
    """Real ASGI routes validate new choices and retain paginated service order."""
    app = FastAPI()
    app.include_router(cases_router)
    app.include_router(internal_cases_router)

    async def bound_session() -> AsyncSession:
        return session

    def bound_role() -> Role:
        return svc_role

    app.dependency_overrides[get_async_session] = bound_session
    for role_type in (WorkspaceActorRouteRole, ExecutorWorkspaceRole):
        app.dependency_overrides[get_args(role_type)[1].dependency] = bound_role
    token = ctx_role.set(svc_role)
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://synthetic-case-api"
        ) as client:
            params: dict[str, str | int] = {
                "workspace_id": str(dataset.workspace_id),
                "order_by": field,
                "sort": direction,
                "limit": 2,
            }
            pages: list[CursorPaginatedResponse[CaseReadMinimal]] = []
            for _ in range(len(dataset.keys[field]) + 1):
                response = await client.get(path, params=params)
                assert response.status_code == 200, response.text
                page = CursorPaginatedResponse[CaseReadMinimal].model_validate(
                    response.json()
                )
                assert page.total_estimate == len(dataset.keys[field])
                pages.append(page)
                if page.next_cursor is None:
                    break
                params["cursor"] = page.next_cursor
            else:
                pytest.fail("HTTP sorting did not terminate its forward traversal")
            assert [case.id for page in pages for case in page.items] == expected_ids(
                dataset, field, direction
            )
            current = pages[-1]
            for expected in reversed(pages[:-1]):
                assert current.prev_cursor is not None
                params["cursor"] = current.prev_cursor
                response = await client.get(path, params=params)
                assert response.status_code == 200, response.text
                current = CursorPaginatedResponse[CaseReadMinimal].model_validate(
                    response.json()
                )
                assert [case.id for case in current.items] == [
                    case.id for case in expected.items
                ]
    finally:
        ctx_role.reset(token)
