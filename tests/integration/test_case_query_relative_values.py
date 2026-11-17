"""Live checks that relative windows, "me", and creator filters match the right cases."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import pytest
import sqlalchemy as sa
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.enums import CaseEventType, CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.search_adapters import CurrentUserRequiredError
from tracecat.cases.search_schemas import CaseSearchRequest, RelativeTimeWindow
from tracecat.cases.search_service import relative_window_start
from tracecat.cases.service import CasesService
from tracecat.db.models import Case, CaseEvent, User

pytestmark = [pytest.mark.anyio, pytest.mark.integration, pytest.mark.usefixtures("db")]


@dataclass(frozen=True, slots=True)
class SeededCases:
    recent_mine: uuid.UUID
    old_mine: uuid.UUID
    recent_other: uuid.UUID
    by_workflow: uuid.UUID
    future: uuid.UUID


async def _add_user(session: AsyncSession, user_id: uuid.UUID, email: str) -> None:
    session.add(User(id=user_id, email=email, hashed_password="synthetic", settings={}))
    await session.flush()


@pytest.fixture
async def seeded(session: AsyncSession, svc_role: Role) -> SeededCases:
    """Four cases: two created and assigned by the role's user, one by another
    user, and one by a workflow (no user on the creation event)."""
    assert svc_role.workspace_id is not None
    assert svc_role.user_id is not None
    other_user_id = uuid.uuid4()
    await _add_user(session, svc_role.user_id, "views-me@example.com")
    await _add_user(session, other_user_id, "views-other@example.com")

    now = datetime.now(UTC)
    specs = [
        ("recent_mine", now - timedelta(days=2), svc_role.user_id),
        ("old_mine", now - timedelta(days=40), svc_role.user_id),
        ("recent_other", now - timedelta(days=1), other_user_id),
        ("by_workflow", now - timedelta(hours=3), None),
        # Dated in the future, for example by an import with a clock skew.
        ("future", now + timedelta(days=2), other_user_id),
    ]
    ids: dict[str, uuid.UUID] = {}
    for number, (name, created_at, user_id) in enumerate(specs, start=1):
        case = Case(
            id=uuid.uuid4(),
            case_number=number,
            workspace_id=svc_role.workspace_id,
            created_at=created_at,
            summary=f"Relative value case {name}",
            description="",
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
            status=CaseStatus.NEW,
            assignee_id=user_id,
        )
        case.events = [
            CaseEvent(
                workspace_id=svc_role.workspace_id,
                type=CaseEventType.CASE_CREATED,
                data={},
                user_id=user_id,
            )
        ]
        session.add(case)
        ids[name] = case.id
    await session.flush()
    session.expunge_all()
    return SeededCases(**ids)


async def _search(
    session: AsyncSession, role: Role, **criteria: object
) -> set[uuid.UUID]:
    request = CaseSearchRequest.model_validate({"limit": 50, **criteria})
    page = await CasesService(session, role).query_cases(request)
    return {case.id for case in page.items}


async def test_created_within_excludes_cases_older_than_the_window(
    session: AsyncSession, svc_role: Role, seeded: SeededCases
) -> None:
    found = await _search(
        session, svc_role, created_within={"amount": 7, "unit": "day"}
    )

    assert found == {seeded.recent_mine, seeded.recent_other, seeded.by_workflow}


async def test_relative_window_pages_follow_their_cursor(
    session: AsyncSession, svc_role: Role, seeded: SeededCases
) -> None:
    """Page two of a relative-window search reuses page one's cursor."""
    service = CasesService(session, svc_role)
    criteria = {"created_within": {"amount": 7, "unit": "day"}, "limit": 1}
    seen: list[uuid.UUID] = []
    cursor = None
    for _ in range(3):
        request = CaseSearchRequest.model_validate({**criteria, "cursor": cursor})
        page = await service.query_cases(request)
        seen.extend(case.id for case in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert set(seen) == {
        seeded.recent_mine,
        seeded.recent_other,
        seeded.by_workflow,
    }


async def test_assignee_me_matches_the_requesting_user(
    session: AsyncSession, svc_role: Role, seeded: SeededCases
) -> None:
    found = await _search(session, svc_role, assignee_id=["me"])

    assert found == {seeded.recent_mine, seeded.old_mine}


async def test_assignee_me_and_unassigned_are_combined_with_or(
    session: AsyncSession, svc_role: Role, seeded: SeededCases
) -> None:
    found = await _search(session, svc_role, assignee_id=["me", "unassigned"])

    assert found == {seeded.recent_mine, seeded.old_mine, seeded.by_workflow}


async def test_created_by_me_uses_the_creation_event(
    session: AsyncSession, svc_role: Role, seeded: SeededCases
) -> None:
    found = await _search(session, svc_role, created_by=["me"])

    assert found == {seeded.recent_mine, seeded.old_mine}


async def test_created_by_is_null_matches_workflow_created_cases(
    session: AsyncSession, svc_role: Role, seeded: SeededCases
) -> None:
    found = await _search(
        session, svc_role, filters={"field": "created_by", "op": "is_null"}
    )

    assert found == {seeded.by_workflow}


async def test_me_without_a_user_fails_with_typed_error(
    session: AsyncSession, svc_role: Role, seeded: SeededCases
) -> None:
    service_role = svc_role.model_copy(update={"type": "service", "user_id": None})

    with pytest.raises(CurrentUserRequiredError):
        await _search(session, service_role, created_by=["me"])


@pytest.mark.parametrize(
    ("anchor", "window", "expected"),
    [
        # Calendar months, not 30 days: 31 March minus one month is 29 February
        # in a leap year and 28 February otherwise.
        (
            datetime(2024, 3, 31, 12, tzinfo=UTC),
            {"amount": 1, "unit": "month"},
            datetime(2024, 2, 29, 12, tzinfo=UTC),
        ),
        (
            datetime(2025, 3, 31, 12, tzinfo=UTC),
            {"amount": 1, "unit": "month"},
            datetime(2025, 2, 28, 12, tzinfo=UTC),
        ),
        (
            datetime(2025, 3, 31, 12, tzinfo=UTC),
            {"amount": 2, "unit": "week"},
            datetime(2025, 3, 17, 12, tzinfo=UTC),
        ),
    ],
)
async def test_relative_window_math_runs_in_postgres(
    session: AsyncSession,
    anchor: datetime,
    window: dict[str, object],
    expected: datetime,
) -> None:
    start = relative_window_start(
        RelativeTimeWindow.model_validate(window),
        anchor=sa.literal(anchor, sa.TIMESTAMP(timezone=True)),
    )

    result = (await session.execute(sa.select(start))).scalar_one()

    assert result == expected
