import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.cases.durations import (
    CaseDurationAnchorSelection,
    CaseDurationComputation,
    CaseDurationDefinitionCreate,
    CaseDurationDefinitionUpdate,
    CaseDurationEventAnchor,
    CaseDurationEventFilters,
)
from tracecat.cases.durations.service import (
    CaseDurationDefinitionService,
    CaseDurationService,
)
from tracecat.cases.enums import CaseEventType, CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.schemas import CaseCreate, CaseUpdate
from tracecat.cases.service import CasesService
from tracecat.cases.tags.service import CaseTagsService
from tracecat.db.models import CaseDuration, CaseEvent
from tracecat.db.models import CaseDurationDefinition as CaseDurationDefinitionDB
from tracecat.tags.schemas import TagCreate

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def stub_case_duration_entitlements() -> Iterator[None]:
    with (
        patch.object(
            CaseDurationDefinitionService,
            "has_entitlement",
            new=AsyncMock(return_value=True),
        ),
        patch.object(
            CaseDurationService,
            "has_entitlement",
            new=AsyncMock(return_value=True),
        ),
        patch.object(
            CasesService,
            "has_entitlement",
            new=AsyncMock(return_value=False),
        ),
    ):
        yield


def make_case_create(
    *,
    summary: str = "Investigate suspicious login",
    description: str = "Track the suspicious user activity.",
    status: CaseStatus = CaseStatus.NEW,
    priority: CasePriority = CasePriority.MEDIUM,
    severity: CaseSeverity = CaseSeverity.MEDIUM,
) -> CaseCreate:
    """Build a CaseCreate payload while keeping type checkers happy."""
    return CaseCreate.model_validate(
        {
            "summary": summary,
            "description": description,
            "status": status,
            "priority": priority,
            "severity": severity,
        }
    )


@pytest.mark.anyio
async def test_compute_case_durations_from_events(
    session: AsyncSession, svc_role
) -> None:
    cases_service = CasesService(session=session, role=svc_role)
    definition_service = CaseDurationDefinitionService(session=session, role=svc_role)
    duration_service = CaseDurationService(session=session, role=svc_role)

    metric = await definition_service.create_definition(
        CaseDurationDefinitionCreate(
            name="Time to Resolve",
            description="Elapsed time from creation to resolution",
            start_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.CASE_CREATED,
            ),
            end_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.STATUS_CHANGED,
                filters=CaseDurationEventFilters(
                    new_values=[CaseStatus.RESOLVED.value]
                ),
            ),
        )
    )

    case = await cases_service.create_case(make_case_create())

    assert not isinstance(duration_service, CaseDurationDefinitionService)
    values = await duration_service.compute_duration(case)
    assert len(values) == 1
    value = values[0]
    assert value.duration_id == metric.id
    assert value.start_event_id is not None
    assert value.started_at is not None
    assert value.end_event_id is None
    assert value.duration is None

    initial_stmt = select(CaseDuration).where(CaseDuration.case_id == case.id)
    initial_duration = await session.execute(initial_stmt)
    initial_record = initial_duration.scalar_one()
    assert initial_record.definition_id == metric.id
    assert initial_record.start_event_id == value.start_event_id
    assert initial_record.end_event_id is None

    updated_case = await cases_service.update_case(
        case,
        CaseUpdate(status=CaseStatus.RESOLVED),
    )
    assert updated_case.status == CaseStatus.RESOLVED

    values = await duration_service.compute_duration(updated_case)
    assert len(values) == 1
    value = values[0]
    assert value.end_event_id is not None
    assert value.ended_at is not None
    assert value.duration is not None
    assert value.duration.total_seconds() >= 0

    duration_stmt = select(CaseDuration).where(CaseDuration.case_id == case.id)
    stored_duration = await session.execute(duration_stmt)
    record = stored_duration.scalar_one()
    assert record.definition_id == metric.id
    assert record.start_event_id is not None
    assert record.end_event_id == value.end_event_id
    assert record.duration is not None


@pytest.mark.anyio
async def test_duration_filters_match_event_payload(
    session: AsyncSession, svc_role
) -> None:
    cases_service = CasesService(session=session, role=svc_role)
    definition_service = CaseDurationDefinitionService(session=session, role=svc_role)
    duration_service = CaseDurationService(session=session, role=svc_role)

    await definition_service.create_definition(
        CaseDurationDefinitionCreate(
            name="Time to Close",
            start_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.CASE_CREATED,
            ),
            end_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.CASE_CLOSED,
            ),
        )
    )

    case = await cases_service.create_case(make_case_create())

    # Transition to resolved (should not match the CLOSED filter)
    await cases_service.update_case(case, CaseUpdate(status=CaseStatus.RESOLVED))

    assert not isinstance(duration_service, CaseDurationDefinitionService)
    values = await duration_service.compute_duration(case)
    assert len(values) == 1
    value = values[0]
    assert value.end_event_id is None
    assert value.duration is None

    # Now transition to closed which should satisfy the filter
    await cases_service.update_case(case, CaseUpdate(status=CaseStatus.CLOSED))

    values = await duration_service.compute_duration(case)
    value = values[0]
    assert value.end_event_id is not None
    assert value.duration is not None


@pytest.mark.anyio
async def test_duration_filters_support_multiple_values(
    session: AsyncSession, svc_role
) -> None:
    cases_service = CasesService(session=session, role=svc_role)
    definition_service = CaseDurationDefinitionService(session=session, role=svc_role)
    duration_service = CaseDurationService(session=session, role=svc_role)

    await definition_service.create_definition(
        CaseDurationDefinitionCreate(
            name="Time to resolved or closed",
            start_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.CASE_CREATED,
            ),
            end_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.STATUS_CHANGED,
                filters=CaseDurationEventFilters(
                    new_values=[CaseStatus.RESOLVED.value, CaseStatus.CLOSED.value]
                ),
            ),
        )
    )

    case = await cases_service.create_case(make_case_create())

    assert not isinstance(duration_service, CaseDurationDefinitionService)
    values = await duration_service.compute_duration(case)
    assert len(values) == 1
    initial_value = values[0]
    assert initial_value.end_event_id is None

    case = await cases_service.update_case(
        case,
        CaseUpdate(status=CaseStatus.RESOLVED),
    )

    values = await duration_service.compute_duration(case)
    assert len(values) == 1
    updated_value = values[0]
    assert updated_value.end_event_id is not None
    assert updated_value.duration is not None


@pytest.mark.anyio
async def test_duration_filters_match_custom_field_changes(
    session: AsyncSession, svc_role
) -> None:
    cases_service = CasesService(session=session, role=svc_role)
    definition_service = CaseDurationDefinitionService(session=session, role=svc_role)
    duration_service = CaseDurationService(session=session, role=svc_role)

    await definition_service.create_definition(
        CaseDurationDefinitionCreate(
            name="Time to severity score update",
            start_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.CASE_CREATED,
            ),
            end_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.FIELDS_CHANGED,
                filters=CaseDurationEventFilters(field_ids=["severity_score"]),
            ),
        )
    )

    case = await cases_service.create_case(make_case_create())
    unrelated_event = CaseEvent(
        workspace_id=case.workspace_id,
        case_id=case.id,
        type=CaseEventType.FIELDS_CHANGED,
        data={"changes": [{"field": "team", "old": "alpha", "new": "beta"}]},
        created_at=datetime.now(UTC) + timedelta(seconds=1),
    )
    matching_event = CaseEvent(
        workspace_id=case.workspace_id,
        case_id=case.id,
        type=CaseEventType.FIELDS_CHANGED,
        data={
            "changes": [
                {"field": "severity_score", "old": 3, "new": 8},
                {"field": "team", "old": "alpha", "new": "beta"},
            ]
        },
        created_at=datetime.now(UTC) + timedelta(seconds=2),
    )
    session.add_all([unrelated_event, matching_event])
    await session.flush()

    values = await duration_service.compute_duration(case)
    assert len(values) == 1
    assert values[0].end_event_id == matching_event.id
    assert values[0].duration is not None


@pytest.mark.anyio
async def test_duration_filters_match_closed_under_status_changed(
    session: AsyncSession, svc_role
) -> None:
    cases_service = CasesService(session=session, role=svc_role)
    definition_service = CaseDurationDefinitionService(session=session, role=svc_role)
    duration_service = CaseDurationService(session=session, role=svc_role)

    await definition_service.create_definition(
        CaseDurationDefinitionCreate(
            name="Time to closed status",
            start_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.CASE_CREATED,
            ),
            end_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.STATUS_CHANGED,
                filters=CaseDurationEventFilters(new_values=[CaseStatus.CLOSED.value]),
            ),
        )
    )

    case = await cases_service.create_case(make_case_create())

    values = await duration_service.compute_duration(case)
    assert len(values) == 1
    assert values[0].end_event_id is None

    case = await cases_service.update_case(case, CaseUpdate(status=CaseStatus.CLOSED))

    values = await duration_service.compute_duration(case)
    assert len(values) == 1
    value = values[0]
    assert value.end_event_id is not None
    assert value.ended_at is not None
    assert value.duration is not None


@pytest.mark.anyio
async def test_duration_filters_match_reopened_under_status_changed(
    session: AsyncSession, svc_role
) -> None:
    cases_service = CasesService(session=session, role=svc_role)
    definition_service = CaseDurationDefinitionService(session=session, role=svc_role)
    duration_service = CaseDurationService(session=session, role=svc_role)

    await definition_service.create_definition(
        CaseDurationDefinitionCreate(
            name="Time to resume work",
            start_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.CASE_CLOSED,
            ),
            end_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.STATUS_CHANGED,
                filters=CaseDurationEventFilters(
                    new_values=[CaseStatus.IN_PROGRESS.value]
                ),
            ),
        )
    )

    case = await cases_service.create_case(make_case_create())
    case = await cases_service.update_case(case, CaseUpdate(status=CaseStatus.CLOSED))

    values = await duration_service.compute_duration(case)
    assert len(values) == 1
    assert values[0].start_event_id is not None
    assert values[0].end_event_id is None

    case = await cases_service.update_case(
        case,
        CaseUpdate(status=CaseStatus.IN_PROGRESS),
    )

    values = await duration_service.compute_duration(case)
    assert len(values) == 1
    value = values[0]
    assert value.start_event_id is not None
    assert value.end_event_id is not None
    assert value.duration is not None


@pytest.mark.anyio
async def test_duration_supports_tag_events(session: AsyncSession, svc_role) -> None:
    cases_service = CasesService(session=session, role=svc_role)
    tags_service = CaseTagsService(session=session, role=svc_role)
    definition_service = CaseDurationDefinitionService(session=session, role=svc_role)
    duration_service = CaseDurationService(session=session, role=svc_role)

    tag = await tags_service.create_tag(TagCreate(name="Urgent", color="#ff0000"))

    metric = await definition_service.create_definition(
        CaseDurationDefinitionCreate(
            name="Tag window",
            start_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.TAG_ADDED,
                filters=CaseDurationEventFilters(tag_refs=[tag.ref]),
            ),
            end_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.TAG_REMOVED,
                filters=CaseDurationEventFilters(tag_refs=[tag.ref]),
            ),
        )
    )

    case = await cases_service.create_case(
        make_case_create(
            summary="Investigate missing data",
            description="Ensure data completeness",
        )
    )

    assert not isinstance(duration_service, CaseDurationDefinitionService)
    values = await duration_service.compute_duration(case.id)
    assert len(values) == 1
    assert values[0].start_event_id is None
    assert values[0].end_event_id is None

    await tags_service.add_case_tag(case.id, str(tag.id))

    values = await duration_service.compute_duration(case.id)
    tag_duration = values[0]
    assert tag_duration.duration_id == metric.id
    assert tag_duration.start_event_id is not None
    assert tag_duration.end_event_id is None

    await tags_service.remove_case_tag(case.id, str(tag.id))

    values = await duration_service.compute_duration(case.id)
    tag_duration = values[0]
    assert tag_duration.start_event_id is not None
    assert tag_duration.end_event_id is not None
    assert tag_duration.duration is not None
    assert tag_duration.duration.total_seconds() >= 0


@pytest.mark.anyio
async def test_duration_definition_update_accepts_nested_anchor_models(
    session: AsyncSession, svc_role
) -> None:
    definition_service = CaseDurationDefinitionService(session=session, role=svc_role)

    definition = await definition_service.create_definition(
        CaseDurationDefinitionCreate(
            name="Time to review",
            start_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.CASE_CREATED,
            ),
            end_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.STATUS_CHANGED,
                filters=CaseDurationEventFilters(
                    new_values=[CaseStatus.RESOLVED.value]
                ),
            ),
        )
    )

    update_payload = CaseDurationDefinitionUpdate(
        start_anchor=CaseDurationEventAnchor(
            event_type=CaseEventType.STATUS_CHANGED,
            selection=CaseDurationAnchorSelection.LAST,
            filters=CaseDurationEventFilters(new_values=[CaseStatus.IN_PROGRESS.value]),
        ),
        end_anchor=CaseDurationEventAnchor(
            event_type=CaseEventType.STATUS_CHANGED,
            selection=CaseDurationAnchorSelection.FIRST,
            filters=CaseDurationEventFilters(new_values=[CaseStatus.RESOLVED.value]),
        ),
    )

    updated = await definition_service.update_definition(definition.id, update_payload)

    assert updated.start_anchor.event_type == CaseEventType.STATUS_CHANGED
    assert updated.start_anchor.selection == CaseDurationAnchorSelection.LAST
    assert updated.start_anchor.filters == CaseDurationEventFilters(
        new_values=[CaseStatus.IN_PROGRESS.value]
    )
    assert updated.end_anchor.selection == CaseDurationAnchorSelection.FIRST
    assert updated.end_anchor.filters == CaseDurationEventFilters(
        new_values=[CaseStatus.RESOLVED.value]
    )

    persisted = await definition_service.get_definition(definition.id)
    assert persisted.start_anchor.event_type == CaseEventType.STATUS_CHANGED
    assert persisted.start_anchor.selection == CaseDurationAnchorSelection.LAST
    assert persisted.start_anchor.filters == CaseDurationEventFilters(
        new_values=[CaseStatus.IN_PROGRESS.value]
    )


@pytest.mark.anyio
async def test_duration_anchor_selection_first_vs_last(
    session: AsyncSession, svc_role
) -> None:
    cases_service = CasesService(session=session, role=svc_role)
    definition_service = CaseDurationDefinitionService(session=session, role=svc_role)
    duration_service = CaseDurationService(session=session, role=svc_role)

    await definition_service.create_definition(
        CaseDurationDefinitionCreate(
            name="Time to first resolution",
            start_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.CASE_CREATED,
            ),
            end_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.STATUS_CHANGED,
                filters=CaseDurationEventFilters(
                    new_values=[CaseStatus.RESOLVED.value]
                ),
                selection=CaseDurationAnchorSelection.FIRST,
            ),
        )
    )
    await definition_service.create_definition(
        CaseDurationDefinitionCreate(
            name="Time to last resolution",
            start_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.CASE_CREATED,
            ),
            end_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.STATUS_CHANGED,
                filters=CaseDurationEventFilters(
                    new_values=[CaseStatus.RESOLVED.value]
                ),
                selection=CaseDurationAnchorSelection.LAST,
            ),
        )
    )

    case = await cases_service.create_case(make_case_create())

    case = await cases_service.update_case(
        case,
        CaseUpdate(status=CaseStatus.IN_PROGRESS),
    )
    case = await cases_service.update_case(
        case,
        CaseUpdate(status=CaseStatus.RESOLVED),
    )
    case = await cases_service.update_case(
        case,
        CaseUpdate(status=CaseStatus.IN_PROGRESS),
    )
    case = await cases_service.update_case(
        case,
        CaseUpdate(status=CaseStatus.RESOLVED),
    )

    assert not isinstance(duration_service, CaseDurationDefinitionService)
    values = await duration_service.compute_duration(case)
    assert len(values) == 2

    first_metric = next(v for v in values if v.name == "Time to first resolution")
    last_metric = next(v for v in values if v.name == "Time to last resolution")

    assert first_metric.end_event_id is not None
    assert last_metric.end_event_id is not None
    assert first_metric.end_event_id != last_metric.end_event_id
    assert first_metric.duration is not None
    assert last_metric.duration is not None
    assert last_metric.duration >= first_metric.duration


@pytest.mark.anyio
async def test_duration_handles_reopen_cycles_without_negative_time(
    session: AsyncSession, svc_role
) -> None:
    cases_service = CasesService(session=session, role=svc_role)
    definition_service = CaseDurationDefinitionService(session=session, role=svc_role)
    duration_service = CaseDurationService(session=session, role=svc_role)

    await definition_service.create_definition(
        CaseDurationDefinitionCreate(
            name="Time to reopen",
            start_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.CASE_CLOSED,
                selection=CaseDurationAnchorSelection.LAST,
            ),
            end_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.CASE_REOPENED,
                selection=CaseDurationAnchorSelection.FIRST,
            ),
        )
    )

    case = await cases_service.create_case(make_case_create())

    case = await cases_service.update_case(
        case,
        CaseUpdate(status=CaseStatus.CLOSED),
    )
    case = await cases_service.update_case(
        case,
        CaseUpdate(status=CaseStatus.IN_PROGRESS),
    )
    case = await cases_service.update_case(
        case,
        CaseUpdate(status=CaseStatus.CLOSED),
    )
    case = await cases_service.update_case(
        case,
        CaseUpdate(status=CaseStatus.IN_PROGRESS),
    )

    assert not isinstance(duration_service, CaseDurationDefinitionService)
    values = await duration_service.compute_duration(case)
    assert len(values) == 1
    metric = values[0]

    assert metric.start_event_id is not None
    assert metric.end_event_id is not None
    assert metric.started_at is not None
    assert metric.ended_at is not None
    assert metric.duration is not None
    assert metric.duration.total_seconds() >= 0


@pytest.mark.anyio
async def test_compute_time_series_returns_metrics(
    session: AsyncSession, svc_role
) -> None:
    """Test that compute_time_series returns OTEL-aligned metrics for analytics."""
    cases_service = CasesService(session=session, role=svc_role)
    definition_service = CaseDurationDefinitionService(session=session, role=svc_role)
    duration_service = CaseDurationService(session=session, role=svc_role)

    await definition_service.create_definition(
        CaseDurationDefinitionCreate(
            name="Time to Resolve",
            description="Elapsed time from creation to resolution",
            start_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.CASE_CREATED,
            ),
            end_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.STATUS_CHANGED,
                filters=CaseDurationEventFilters(
                    new_values=[CaseStatus.RESOLVED.value]
                ),
            ),
        )
    )

    case = await cases_service.create_case(
        make_case_create(
            summary="Test case for metrics",
            description="Testing compute_time_series output",
            priority=CasePriority.HIGH,
            severity=CaseSeverity.CRITICAL,
        )
    )

    await cases_service.update_case(case, CaseUpdate(status=CaseStatus.RESOLVED))

    assert not isinstance(duration_service, CaseDurationDefinitionService)
    metrics = await duration_service.compute_time_series([case])

    assert len(metrics) == 1
    metric = metrics[0]

    # Verify metric identity
    assert metric.metric_name == "case_duration_seconds"
    assert metric.duration_name == "Time to Resolve"
    assert metric.duration_slug == "time_to_resolve"  # slugified with underscore

    # Verify case identifiers (for drill-down)
    assert metric.case_id == str(case.id)
    assert metric.case_short_id == case.short_id

    # Verify case dimensions (for groupby)
    assert metric.case_status == CaseStatus.RESOLVED.value
    assert metric.case_priority == CasePriority.HIGH.value
    assert metric.case_severity == CaseSeverity.CRITICAL.value

    # Verify metric value and timestamp
    assert metric.timestamp is not None
    assert metric.value is not None
    assert metric.value >= 0


@pytest.mark.anyio
async def test_compute_time_series_formats_duration_slug(
    session: AsyncSession, svc_role
) -> None:
    """Ensure duration_slug is properly slugified with underscore separator."""
    cases_service = CasesService(session=session, role=svc_role)
    duration_service = CaseDurationService(session=session, role=svc_role)

    case = await cases_service.create_case(
        make_case_create(
            summary="Test slug formatting",
            description="Verify duration_slug formatting",
        )
    )

    started_at = datetime.now(tz=UTC)
    ended_at = started_at + timedelta(minutes=5)

    computation = CaseDurationComputation(
        duration_id=uuid.uuid4(),
        name="Time To Resolve",  # Mixed case with spaces
        description=None,
        start_event_id=None,
        end_event_id=None,
        started_at=started_at,
        ended_at=ended_at,
        duration=ended_at - started_at,
    )

    metrics = duration_service._format_time_series(
        [case],
        {case.id: [computation]},
    )

    assert len(metrics) == 1
    metric = metrics[0]
    assert metric.duration_name == "Time To Resolve"  # original name preserved
    assert metric.duration_slug == "time_to_resolve"  # slugified with underscore
    assert metric.timestamp == ended_at
    assert metric.value == 300.0  # 5 minutes in seconds


@pytest.mark.anyio
async def test_compute_time_series_excludes_incomplete_durations(
    session: AsyncSession, svc_role
) -> None:
    """Test that compute_time_series excludes durations without an end event."""
    cases_service = CasesService(session=session, role=svc_role)
    definition_service = CaseDurationDefinitionService(session=session, role=svc_role)
    duration_service = CaseDurationService(session=session, role=svc_role)

    await definition_service.create_definition(
        CaseDurationDefinitionCreate(
            name="Time to Resolve",
            start_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.CASE_CREATED,
            ),
            end_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.STATUS_CHANGED,
                filters=CaseDurationEventFilters(
                    new_values=[CaseStatus.RESOLVED.value]
                ),
            ),
        )
    )

    # Case without resolution - should not appear in metrics
    case = await cases_service.create_case(
        make_case_create(
            summary="Incomplete case",
            description="This case is not resolved",
        )
    )

    assert not isinstance(duration_service, CaseDurationDefinitionService)
    metrics = await duration_service.compute_time_series([case])

    # No metrics since the duration is incomplete (no end event)
    assert len(metrics) == 0


@pytest.mark.anyio
async def test_compute_durations_batch_multiple_cases(
    session: AsyncSession, svc_role
) -> None:
    """Test that compute_durations efficiently handles multiple cases."""
    cases_service = CasesService(session=session, role=svc_role)
    definition_service = CaseDurationDefinitionService(session=session, role=svc_role)
    duration_service = CaseDurationService(session=session, role=svc_role)

    metric = await definition_service.create_definition(
        CaseDurationDefinitionCreate(
            name="Time to Resolve",
            start_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.CASE_CREATED,
            ),
            end_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.STATUS_CHANGED,
                filters=CaseDurationEventFilters(
                    new_values=[CaseStatus.RESOLVED.value]
                ),
            ),
        )
    )

    # Create multiple cases with different states
    case1 = await cases_service.create_case(
        make_case_create(
            summary="Case 1 - resolved",
            description="First case",
            priority=CasePriority.HIGH,
            severity=CaseSeverity.HIGH,
        )
    )
    await cases_service.update_case(case1, CaseUpdate(status=CaseStatus.RESOLVED))

    case2 = await cases_service.create_case(
        make_case_create(
            summary="Case 2 - in progress",
            description="Second case",
        )
    )
    await cases_service.update_case(case2, CaseUpdate(status=CaseStatus.IN_PROGRESS))

    case3 = await cases_service.create_case(
        make_case_create(
            summary="Case 3 - resolved",
            description="Third case",
            priority=CasePriority.LOW,
            severity=CaseSeverity.LOW,
        )
    )
    await cases_service.update_case(case3, CaseUpdate(status=CaseStatus.RESOLVED))

    # Batch compute durations for all cases
    assert not isinstance(duration_service, CaseDurationDefinitionService)
    durations_by_case = await duration_service.compute_durations([case1, case2, case3])

    assert len(durations_by_case) == 3
    assert case1.id in durations_by_case
    assert case2.id in durations_by_case
    assert case3.id in durations_by_case

    # Case 1 should have completed duration
    case1_durations = durations_by_case[case1.id]
    assert len(case1_durations) == 1
    assert case1_durations[0].duration_id == metric.id
    assert case1_durations[0].duration is not None

    # Case 2 should have incomplete duration (not resolved)
    case2_durations = durations_by_case[case2.id]
    assert len(case2_durations) == 1
    assert case2_durations[0].duration is None

    # Case 3 should have completed duration
    case3_durations = durations_by_case[case3.id]
    assert len(case3_durations) == 1
    assert case3_durations[0].duration is not None


@pytest.mark.anyio
async def test_compute_durations_batch_applies_start_bound_per_case(
    session: AsyncSession, svc_role
) -> None:
    cases_service = CasesService(session=session, role=svc_role)
    definition_service = CaseDurationDefinitionService(session=session, role=svc_role)
    duration_service = CaseDurationService(session=session, role=svc_role)

    await definition_service.create_definition(
        CaseDurationDefinitionCreate(
            name="Time from progress to resolve",
            start_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.STATUS_CHANGED,
                filters=CaseDurationEventFilters(
                    new_values=[CaseStatus.IN_PROGRESS.value]
                ),
            ),
            end_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.STATUS_CHANGED,
                filters=CaseDurationEventFilters(
                    new_values=[CaseStatus.RESOLVED.value]
                ),
            ),
        )
    )

    case1 = await cases_service.create_case(make_case_create(summary="Case 1"))
    case2 = await cases_service.create_case(make_case_create(summary="Case 2"))
    case3 = await cases_service.create_case(make_case_create(summary="Case 3"))
    base_time = datetime.now(UTC)

    case1_early_resolved = CaseEvent(
        workspace_id=case1.workspace_id,
        case_id=case1.id,
        type=CaseEventType.STATUS_CHANGED,
        data={"old": CaseStatus.NEW.value, "new": CaseStatus.RESOLVED.value},
        created_at=base_time + timedelta(seconds=1),
    )
    case1_start = CaseEvent(
        workspace_id=case1.workspace_id,
        case_id=case1.id,
        type=CaseEventType.STATUS_CHANGED,
        data={"old": CaseStatus.NEW.value, "new": CaseStatus.IN_PROGRESS.value},
        created_at=base_time + timedelta(seconds=2),
    )
    case1_end = CaseEvent(
        workspace_id=case1.workspace_id,
        case_id=case1.id,
        type=CaseEventType.STATUS_CHANGED,
        data={"old": CaseStatus.IN_PROGRESS.value, "new": CaseStatus.RESOLVED.value},
        created_at=base_time + timedelta(seconds=3),
    )
    case2_start = CaseEvent(
        workspace_id=case2.workspace_id,
        case_id=case2.id,
        type=CaseEventType.STATUS_CHANGED,
        data={"old": CaseStatus.NEW.value, "new": CaseStatus.IN_PROGRESS.value},
        created_at=base_time + timedelta(seconds=4),
    )
    case2_end = CaseEvent(
        workspace_id=case2.workspace_id,
        case_id=case2.id,
        type=CaseEventType.STATUS_CHANGED,
        data={"old": CaseStatus.IN_PROGRESS.value, "new": CaseStatus.RESOLVED.value},
        created_at=base_time + timedelta(seconds=5),
    )
    case3_end_without_start = CaseEvent(
        workspace_id=case3.workspace_id,
        case_id=case3.id,
        type=CaseEventType.STATUS_CHANGED,
        data={"old": CaseStatus.NEW.value, "new": CaseStatus.RESOLVED.value},
        created_at=base_time + timedelta(seconds=6),
    )
    session.add_all(
        [
            case1_early_resolved,
            case1_start,
            case1_end,
            case2_start,
            case2_end,
            case3_end_without_start,
        ]
    )
    await session.flush()

    durations_by_case = await duration_service.compute_durations([case1, case2, case3])

    case1_duration = durations_by_case[case1.id][0]
    assert case1_duration.start_event_id == case1_start.id
    assert case1_duration.end_event_id == case1_end.id
    assert case1_duration.end_event_id != case1_early_resolved.id
    assert case1_duration.duration is not None

    case2_duration = durations_by_case[case2.id][0]
    assert case2_duration.start_event_id == case2_start.id
    assert case2_duration.end_event_id == case2_end.id
    assert case2_duration.duration is not None

    case3_duration = durations_by_case[case3.id][0]
    assert case3_duration.start_event_id is None
    assert case3_duration.end_event_id == case3_end_without_start.id
    assert case3_duration.duration is None


@pytest.mark.anyio
async def test_compute_time_series_multiple_cases_with_mixed_completion(
    session: AsyncSession, svc_role
) -> None:
    """Test compute_time_series with multiple cases, some complete and some incomplete."""
    cases_service = CasesService(session=session, role=svc_role)
    definition_service = CaseDurationDefinitionService(session=session, role=svc_role)
    duration_service = CaseDurationService(session=session, role=svc_role)

    await definition_service.create_definition(
        CaseDurationDefinitionCreate(
            name="Time to Resolve",
            start_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.CASE_CREATED,
            ),
            end_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.STATUS_CHANGED,
                filters=CaseDurationEventFilters(
                    new_values=[CaseStatus.RESOLVED.value]
                ),
            ),
        )
    )

    # Resolved case
    case1 = await cases_service.create_case(
        make_case_create(
            summary="Resolved case",
            description="This case is resolved",
            priority=CasePriority.HIGH,
            severity=CaseSeverity.HIGH,
        )
    )
    await cases_service.update_case(case1, CaseUpdate(status=CaseStatus.RESOLVED))

    # Unresolved case
    case2 = await cases_service.create_case(
        make_case_create(
            summary="Unresolved case",
            description="This case is not resolved",
            priority=CasePriority.LOW,
            severity=CaseSeverity.LOW,
        )
    )

    assert not isinstance(duration_service, CaseDurationDefinitionService)
    metrics = await duration_service.compute_time_series([case1, case2])

    # Only the resolved case should have a metric
    assert len(metrics) == 1
    assert metrics[0].case_id == str(case1.id)
    assert metrics[0].case_priority == CasePriority.HIGH.value


@pytest.mark.anyio
async def test_compute_time_series_empty_cases_list(
    session: AsyncSession, svc_role
) -> None:
    """Test compute_time_series with empty cases list returns empty list."""
    duration_service = CaseDurationService(session=session, role=svc_role)

    assert not isinstance(duration_service, CaseDurationDefinitionService)
    metrics = await duration_service.compute_time_series([])

    assert metrics == []


@pytest.mark.anyio
async def test_compute_durations_empty_cases_list(
    session: AsyncSession, svc_role
) -> None:
    """Test compute_durations with empty cases list returns empty dict."""
    duration_service = CaseDurationService(session=session, role=svc_role)

    assert not isinstance(duration_service, CaseDurationDefinitionService)
    durations = await duration_service.compute_durations([])

    assert durations == {}


def test_duration_anchor_rejects_legacy_dot_path_filters() -> None:
    with pytest.raises(ValidationError):
        CaseDurationEventAnchor.model_validate(
            {
                "event_type": CaseEventType.FIELDS_CHANGED,
                "field_filters": {"data.changes.field": ["severity_score"]},
            }
        )


def test_duration_anchor_rejects_custom_timestamp_paths() -> None:
    with pytest.raises(ValidationError):
        CaseDurationEventAnchor.model_validate(
            {
                "event_type": CaseEventType.CASE_CREATED,
                "timestamp_path": "data.created_at",
            }
        )


@pytest.mark.parametrize(
    "event_type",
    [
        CaseEventType.PRIORITY_CHANGED,
        CaseEventType.SEVERITY_CHANGED,
        CaseEventType.STATUS_CHANGED,
        CaseEventType.TAG_ADDED,
        CaseEventType.TAG_REMOVED,
        CaseEventType.FIELDS_CHANGED,
        CaseEventType.DROPDOWN_VALUE_CHANGED,
    ],
)
def test_duration_anchor_rejects_empty_required_filters(
    event_type: CaseEventType,
) -> None:
    with pytest.raises(ValidationError):
        CaseDurationEventAnchor(event_type=event_type)


@pytest.mark.parametrize(
    "event_type",
    [
        CaseEventType.CASE_CREATED,
        CaseEventType.CASE_CLOSED,
        CaseEventType.CASE_REOPENED,
    ],
)
def test_duration_anchor_allows_empty_filters_for_unfiltered_events(
    event_type: CaseEventType,
) -> None:
    anchor = CaseDurationEventAnchor(event_type=event_type)

    assert anchor.filters == CaseDurationEventFilters()


@pytest.mark.anyio
async def test_duration_storage_maps_known_legacy_ui_filters(
    session: AsyncSession, svc_role
) -> None:
    definition_service = CaseDurationDefinitionService(session=session, role=svc_role)

    filters, unsupported = definition_service._filters_from_storage(
        CaseEventType.FIELDS_CHANGED,
        {"data.changes.field": ["severity_score"]},
    )

    assert not unsupported
    assert filters == CaseDurationEventFilters(field_ids=["severity_score"])


@pytest.mark.anyio
async def test_duration_storage_marks_arbitrary_legacy_filters_unsupported(
    session: AsyncSession, svc_role
) -> None:
    definition_service = CaseDurationDefinitionService(session=session, role=svc_role)

    filters, unsupported = definition_service._filters_from_storage(
        CaseEventType.FIELDS_CHANGED,
        {"data.items.name": ["foo"]},
    )

    assert unsupported
    assert filters == CaseDurationEventFilters()


@pytest.mark.anyio
async def test_duration_storage_allows_empty_legacy_filters(
    session: AsyncSession, svc_role
) -> None:
    definition_service = CaseDurationDefinitionService(session=session, role=svc_role)
    assert svc_role.workspace_id is not None
    entity = CaseDurationDefinitionDB(
        workspace_id=svc_role.workspace_id,
        name="Legacy status duration",
        start_event_type=CaseEventType.STATUS_CHANGED,
        start_timestamp_path="created_at",
        start_field_filters={},
        start_selection=CaseDurationAnchorSelection.FIRST,
        end_event_type=CaseEventType.CASE_CLOSED,
        end_timestamp_path="created_at",
        end_field_filters={},
        end_selection=CaseDurationAnchorSelection.FIRST,
    )

    anchor = definition_service._anchor_from_entity(entity, "start")

    assert anchor._has_unsupported_filters
    assert anchor.filters == CaseDurationEventFilters()
    assert anchor._legacy_field_filters == {}


@pytest.mark.anyio
async def test_legacy_empty_status_filters_match_any_status_change(
    session: AsyncSession, svc_role
) -> None:
    cases_service = CasesService(session=session, role=svc_role)
    duration_service = CaseDurationService(session=session, role=svc_role)

    assert svc_role.workspace_id is not None
    definition = CaseDurationDefinitionDB(
        workspace_id=svc_role.workspace_id,
        name="Any status duration",
        start_event_type=CaseEventType.CASE_CREATED,
        start_timestamp_path="created_at",
        start_field_filters={},
        start_selection=CaseDurationAnchorSelection.FIRST,
        end_event_type=CaseEventType.STATUS_CHANGED,
        end_timestamp_path="created_at",
        end_field_filters={},
        end_selection=CaseDurationAnchorSelection.FIRST,
    )
    session.add(definition)
    await session.flush()

    case = await cases_service.create_case(make_case_create())
    case = await cases_service.update_case(
        case,
        CaseUpdate(status=CaseStatus.IN_PROGRESS),
    )

    values = await duration_service.compute_duration(case)

    assert len(values) == 1
    assert values[0].end_event_id is not None
    assert values[0].ended_at is not None
    assert values[0].duration is not None


@pytest.mark.anyio
async def test_legacy_empty_field_filters_match_any_field_change(
    session: AsyncSession, svc_role
) -> None:
    cases_service = CasesService(session=session, role=svc_role)
    duration_service = CaseDurationService(session=session, role=svc_role)

    assert svc_role.workspace_id is not None
    definition = CaseDurationDefinitionDB(
        workspace_id=svc_role.workspace_id,
        name="Any field duration",
        start_event_type=CaseEventType.CASE_CREATED,
        start_timestamp_path="created_at",
        start_field_filters={},
        start_selection=CaseDurationAnchorSelection.FIRST,
        end_event_type=CaseEventType.FIELDS_CHANGED,
        end_timestamp_path="created_at",
        end_field_filters={},
        end_selection=CaseDurationAnchorSelection.FIRST,
    )
    session.add(definition)
    await session.flush()

    case = await cases_service.create_case(make_case_create())
    field_event = CaseEvent(
        workspace_id=case.workspace_id,
        case_id=case.id,
        type=CaseEventType.FIELDS_CHANGED,
        data={"changes": [{"field": "legacy_field", "old": None, "new": "value"}]},
        created_at=datetime.now(UTC) + timedelta(seconds=1),
    )
    session.add(field_event)
    await session.flush()

    values = await duration_service.compute_duration(case)

    assert len(values) == 1
    assert values[0].end_event_id == field_event.id
    assert values[0].ended_at == field_event.created_at
    assert values[0].duration is not None


@pytest.mark.anyio
async def test_legacy_custom_timestamp_path_uses_event_scan_fallback(
    session: AsyncSession, svc_role
) -> None:
    cases_service = CasesService(session=session, role=svc_role)
    duration_service = CaseDurationService(session=session, role=svc_role)

    case = await cases_service.create_case(make_case_create())
    case_created_result = await session.execute(
        select(CaseEvent).where(
            CaseEvent.case_id == case.id,
            CaseEvent.type == CaseEventType.CASE_CREATED,
        )
    )
    created_event = case_created_result.scalar_one()
    custom_end = created_event.created_at + timedelta(hours=2)
    status_event = CaseEvent(
        workspace_id=case.workspace_id,
        case_id=case.id,
        type=CaseEventType.STATUS_CHANGED,
        data={
            "old": CaseStatus.NEW.value,
            "new": CaseStatus.RESOLVED.value,
            "resolved_at": custom_end.isoformat(),
        },
        created_at=created_event.created_at + timedelta(seconds=1),
    )
    session.add(status_event)

    assert svc_role.workspace_id is not None
    definition = CaseDurationDefinitionDB(
        workspace_id=svc_role.workspace_id,
        name="Legacy custom timestamp duration",
        start_event_type=CaseEventType.CASE_CREATED,
        start_timestamp_path="created_at",
        start_field_filters={},
        start_selection=CaseDurationAnchorSelection.FIRST,
        end_event_type=CaseEventType.STATUS_CHANGED,
        end_timestamp_path="data.resolved_at",
        end_field_filters={"data.new": CaseStatus.RESOLVED.value},
        end_selection=CaseDurationAnchorSelection.FIRST,
    )
    session.add(definition)
    await session.flush()

    values = await duration_service.compute_duration(case)

    assert len(values) == 1
    value = values[0]
    assert value.end_event_id == status_event.id
    assert value.ended_at == custom_end
    assert value.duration == custom_end - created_event.created_at
