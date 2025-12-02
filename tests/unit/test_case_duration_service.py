import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.cases.durations import (
    CaseDurationAnchorSelection,
    CaseDurationComputation,
    CaseDurationDefinitionCreate,
    CaseDurationDefinitionUpdate,
    CaseDurationEventAnchor,
)
from tracecat.cases.durations.service import (
    CaseDurationDefinitionService,
    CaseDurationService,
)
from tracecat.cases.enums import CaseEventType, CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.schemas import CaseCreate, CaseUpdate
from tracecat.cases.service import CasesService
from tracecat.cases.tags.service import CaseTagsService
from tracecat.db.models import CaseDuration
from tracecat.tags.schemas import TagCreate

pytestmark = pytest.mark.usefixtures("db")


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
                field_filters={"data.new": CaseStatus.RESOLVED},
            ),
        )
    )

    case = await cases_service.create_case(
        CaseCreate(
            summary="Investigate suspicious login",
            description="Track the suspicious user activity.",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.MEDIUM,
        )
    )

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
                field_filters={"data.new": CaseStatus.CLOSED},
            ),
        )
    )

    case = await cases_service.create_case(
        CaseCreate(
            summary="Investigate suspicious login",
            description="Track the suspicious user activity.",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.MEDIUM,
        )
    )

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
                field_filters={"data.new": [CaseStatus.RESOLVED, CaseStatus.CLOSED]},
            ),
        )
    )

    case = await cases_service.create_case(
        CaseCreate(
            summary="Investigate suspicious login",
            description="Track the suspicious user activity.",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.MEDIUM,
        )
    )

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
                field_filters={"data.tag_ref": [tag.ref]},
            ),
            end_anchor=CaseDurationEventAnchor(
                event_type=CaseEventType.TAG_REMOVED,
                field_filters={"data.tag_ref": [tag.ref]},
            ),
        )
    )

    case = await cases_service.create_case(
        CaseCreate(
            summary="Investigate missing data",
            description="Ensure data completeness",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.MEDIUM,
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
                field_filters={"data.new": CaseStatus.RESOLVED},
            ),
        )
    )

    update_payload = CaseDurationDefinitionUpdate(
        start_anchor=CaseDurationEventAnchor(
            event_type=CaseEventType.STATUS_CHANGED,
            selection=CaseDurationAnchorSelection.LAST,
            field_filters={"data.new": CaseStatus.IN_PROGRESS},
        ),
        end_anchor=CaseDurationEventAnchor(
            event_type=CaseEventType.STATUS_CHANGED,
            selection=CaseDurationAnchorSelection.FIRST,
            field_filters={"data.new": CaseStatus.RESOLVED},
        ),
    )

    updated = await definition_service.update_definition(definition.id, update_payload)

    assert updated.start_anchor.event_type == CaseEventType.STATUS_CHANGED
    assert updated.start_anchor.selection == CaseDurationAnchorSelection.LAST
    assert updated.start_anchor.field_filters == {"data.new": CaseStatus.IN_PROGRESS}
    assert updated.end_anchor.selection == CaseDurationAnchorSelection.FIRST
    assert updated.end_anchor.field_filters == {"data.new": CaseStatus.RESOLVED}

    persisted = await definition_service.get_definition(definition.id)
    assert persisted.start_anchor.event_type == CaseEventType.STATUS_CHANGED
    assert persisted.start_anchor.selection == CaseDurationAnchorSelection.LAST
    assert persisted.start_anchor.field_filters == {"data.new": CaseStatus.IN_PROGRESS}


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
                field_filters={"data.new": CaseStatus.RESOLVED},
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
                field_filters={"data.new": CaseStatus.RESOLVED},
                selection=CaseDurationAnchorSelection.LAST,
            ),
        )
    )

    case = await cases_service.create_case(
        CaseCreate(
            summary="Investigate suspicious login",
            description="Track the suspicious user activity.",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.MEDIUM,
        )
    )

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

    case = await cases_service.create_case(
        CaseCreate(
            summary="Investigate suspicious login",
            description="Track the suspicious user activity.",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.MEDIUM,
        )
    )

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
async def test_list_records_returns_flat_duration_records(
    session: AsyncSession, svc_role
) -> None:
    """Test that list_records returns denormalized records for analytics."""
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
                field_filters={"data.new": CaseStatus.RESOLVED},
            ),
        )
    )

    case = await cases_service.create_case(
        CaseCreate(
            summary="Test case for records",
            description="Testing list_records output",
            status=CaseStatus.NEW,
            priority=CasePriority.HIGH,
            severity=CaseSeverity.CRITICAL,
        )
    )

    await cases_service.update_case(case, CaseUpdate(status=CaseStatus.RESOLVED))

    assert not isinstance(duration_service, CaseDurationDefinitionService)
    records = await duration_service.list_records([case])

    assert len(records) == 1
    record = records[0]

    # Verify case context fields
    assert record.case_id == str(case.id)
    assert record.case_short_id == case.short_id
    assert record.case_summary == "Test case for records"
    assert record.case_status == CaseStatus.RESOLVED.value
    assert record.case_priority == CasePriority.HIGH.value
    assert record.case_severity == CaseSeverity.CRITICAL.value

    # Verify duration fields
    assert record.duration_name == "Time to Resolve"
    assert record.duration_description == "Elapsed time from creation to resolution"
    assert record.started_at is not None
    assert record.ended_at is not None
    assert record.duration_seconds is not None
    assert record.duration_seconds >= 0

    # Verify event references
    assert record.start_event_id is not None
    assert record.end_event_id is not None


@pytest.mark.anyio
async def test_list_records_preserves_missing_event_ids(
    session: AsyncSession, svc_role
) -> None:
    """Ensure records keep None values when anchor events are missing."""
    cases_service = CasesService(session=session, role=svc_role)
    duration_service = CaseDurationService(session=session, role=svc_role)

    case = await cases_service.create_case(
        CaseCreate(
            summary="Synthetic duration without anchors",
            description="Verify None anchors stay None",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.MEDIUM,
        )
    )

    started_at = datetime.now(tz=UTC)
    ended_at = started_at + timedelta(minutes=5)

    computation = CaseDurationComputation(
        duration_id=uuid.uuid4(),
        name="Synthetic duration",
        description=None,
        start_event_id=None,
        end_event_id=None,
        started_at=started_at,
        ended_at=ended_at,
        duration=ended_at - started_at,
    )

    records = duration_service._format_records(
        [case],
        {case.id: [computation]},
    )

    assert len(records) == 1
    record = records[0]
    assert record.start_event_id is None
    assert record.end_event_id is None


@pytest.mark.anyio
async def test_list_records_excludes_incomplete_durations(
    session: AsyncSession, svc_role
) -> None:
    """Test that list_records excludes durations without an end event."""
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
                field_filters={"data.new": CaseStatus.RESOLVED},
            ),
        )
    )

    # Case without resolution - should not appear in records
    case = await cases_service.create_case(
        CaseCreate(
            summary="Incomplete case",
            description="This case is not resolved",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.MEDIUM,
        )
    )

    assert not isinstance(duration_service, CaseDurationDefinitionService)
    records = await duration_service.list_records([case])

    # No records since the duration is incomplete (no end event)
    assert len(records) == 0


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
                field_filters={"data.new": CaseStatus.RESOLVED},
            ),
        )
    )

    # Create multiple cases with different states
    case1 = await cases_service.create_case(
        CaseCreate(
            summary="Case 1 - resolved",
            description="First case",
            status=CaseStatus.NEW,
            priority=CasePriority.HIGH,
            severity=CaseSeverity.HIGH,
        )
    )
    await cases_service.update_case(case1, CaseUpdate(status=CaseStatus.RESOLVED))

    case2 = await cases_service.create_case(
        CaseCreate(
            summary="Case 2 - in progress",
            description="Second case",
            status=CaseStatus.NEW,
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.MEDIUM,
        )
    )
    await cases_service.update_case(case2, CaseUpdate(status=CaseStatus.IN_PROGRESS))

    case3 = await cases_service.create_case(
        CaseCreate(
            summary="Case 3 - resolved",
            description="Third case",
            status=CaseStatus.NEW,
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
async def test_list_records_multiple_cases_with_mixed_completion(
    session: AsyncSession, svc_role
) -> None:
    """Test list_records with multiple cases, some complete and some incomplete."""
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
                field_filters={"data.new": CaseStatus.RESOLVED},
            ),
        )
    )

    # Resolved case
    case1 = await cases_service.create_case(
        CaseCreate(
            summary="Resolved case",
            description="This case is resolved",
            status=CaseStatus.NEW,
            priority=CasePriority.HIGH,
            severity=CaseSeverity.HIGH,
        )
    )
    await cases_service.update_case(case1, CaseUpdate(status=CaseStatus.RESOLVED))

    # Unresolved case
    case2 = await cases_service.create_case(
        CaseCreate(
            summary="Unresolved case",
            description="This case is not resolved",
            status=CaseStatus.NEW,
            priority=CasePriority.LOW,
            severity=CaseSeverity.LOW,
        )
    )

    assert not isinstance(duration_service, CaseDurationDefinitionService)
    records = await duration_service.list_records([case1, case2])

    # Only the resolved case should have a record
    assert len(records) == 1
    assert records[0].case_id == str(case1.id)
    assert records[0].case_summary == "Resolved case"


@pytest.mark.anyio
async def test_list_records_empty_cases_list(session: AsyncSession, svc_role) -> None:
    """Test list_records with empty cases list returns empty list."""
    duration_service = CaseDurationService(session=session, role=svc_role)

    assert not isinstance(duration_service, CaseDurationDefinitionService)
    records = await duration_service.list_records([])

    assert records == []


@pytest.mark.anyio
async def test_compute_durations_empty_cases_list(
    session: AsyncSession, svc_role
) -> None:
    """Test compute_durations with empty cases list returns empty dict."""
    duration_service = CaseDurationService(session=session, role=svc_role)

    assert not isinstance(duration_service, CaseDurationDefinitionService)
    durations = await duration_service.compute_durations([])

    assert durations == {}
