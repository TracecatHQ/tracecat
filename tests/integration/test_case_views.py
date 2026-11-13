"""Saved case views: visibility, edit rights, pins, and running a view."""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import pytest
from sqlalchemy import delete, update
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.enums import (
    CaseEventType,
    CasePriority,
    CaseSeverity,
    CaseStatus,
    CaseViewVisibility,
)
from tracecat.cases.search_schemas import CaseQuery, CaseSearchRequest
from tracecat.cases.service import CasesService
from tracecat.cases.views.schemas import (
    CaseViewCreate,
    CaseViewUpdate,
)
from tracecat.cases.views.service import (
    CaseViewNotFoundError,
    CaseViewPermissionError,
    CaseViewsService,
)
from tracecat.db.models import Case, CaseEvent, CaseTag, CaseView, User
from tracecat.exceptions import TracecatValidationError
from tracecat.pagination import PageParams

pytestmark = [pytest.mark.anyio, pytest.mark.integration, pytest.mark.usefixtures("db")]


@dataclass(frozen=True, slots=True)
class Actors:
    owner: Role
    """Has case:update, like the default admin role."""
    editor: Role
    """Another user with case:update."""
    reader: Role
    """Another user with only case:read."""
    workflow: Role
    """A non-user caller."""


@dataclass(frozen=True, slots=True)
class Seeded:
    phishing_open: uuid.UUID
    phishing_closed: uuid.UUID
    malware_open: uuid.UUID
    tag_ref: str


async def _user_role(session: AsyncSession, base: Role, scopes: frozenset[str]) -> Role:
    user_id = uuid.uuid4()
    session.add(
        User(
            id=user_id,
            email=f"views-{user_id.hex[:8]}@example.com",
            hashed_password="synthetic",
            settings={},
        )
    )
    await session.flush()
    return base.model_copy(update={"user_id": user_id, "scopes": scopes})


@pytest.fixture
async def actors(session: AsyncSession, svc_role: Role) -> Actors:
    full = svc_role.scopes or frozenset()
    return Actors(
        owner=await _user_role(session, svc_role, full),
        editor=await _user_role(session, svc_role, full),
        reader=await _user_role(session, svc_role, frozenset({"case:read"})),
        workflow=svc_role.model_copy(
            update={"type": "service", "user_id": None, "scopes": full}
        ),
    )


@pytest.fixture
async def seeded(session: AsyncSession, actors: Actors) -> Seeded:
    workspace_id = actors.owner.workspace_id
    assert workspace_id is not None
    tag = CaseTag(
        id=uuid.uuid4(),
        workspace_id=workspace_id,
        name="Phishing",
        ref="phishing",
        color="red",
    )
    specs = [
        ("phishing_open", CaseStatus.NEW, [tag]),
        ("phishing_closed", CaseStatus.CLOSED, [tag]),
        ("malware_open", CaseStatus.IN_PROGRESS, []),
    ]
    ids: dict[str, uuid.UUID] = {}
    for number, (name, status, tags) in enumerate(specs, start=1):
        case = Case(
            id=uuid.uuid4(),
            case_number=number,
            workspace_id=workspace_id,
            summary=f"Views case {name}",
            description="",
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
            status=status,
            assignee_id=actors.owner.user_id if name == "malware_open" else None,
            tags=tags,
        )
        case.events = [
            CaseEvent(
                workspace_id=workspace_id,
                type=CaseEventType.CASE_CREATED,
                data={},
                user_id=actors.owner.user_id,
            )
        ]
        session.add(case)
        ids[name] = case.id
    await session.flush()
    session.expunge_all()
    return Seeded(**ids, tag_ref=tag.ref)


def _views(session: AsyncSession, role: Role) -> CaseViewsService:
    return CaseViewsService(session, role)


async def _run_view(
    session: AsyncSession, role: Role, view_id: str, **extra: object
) -> set[uuid.UUID]:
    request = CaseSearchRequest.model_validate({"view_id": view_id, **extra})
    page = await CasesService(session, role).query_cases(request)
    return {case.id for case in page.items}


async def _create(
    session: AsyncSession,
    role: Role,
    *,
    visibility: CaseViewVisibility = CaseViewVisibility.PERSONAL,
    query: dict[str, object] | None = None,
) -> str:
    view = await _views(session, role).create_view(
        CaseViewCreate(
            name="Open phishing",
            visibility=visibility,
            query=CaseQuery.model_validate(
                query or {"tags": ["phishing"], "status": ["new", "in_progress"]}
            ),
        )
    )
    return view.id


async def test_personal_view_is_invisible_to_other_users(
    session: AsyncSession, actors: Actors, seeded: Seeded
) -> None:
    view_id = await _create(session, actors.owner)
    other = _views(session, actors.editor)

    listed = await other.list_views(PageParams())
    assert view_id not in {view.id for view in listed.items}
    with pytest.raises(CaseViewNotFoundError):
        await other.get_view(view_id)
    with pytest.raises(CaseViewNotFoundError):
        await other.update_view(view_id, CaseViewUpdate(name="Taken over"))
    with pytest.raises(CaseViewNotFoundError):
        await other.delete_view(view_id)
    with pytest.raises(CaseViewNotFoundError):
        await other.pin_view(view_id)
    with pytest.raises(CaseViewNotFoundError):
        await _run_view(session, actors.editor, view_id)
    with pytest.raises(CaseViewNotFoundError):
        await _run_view(session, actors.workflow, view_id)


async def test_view_runs_its_stored_query(
    session: AsyncSession, actors: Actors, seeded: Seeded
) -> None:
    view_id = await _create(session, actors.owner)

    assert await _run_view(session, actors.owner, view_id) == {seeded.phishing_open}


async def test_request_filters_narrow_a_view_and_other_fields_are_rejected(
    session: AsyncSession, actors: Actors, seeded: Seeded
) -> None:
    view_id = await _create(
        session, actors.owner, query={"status": ["new", "in_progress", "closed"]}
    )

    narrowed = await _run_view(
        session,
        actors.owner,
        view_id,
        filters={"field": "status", "op": "eq", "value": "closed"},
    )
    assert narrowed == {seeded.phishing_closed}
    with pytest.raises(TracecatValidationError, match="status"):
        await _run_view(session, actors.owner, view_id, status=["new"])


async def test_sharing_requires_case_update(
    session: AsyncSession, actors: Actors, seeded: Seeded
) -> None:
    with pytest.raises(CaseViewPermissionError):
        await _create(session, actors.reader, visibility=CaseViewVisibility.WORKSPACE)

    personal = await _create(session, actors.reader)
    with pytest.raises(CaseViewPermissionError):
        await _views(session, actors.reader).update_view(
            personal, CaseViewUpdate(visibility=CaseViewVisibility.WORKSPACE)
        )


async def test_shared_view_edit_rights(
    session: AsyncSession, actors: Actors, seeded: Seeded
) -> None:
    view_id = await _create(
        session, actors.owner, visibility=CaseViewVisibility.WORKSPACE
    )

    reader_view = await _views(session, actors.reader).get_view(view_id)
    assert reader_view.can_edit is False
    with pytest.raises(CaseViewPermissionError):
        await _views(session, actors.reader).update_view(
            view_id, CaseViewUpdate(name="Renamed")
        )

    renamed = await _views(session, actors.editor).update_view(
        view_id, CaseViewUpdate(name="Renamed")
    )
    assert renamed.name == "Renamed"
    # Only the owner may hide a shared view by making it personal.
    with pytest.raises(CaseViewPermissionError):
        await _views(session, actors.editor).update_view(
            view_id, CaseViewUpdate(visibility=CaseViewVisibility.PERSONAL)
        )

    workflow_list = await _views(session, actors.workflow).list_views(PageParams())
    assert [view.id for view in workflow_list.items] == [view_id]
    assert await _run_view(session, actors.workflow, view_id) == {seeded.phishing_open}


async def test_builtin_views_are_read_only_and_run(
    session: AsyncSession, actors: Actors, seeded: Seeded
) -> None:
    views = _views(session, actors.owner)

    assert [view.id for view in views.list_builtins()] == [
        "all",
        "assigned_to_me",
        "unassigned",
        "created_by_me",
    ]
    with pytest.raises(TracecatValidationError):
        await views.update_view("unassigned", CaseViewUpdate(name="Mine"))
    with pytest.raises(TracecatValidationError):
        await views.delete_view("unassigned")
    with pytest.raises(TracecatValidationError):
        await views.pin_view("unassigned")

    assert await _run_view(session, actors.owner, "assigned_to_me") == {
        seeded.malware_open
    }
    assert await _run_view(session, actors.owner, "unassigned") == {
        seeded.phishing_open
    }
    assert await _run_view(session, actors.owner, "created_by_me") == {
        seeded.phishing_open,
        seeded.phishing_closed,
        seeded.malware_open,
    }


async def test_pins_are_per_user_and_ordered(
    session: AsyncSession, actors: Actors, seeded: Seeded
) -> None:
    first = await _create(
        session, actors.owner, visibility=CaseViewVisibility.WORKSPACE
    )
    second = await _create(
        session, actors.owner, visibility=CaseViewVisibility.WORKSPACE
    )
    owner_views = _views(session, actors.owner)

    await owner_views.pin_view(second)
    await owner_views.pin_view(first)
    pinned = await owner_views.list_views(PageParams(), pinned_only=True)
    assert [view.id for view in pinned.items] == [second, first]
    assert [view.pin_position for view in pinned.items] == [0, 1]

    editor_pins = await _views(session, actors.editor).list_views(
        PageParams(), pinned_only=True
    )
    assert editor_pins.items == []

    await owner_views.unpin_view(second)
    pinned = await owner_views.list_views(PageParams(), pinned_only=True)
    assert [view.id for view in pinned.items] == [first]


async def test_deleted_tag_is_reported_and_matches_nothing(
    session: AsyncSession, actors: Actors, seeded: Seeded
) -> None:
    view_id = await _create(session, actors.owner)
    await session.execute(delete(CaseTag).where(CaseTag.ref == seeded.tag_ref))
    await session.flush()

    view = await _views(session, actors.owner).get_view(view_id)
    assert [(ref.kind, ref.value) for ref in view.stale_refs] == [("tag", "phishing")]
    # Dropping the only tag would widen the view to every open case.
    assert await _run_view(session, actors.owner, view_id) == set()
    counts = await CasesService(session, actors.owner).query_case_aggregates(
        CaseQuery(view_id=uuid.UUID(view_id))
    )
    assert counts.total == 0


async def test_unreadable_stored_query_is_a_validation_error(
    session: AsyncSession, actors: Actors, seeded: Seeded
) -> None:
    view_id = await _create(session, actors.owner)
    await session.execute(
        update(CaseView)
        .where(CaseView.id == uuid.UUID(view_id))
        .values(query={"no_longer_a_field": True})
    )
    await session.flush()
    session.expunge_all()

    with pytest.raises(TracecatValidationError, match="cannot read"):
        await _run_view(session, actors.owner, view_id)


async def test_stored_query_cannot_reference_another_view() -> None:
    with pytest.raises(ValueError, match="another view"):
        CaseViewCreate(
            name="Nested", query=CaseQuery.model_validate({"view_id": "all"})
        )
