"""Tests for grouping cases under parent cases (sub-cases)."""

import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock, patch

import pytest
import sqlalchemy as sa
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.audit.service import AuditService
from tracecat.auth.types import Role
from tracecat.cases.dropdowns.schemas import (
    CaseDropdownDefinitionCreate,
    CaseDropdownOptionCreate,
    CaseDropdownValueInput,
)
from tracecat.cases.dropdowns.service import (
    CaseDropdownDefinitionsService,
    CaseDropdownValuesService,
)
from tracecat.cases.enums import CaseEventType, CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.hierarchy import CaseHierarchyService
from tracecat.cases.schemas import CaseCreate, CaseUpdate
from tracecat.cases.service import CasesService
from tracecat.db.models import Case, CaseEvent, Workspace
from tracecat.exceptions import (
    EntitlementRequired,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.pagination import CursorPaginationParams

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def stub_case_side_effects() -> Iterator[None]:
    with (
        patch(
            "tracecat.cases.events.sync_case_duration",
            new=AsyncMock(return_value=True),
        ),
        patch(
            "tracecat.cases.events.enqueue_case_duration_sync_after_commit",
            return_value=None,
        ),
        patch.object(AuditService, "create_event", new_callable=AsyncMock),
        patch.object(CasesService, "has_entitlement", new=AsyncMock(return_value=True)),
        patch.object(
            CaseHierarchyService, "has_entitlement", new=AsyncMock(return_value=True)
        ),
    ):
        yield


@pytest.fixture
async def cases_service(session: AsyncSession, svc_role: Role) -> CasesService:
    return CasesService(session=session, role=svc_role)


async def _create(
    service: CasesService, summary: str, parent_id: uuid.UUID | None = None
) -> Case:
    return await service.create_case(
        CaseCreate(
            summary=summary,
            description=f"{summary} description",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
            parent_id=parent_id,
        )
    )


async def _search_ids(service: CasesService, **kwargs) -> set[uuid.UUID]:
    response = await service.search_cases(CursorPaginationParams(limit=100), **kwargs)
    return {item.id for item in response.items}


async def _events(
    session: AsyncSession, case_id: uuid.UUID, event_type: CaseEventType
) -> list[CaseEvent]:
    result = await session.execute(
        sa.select(CaseEvent)
        .where(CaseEvent.case_id == case_id, CaseEvent.type == event_type)
        .order_by(CaseEvent.surrogate_id)
    )
    return list(result.scalars().all())


@pytest.mark.anyio
async def test_set_parent_groups_and_filters_cases(
    cases_service: CasesService,
) -> None:
    parent = await _create(cases_service, "Parent")
    first = await _create(cases_service, "Alert 1")
    second = await _create(cases_service, "Alert 2")
    standalone = await _create(cases_service, "Standalone")

    response = await cases_service.batch_set_parent([first.id, second.id], parent.id)

    assert response.succeeded == 2
    assert response.failed == 0
    assert await _search_ids(cases_service) == {
        parent.id,
        first.id,
        second.id,
        standalone.id,
    }
    assert await _search_ids(cases_service, hierarchy="top_level") == {
        parent.id,
        standalone.id,
    }
    assert await _search_ids(cases_service, parent_id=parent.id) == {
        first.id,
        second.id,
    }
    assert await _search_ids(
        cases_service, parent_id=parent.id, search_term="Alert 2"
    ) == {second.id}

    listed = await cases_service.list_cases(limit=100, hierarchy="top_level")
    items = {item.id: item for item in listed.items}
    assert items[parent.id].num_sub_cases == 2
    assert items[standalone.id].num_sub_cases == 0

    children = await cases_service.search_cases(
        CursorPaginationParams(limit=100), parent_id=parent.id
    )
    for item in children.items:
        assert item.parent is not None
        assert item.parent.id == parent.id
        assert item.parent is not None
        assert item.parent.short_id == parent.short_id
        assert item.parent.summary == "Parent"

    aggregates = await cases_service.get_search_case_aggregates(hierarchy="top_level")
    assert aggregates.total == 2
    child_aggregates = await cases_service.get_search_case_aggregates(
        parent_id=parent.id
    )
    assert child_aggregates.total == 2


@pytest.mark.anyio
async def test_sub_case_pagination_is_stable(cases_service: CasesService) -> None:
    parent = await _create(cases_service, "Parent")
    children = [await _create(cases_service, f"Child {i}") for i in range(5)]
    await cases_service.batch_set_parent([c.id for c in children], parent.id)

    seen: list[uuid.UUID] = []
    cursor: str | None = None
    while True:
        page = await cases_service.search_cases(
            CursorPaginationParams(limit=2, cursor=cursor), parent_id=parent.id
        )
        seen.extend(item.id for item in page.items)
        assert page.total_estimate == 5
        if not page.has_more:
            break
        cursor = page.next_cursor

    assert len(seen) == 5
    assert set(seen) == {c.id for c in children}


@pytest.mark.anyio
async def test_set_parent_validation(cases_service: CasesService) -> None:
    # Failed batches roll back the session, so hold IDs rather than ORM rows.
    parent_id = (await _create(cases_service, "Parent")).id
    child_id = (await _create(cases_service, "Child")).id
    other_parent_id = (await _create(cases_service, "Other parent")).id
    await cases_service.batch_set_parent([child_id], parent_id)
    missing_id = uuid.uuid4()

    # A sub-case cannot become a parent.
    with pytest.raises(TracecatValidationError):
        await cases_service.batch_set_parent([other_parent_id], child_id)

    # Unknown parent.
    with pytest.raises(TracecatNotFoundError):
        await cases_service.batch_set_parent([other_parent_id], uuid.uuid4())

    # Per-item failures: self-parent, missing case, and a case with sub-cases.
    response = await cases_service.batch_set_parent(
        [other_parent_id, missing_id, parent_id], other_parent_id
    )
    results = {result.case_id: result for result in response.results}
    assert response.succeeded == 0
    assert results[other_parent_id].error == "A case cannot be its own parent"
    assert results[missing_id].error == "Case not found"
    error = results[parent_id].error
    assert error is not None and "has sub-cases" in error

    refreshed = await cases_service.get_case(parent_id)
    assert refreshed is not None
    assert refreshed.parent_id is None


@pytest.mark.anyio
async def test_set_parent_moves_case_and_records_events(
    cases_service: CasesService, session: AsyncSession
) -> None:
    first_parent = await _create(cases_service, "First parent")
    second_parent = await _create(cases_service, "Second parent")
    child = await _create(cases_service, "Child")

    await cases_service.batch_set_parent([child.id], first_parent.id)
    # Re-applying the same parent is a successful no-op without new events.
    noop = await cases_service.batch_set_parent([child.id], first_parent.id)
    assert noop.succeeded == 1
    assert len(await _events(session, child.id, CaseEventType.PARENT_CHANGED)) == 1

    response = await cases_service.batch_set_parent([child.id], second_parent.id)
    assert response.succeeded == 1

    moved = await cases_service.get_case(child.id)
    assert moved is not None
    assert moved.parent_id == second_parent.id

    child_events = await _events(session, child.id, CaseEventType.PARENT_CHANGED)
    assert [event.data["new"]["id"] for event in child_events] == [
        str(first_parent.id),
        str(second_parent.id),
    ]
    assert child_events[1].data["old"]["short_id"] == first_parent.short_id

    removed = await _events(session, first_parent.id, CaseEventType.SUB_CASES_REMOVED)
    assert len(removed) == 1
    assert removed[0].data["sub_cases"] == [
        {"id": str(child.id), "short_id": child.short_id}
    ]
    added = await _events(session, second_parent.id, CaseEventType.SUB_CASES_ADDED)
    assert len(added) == 1


@pytest.mark.anyio
async def test_batch_records_one_parent_event(
    cases_service: CasesService, session: AsyncSession
) -> None:
    parent = await _create(cases_service, "Parent")
    children = [await _create(cases_service, f"Child {i}") for i in range(3)]

    await cases_service.batch_set_parent([c.id for c in children], parent.id)

    added = await _events(session, parent.id, CaseEventType.SUB_CASES_ADDED)
    assert len(added) == 1
    assert len(added[0].data["sub_cases"]) == 3


@pytest.mark.anyio
async def test_clear_parent(cases_service: CasesService, session: AsyncSession) -> None:
    parent = await _create(cases_service, "Parent")
    child = await _create(cases_service, "Child")
    top_level = await _create(cases_service, "Top level")
    await cases_service.batch_set_parent([child.id], parent.id)

    response = await cases_service.batch_clear_parent([child.id, top_level.id])

    assert response.succeeded == 2
    assert await _search_ids(cases_service, parent_id=parent.id) == set()
    assert child.id in await _search_ids(cases_service, hierarchy="top_level")
    child_events = await _events(session, child.id, CaseEventType.PARENT_CHANGED)
    assert child_events[-1].data["new"] is None
    assert len(await _events(session, top_level.id, CaseEventType.PARENT_CHANGED)) == 0
    assert len(await _events(session, parent.id, CaseEventType.SUB_CASES_REMOVED)) == 1


@pytest.mark.anyio
async def test_create_case_with_parent(
    cases_service: CasesService, session: AsyncSession
) -> None:
    parent = await _create(cases_service, "Parent")
    child = await _create(cases_service, "Child", parent_id=parent.id)
    child_id = child.id

    assert child.parent_id == parent.id
    added = await _events(session, parent.id, CaseEventType.SUB_CASES_ADDED)
    assert added[0].data["sub_cases"][0]["short_id"] == child.short_id
    parent_changed = await _events(session, child_id, CaseEventType.PARENT_CHANGED)
    assert len(parent_changed) == 1
    assert parent_changed[0].data["old"] is None
    assert parent_changed[0].data["new"]["id"] == str(parent.id)

    with pytest.raises(TracecatValidationError):
        await _create(cases_service, "Grandchild", parent_id=child_id)
    with pytest.raises(TracecatNotFoundError):
        await _create(cases_service, "Orphan", parent_id=uuid.uuid4())


@pytest.mark.anyio
async def test_deleting_parent_unlinks_sub_cases(
    cases_service: CasesService,
) -> None:
    parent_id = (await _create(cases_service, "Parent")).id
    child_id = (await _create(cases_service, "Child")).id
    await cases_service.batch_set_parent([child_id], parent_id)

    await cases_service.batch_delete_cases([parent_id])

    cases_service.session.expire_all()
    remaining = await cases_service.get_case(child_id)
    assert remaining is not None
    assert remaining.parent_id is None
    assert child_id in await _search_ids(cases_service, hierarchy="top_level")


@pytest.mark.anyio
async def test_parent_cannot_reference_itself(
    cases_service: CasesService, session: AsyncSession
) -> None:
    case = await _create(cases_service, "Self")
    with pytest.raises(IntegrityError):
        async with session.begin_nested():
            await session.execute(
                sa.update(Case).where(Case.id == case.id).values(parent_id=case.id)
            )


@pytest.mark.anyio
async def test_cross_workspace_parent_is_not_found(
    cases_service: CasesService,
    session: AsyncSession,
    svc_role: Role,
) -> None:
    parent_id = (await _create(cases_service, "Parent")).id
    child_id = (await _create(cases_service, "Child")).id
    other_role = svc_role.model_copy(update={"workspace_id": uuid.uuid4()})
    other_service = CasesService(session=session, role=other_role)

    with pytest.raises(TracecatNotFoundError):
        await other_service.batch_set_parent([child_id], parent_id)
    refreshed = await cases_service.get_case(child_id)
    assert refreshed is not None
    assert refreshed.parent_id is None


@pytest.mark.anyio
async def test_sub_cases_require_case_addons(cases_service: CasesService) -> None:
    parent_id = (await _create(cases_service, "Parent")).id
    child_id = (await _create(cases_service, "Child")).id
    await cases_service.batch_set_parent([child_id], parent_id)

    with (
        patch.object(
            CasesService, "has_entitlement", new=AsyncMock(return_value=False)
        ),
        patch.object(
            CaseHierarchyService, "has_entitlement", new=AsyncMock(return_value=False)
        ),
    ):
        with pytest.raises(EntitlementRequired):
            await cases_service.batch_set_parent([child_id], parent_id)
        with pytest.raises(EntitlementRequired):
            await cases_service.batch_clear_parent([child_id])
        with pytest.raises(EntitlementRequired):
            await _create(cases_service, "New child", parent_id=parent_id)
        with pytest.raises(EntitlementRequired):
            await _search_ids(cases_service, parent_id=parent_id)
        with pytest.raises(EntitlementRequired):
            await cases_service.get_search_case_aggregates(parent_id=parent_id)

        # Without the entitlement the list is flat: nothing is hidden and no
        # hierarchy metadata is returned.
        response = await cases_service.search_cases(
            CursorPaginationParams(limit=100), hierarchy="top_level"
        )
        items = {item.id: item for item in response.items}
        assert {parent_id, child_id} <= items.keys()
        assert items[child_id].parent is None
        assert items[child_id].parent is None
        assert items[parent_id].num_sub_cases == 0
        aggregate = await cases_service.get_search_case_aggregates(
            hierarchy="top_level"
        )
        assert aggregate.total == len(items)

    refreshed = await cases_service.get_case(child_id)
    assert refreshed is not None
    assert refreshed.parent_id == parent_id


async def _required_resolution_dropdown(service: CasesService) -> None:
    definitions = CaseDropdownDefinitionsService(
        session=service.session, role=service.role
    )
    definition = await definitions.create_definition(
        CaseDropdownDefinitionCreate(
            name="Resolution",
            ref="resolution",
            is_ordered=False,
            options=[],
            required_on_closure=True,
        )
    )
    await definitions.add_option(
        definition.id,
        CaseDropdownOptionCreate(label="Duplicate", ref="duplicate", position=0),
    )


async def _enable_close_sub_cases(session: AsyncSession, service: CasesService) -> None:
    await session.execute(
        sa.update(Workspace)
        .where(Workspace.id == service.workspace_id)
        .values(settings={"close_sub_cases_with_parent": True})
    )
    await session.commit()


def _resolve_as_duplicate() -> list[CaseDropdownValueInput]:
    return [CaseDropdownValueInput(definition_ref="resolution", option_ref="duplicate")]


async def _resolution(
    session: AsyncSession, role: Role, case_id: uuid.UUID
) -> str | None:
    values = CaseDropdownValuesService(session=session, role=role)
    return next(
        (v.option_ref for v in await values.list_values_for_case(case_id)), None
    )


@pytest.mark.anyio
async def test_closing_parent_leaves_sub_cases_open_by_default(
    cases_service: CasesService, session: AsyncSession
) -> None:
    parent = await _create(cases_service, "Parent")
    child_id = (await _create(cases_service, "Child", parent_id=parent.id)).id

    await cases_service.update_case(parent, CaseUpdate(status=CaseStatus.CLOSED))

    session.expire_all()
    child = await cases_service.get_case(child_id)
    assert child is not None and child.status == CaseStatus.NEW


@pytest.mark.anyio
async def test_closing_parent_closes_open_sub_cases_when_enabled(
    cases_service: CasesService, session: AsyncSession
) -> None:
    await _required_resolution_dropdown(cases_service)
    await _enable_close_sub_cases(session, cases_service)
    parent = await _create(cases_service, "Parent")
    open_ids = [
        (await _create(cases_service, f"Child {i}", parent_id=parent.id)).id
        for i in range(2)
    ]
    resolved = await _create(cases_service, "Resolved child", parent_id=parent.id)
    resolved_id = resolved.id
    await cases_service.update_case(
        resolved,
        CaseUpdate(status=CaseStatus.RESOLVED, dropdown_values=_resolve_as_duplicate()),
    )
    other_id = (await _create(cases_service, "Other")).id
    parent_id = parent.id

    await cases_service.update_case(
        parent,
        CaseUpdate(
            status=CaseStatus.CLOSED,
            priority=CasePriority.HIGH,
            dropdown_values=_resolve_as_duplicate(),
        ),
    )

    session.expire_all()
    role = cases_service.role
    for case_id in (parent_id, *open_ids):
        case = await cases_service.get_case(case_id)
        assert case is not None and case.status == CaseStatus.CLOSED
        assert await _resolution(session, role, case_id) == "duplicate"
        assert len(await _events(session, case_id, CaseEventType.CASE_CLOSED)) == 1
    # Parent-only changes stay on the parent.
    child = await cases_service.get_case(open_ids[0])
    assert child is not None and child.priority == CasePriority.MEDIUM
    # Sub-cases that are already done keep their status.
    done = await cases_service.get_case(resolved_id)
    assert done is not None and done.status == CaseStatus.RESOLVED
    assert not await _events(session, resolved_id, CaseEventType.CASE_CLOSED)
    unrelated = await cases_service.get_case(other_id)
    assert unrelated is not None and unrelated.status == CaseStatus.NEW


@pytest.mark.anyio
async def test_closing_parent_copies_resolution_set_earlier(
    cases_service: CasesService, session: AsyncSession
) -> None:
    await _required_resolution_dropdown(cases_service)
    await _enable_close_sub_cases(session, cases_service)
    parent = await _create(cases_service, "Parent")
    child_id = (await _create(cases_service, "Child", parent_id=parent.id)).id
    await cases_service.update_case(
        parent, CaseUpdate(dropdown_values=_resolve_as_duplicate())
    )

    await cases_service.update_case(parent, CaseUpdate(status=CaseStatus.RESOLVED))

    session.expire_all()
    child = await cases_service.get_case(child_id)
    assert child is not None and child.status == CaseStatus.RESOLVED
    assert await _resolution(session, cases_service.role, child_id) == "duplicate"


@pytest.mark.anyio
async def test_closing_parent_is_all_or_nothing(
    cases_service: CasesService, session: AsyncSession
) -> None:
    await _enable_close_sub_cases(session, cases_service)
    parent = await _create(cases_service, "Parent")
    parent_id = parent.id
    child = await _create(cases_service, "Child", parent_id=parent_id)
    child_id, child_short_id = child.id, child.short_id
    original = CasesService._validate_closure_requirements

    async def fail_for_child(self, case, *args, **kwargs):
        if case.id == child_id:
            raise TracecatValidationError("closure_requirements_not_met")
        return await original(self, case, *args, **kwargs)

    with patch.object(CasesService, "_validate_closure_requirements", fail_for_child):
        with pytest.raises(TracecatValidationError, match=child_short_id):
            await cases_service.update_case(
                parent, CaseUpdate(status=CaseStatus.CLOSED)
            )

    session.expire_all()
    for case_id in (parent_id, child_id):
        case = await cases_service.get_case(case_id)
        assert case is not None and case.status == CaseStatus.NEW


@pytest.mark.anyio
async def test_batch_closing_parent_closes_sub_cases_when_enabled(
    cases_service: CasesService, session: AsyncSession
) -> None:
    await _enable_close_sub_cases(session, cases_service)
    parent_id = (await _create(cases_service, "Parent")).id
    child_id = (await _create(cases_service, "Child", parent_id=parent_id)).id

    response = await cases_service.batch_update_cases(
        [parent_id], CaseUpdate(status=CaseStatus.CLOSED)
    )

    assert response.succeeded == 1
    session.expire_all()
    child = await cases_service.get_case(child_id)
    assert child is not None and child.status == CaseStatus.CLOSED


@pytest.mark.anyio
async def test_closing_parent_skips_sub_cases_without_case_addons(
    cases_service: CasesService, session: AsyncSession
) -> None:
    await _enable_close_sub_cases(session, cases_service)
    parent = await _create(cases_service, "Parent")
    child_id = (await _create(cases_service, "Child", parent_id=parent.id)).id

    with patch.object(
        CasesService, "has_entitlement", new=AsyncMock(return_value=False)
    ):
        await cases_service.update_case(parent, CaseUpdate(status=CaseStatus.CLOSED))

    session.expire_all()
    child = await cases_service.get_case(child_id)
    assert child is not None and child.status == CaseStatus.NEW


@pytest.mark.anyio
async def test_parent_filter_rejects_top_level_hierarchy(
    cases_service: CasesService,
) -> None:
    parent_id = (await _create(cases_service, "Parent")).id
    # The shared request rejects conflicting constraints before query execution.
    with pytest.raises(ValidationError, match="cannot be combined"):
        await cases_service.search_cases(
            CursorPaginationParams(limit=10),
            parent_id=parent_id,
            hierarchy="top_level",
        )


@pytest.mark.anyio
async def test_set_parent_with_short_ids(cases_service: CasesService) -> None:
    parent = await _create(cases_service, "Parent")
    first = await _create(cases_service, "First duplicate")
    second = await _create(cases_service, "Second duplicate")

    case_ids = await cases_service.resolve_case_ids(
        [first.short_id, str(second.id), first.short_id.lower()]
    )
    parent_id = await cases_service.resolve_case_id(parent.short_id)
    response = await cases_service.batch_set_parent(case_ids[:2], parent_id)

    assert case_ids == [first.id, second.id, first.id]
    assert response.failed == 0
    assert await _search_ids(cases_service, parent_id=parent.id) == {
        first.id,
        second.id,
    }


@pytest.mark.anyio
async def test_resolve_case_ids_errors(cases_service: CasesService) -> None:
    case = await _create(cases_service, "Known")

    with pytest.raises(TracecatNotFoundError, match="CASE-9999"):
        await cases_service.resolve_case_ids([case.short_id, "CASE-9999"])
    # Above the integer column's range: not found rather than a bind error.
    with pytest.raises(TracecatNotFoundError, match="CASE-9999999999"):
        await cases_service.resolve_case_id("CASE-9999999999")
    with pytest.raises(TracecatValidationError, match="not-a-case"):
        await cases_service.resolve_case_ids([case.short_id, "not-a-case"])
