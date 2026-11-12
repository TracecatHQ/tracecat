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
from tracecat.cases.schemas import CaseCreate, CaseFieldCreate, CaseUpdate
from tracecat.cases.service import CaseFieldsService, CasesService
from tracecat.db.models import Case, CaseEvent, Workspace
from tracecat.exceptions import (
    EntitlementRequired,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.pagination import CursorPaginationParams
from tracecat.tables.enums import SqlType

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


async def _set_close_sub_cases_setting(
    session: AsyncSession, workspace_id: uuid.UUID | None, enabled: bool
) -> None:
    await session.execute(
        sa.update(Workspace)
        .where(Workspace.id == workspace_id)
        .values(settings={"close_sub_cases_with_parent": enabled})
    )
    await session.commit()


async def _status(session: AsyncSession, case_id: uuid.UUID) -> CaseStatus | None:
    return await session.scalar(sa.select(Case.status).where(Case.id == case_id))


@pytest.mark.anyio
async def test_closing_parent_closes_open_sub_cases(
    cases_service: CasesService, session: AsyncSession
) -> None:
    parent = await _create(cases_service, "Parent")
    first = await _create(cases_service, "Alert 1", parent.id)
    second = await _create(cases_service, "Alert 2", parent.id)
    resolved = await _create(cases_service, "Alert 3", parent.id)
    other = await _create(cases_service, "Other")
    await cases_service.update_case(resolved, CaseUpdate(status=CaseStatus.RESOLVED))

    await cases_service.update_case(parent, CaseUpdate(status=CaseStatus.CLOSED))

    assert await _status(session, first.id) == CaseStatus.CLOSED
    assert await _status(session, second.id) == CaseStatus.CLOSED
    assert await _status(session, resolved.id) == CaseStatus.RESOLVED
    assert await _status(session, other.id) == CaseStatus.NEW
    for sub_case in (first, second):
        [event] = await _events(session, sub_case.id, CaseEventType.CASE_CLOSED)
        assert event.data["old"] == CaseStatus.NEW
        assert event.data["new"] == CaseStatus.CLOSED
    assert await _events(session, resolved.id, CaseEventType.CASE_CLOSED) == []


@pytest.mark.anyio
async def test_resolving_parent_resolves_sub_cases(
    cases_service: CasesService, session: AsyncSession
) -> None:
    parent = await _create(cases_service, "Parent")
    sub_case = await _create(cases_service, "Alert", parent.id)

    await cases_service.update_case(parent, CaseUpdate(status=CaseStatus.RESOLVED))

    assert await _status(session, sub_case.id) == CaseStatus.RESOLVED
    [event] = await _events(session, sub_case.id, CaseEventType.STATUS_CHANGED)
    assert event.data["new"] == CaseStatus.RESOLVED


@pytest.mark.anyio
async def test_closing_parent_leaves_sub_cases_when_setting_disabled(
    cases_service: CasesService, session: AsyncSession, svc_role: Role
) -> None:
    await _set_close_sub_cases_setting(session, svc_role.workspace_id, False)
    parent = await _create(cases_service, "Parent")
    sub_case = await _create(cases_service, "Alert", parent.id)

    await cases_service.update_case(parent, CaseUpdate(status=CaseStatus.CLOSED))

    assert await _status(session, parent.id) == CaseStatus.CLOSED
    assert await _status(session, sub_case.id) == CaseStatus.NEW


@pytest.mark.anyio
async def test_closing_parent_without_case_addons_leaves_sub_cases(
    cases_service: CasesService, session: AsyncSession
) -> None:
    parent = await _create(cases_service, "Parent")
    sub_case = await _create(cases_service, "Alert", parent.id)

    with patch.object(
        CaseHierarchyService, "has_entitlement", new=AsyncMock(return_value=False)
    ):
        await cases_service.update_case(parent, CaseUpdate(status=CaseStatus.CLOSED))

    assert await _status(session, sub_case.id) == CaseStatus.NEW


@pytest.mark.anyio
async def test_closing_parent_fills_sub_case_resolution_from_parent(
    cases_service: CasesService, session: AsyncSession
) -> None:
    definitions = CaseDropdownDefinitionsService(
        session=session, role=cases_service.role
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
    false_positive = await definitions.add_option(
        definition.id,
        CaseDropdownOptionCreate(label="False positive", ref="fp", position=0),
    )
    true_positive = await definitions.add_option(
        definition.id,
        CaseDropdownOptionCreate(label="True positive", ref="tp", position=1),
    )
    fields = CaseFieldsService(session=session, role=cases_service.role)
    await fields.create_field(
        CaseFieldCreate(name="root_cause", type=SqlType.TEXT, required_on_closure=True)
    )
    parent = await _create(cases_service, "Parent")
    empty = await _create(cases_service, "Alert 1", parent.id)
    preset = await _create(cases_service, "Alert 2", parent.id)
    await cases_service.update_case(
        preset,
        CaseUpdate(
            fields={"root_cause": "Own cause"},
            dropdown_values=[
                CaseDropdownValueInput(
                    definition_id=definition.id, option_id=true_positive.id
                )
            ],
        ),
    )

    await cases_service.update_case(
        parent,
        CaseUpdate(
            status=CaseStatus.CLOSED,
            fields={"root_cause": "Benign scanner"},
            dropdown_values=[
                CaseDropdownValueInput(
                    definition_id=definition.id, option_id=false_positive.id
                )
            ],
        ),
    )

    dropdowns = CaseDropdownValuesService(session=session, role=cases_service.role)
    for sub_case, option_id, cause in (
        (empty, false_positive.id, "Benign scanner"),
        (preset, true_positive.id, "Own cause"),
    ):
        assert await _status(session, sub_case.id) == CaseStatus.CLOSED
        [value] = await dropdowns.list_values_for_case(sub_case.id)
        assert value.option_id == option_id
        assert (await fields.get_fields(sub_case) or {})["root_cause"] == cause

    [dropdown_event] = await _events(
        session, empty.id, CaseEventType.DROPDOWN_VALUE_CHANGED
    )
    assert dropdown_event.data["new_option_label"] == "False positive"
    [fields_event] = await _events(session, empty.id, CaseEventType.FIELDS_CHANGED)
    assert fields_event.data["changes"] == [
        {"field": "root_cause", "old": None, "new": "Benign scanner"}
    ]
    assert (
        len(await _events(session, preset.id, CaseEventType.DROPDOWN_VALUE_CHANGED))
        == 1
    )


@pytest.mark.anyio
async def test_batch_closing_parent_closes_sub_cases(
    cases_service: CasesService, session: AsyncSession
) -> None:
    parent = await _create(cases_service, "Parent")
    sub_case = await _create(cases_service, "Alert", parent.id)

    response = await cases_service.batch_update_cases(
        [parent.id, sub_case.id], CaseUpdate(status=CaseStatus.CLOSED)
    )

    assert response.succeeded == 2
    assert await _status(session, sub_case.id) == CaseStatus.CLOSED
    assert len(await _events(session, sub_case.id, CaseEventType.CASE_CLOSED)) == 1
