"""Saved case views: visibility, edit rights, pins, and running a view."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

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
            created_at=datetime(2026, 1, number, tzinfo=UTC),
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

    assert [view.id for view in views.list_builtins()] == ["assigned_to_me"]
    with pytest.raises(TracecatValidationError):
        await views.update_view("assigned_to_me", CaseViewUpdate(name="Mine"))
    with pytest.raises(TracecatValidationError):
        await views.delete_view("assigned_to_me")
    with pytest.raises(TracecatValidationError):
        await views.pin_view("assigned_to_me")
    # Removed built-in IDs are not views.
    for removed in ("all", "unassigned", "created_by_me"):
        with pytest.raises(CaseViewNotFoundError):
            await views.get_view(removed)

    assert await _run_view(session, actors.owner, "assigned_to_me") == {
        seeded.malware_open
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
            name="Nested", query=CaseQuery.model_validate({"view_id": "assigned_to_me"})
        )


async def test_relevance_order_needs_a_full_text_view(
    session: AsyncSession, actors: Actors, seeded: Seeded
) -> None:
    with pytest.raises(ValueError, match="full_text"):
        CaseViewCreate(name="No search", query=CaseQuery(), order_by="relevance")

    view = await _views(session, actors.owner).create_view(
        CaseViewCreate(
            name="Phishing words",
            query=CaseQuery.model_validate(
                {"search_term": "phishing", "search_mode": "full_text"}
            ),
        )
    )
    # The merged request is re-validated: relevance cannot sort ascending.
    with pytest.raises(ValueError, match="descending"):
        await _run_view(session, actors.owner, view.id, sort="asc")
    with pytest.raises(TracecatValidationError, match="full_text"):
        await _views(session, actors.owner).update_view(
            view.id,
            CaseViewUpdate(
                query=CaseQuery(status=[CaseStatus.NEW]), order_by="relevance"
            ),
        )


async def test_request_sort_sets_direction_for_the_view_order(
    session: AsyncSession, actors: Actors, seeded: Seeded
) -> None:
    """With no order_by, the view's order applies and a request sort still wins."""
    view = await _views(session, actors.owner).create_view(
        CaseViewCreate(
            name="By created",
            query=CaseQuery(),
            order_by="created_at",
            sort="desc",
        )
    )
    request = CaseSearchRequest.model_validate({"view_id": view.id, "sort": "asc"})
    page = await CasesService(session, actors.owner).query_cases(request)
    ids = [case.id for case in page.items]

    assert ids == [seeded.phishing_open, seeded.phishing_closed, seeded.malware_open]


def test_stored_query_rejects_malformed_dropdown_entries() -> None:
    with pytest.raises(ValueError, match="definition_ref:option_ref"):
        CaseViewCreate(
            name="Bad dropdown", query=CaseQuery.model_validate({"dropdown": ["x"]})
        )


async def test_deleted_tag_in_filter_tree_matches_nothing(
    session: AsyncSession, actors: Actors, seeded: Seeded
) -> None:
    view_id = await _create(
        session,
        actors.owner,
        query={
            "filters": {
                "or": [
                    {"field": "tags", "op": "in", "value": ["phishing"]},
                    {"field": "status", "op": "eq", "value": "closed"},
                ]
            }
        },
    )
    await session.execute(delete(CaseTag).where(CaseTag.ref == seeded.tag_ref))
    await session.flush()

    view = await _views(session, actors.owner).get_view(view_id)
    assert [(ref.kind, ref.value) for ref in view.stale_refs] == [("tag", "phishing")]
    # Rewriting the tree could widen it, so the view matches nothing instead.
    assert await _run_view(session, actors.owner, view_id) == set()


async def test_blank_tag_does_not_keep_a_deleted_tag_rule_alive(
    session: AsyncSession, actors: Actors, seeded: Seeded
) -> None:
    view_id = await _create(session, actors.owner, query={"tags": ["phishing", " "]})
    await session.execute(delete(CaseTag).where(CaseTag.ref == seeded.tag_ref))
    await session.flush()

    assert await _run_view(session, actors.owner, view_id) == set()


async def test_pinning_twice_keeps_one_pin(
    session: AsyncSession, actors: Actors, seeded: Seeded
) -> None:
    view_id = await _create(session, actors.owner)
    views = _views(session, actors.owner)

    first = await views.pin_view(view_id)
    again = await views.pin_view(view_id)
    moved = await views.pin_view(view_id, position=5)

    assert first.pin_position == again.pin_position == 0
    assert moved.pin_position == 5
    pinned = await views.list_views(PageParams(), pinned_only=True)
    assert [view.id for view in pinned.items] == [view_id]
