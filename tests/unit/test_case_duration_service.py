import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.cases.durations import (
    CaseDurationAnchorSelection,
    CaseDurationCreate,
    CaseDurationEventAnchor,
    CaseDurationService,
)
from tracecat.cases.enums import CaseEventType, CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.models import CaseCreate, CaseUpdate
from tracecat.cases.service import CasesService

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
async def test_compute_case_durations_from_events(
    session: AsyncSession, svc_role
) -> None:
    cases_service = CasesService(session=session, role=svc_role)
    duration_service = CaseDurationService(session=session, role=svc_role)

    metric = await duration_service.create_definition(
        CaseDurationCreate(
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

    values = await duration_service.compute_for_case(case)
    assert len(values) == 1
    value = values[0]
    assert value.duration_id == metric.id
    assert value.start_event_id is not None
    assert value.started_at is not None
    assert value.end_event_id is None
    assert value.duration is None

    updated_case = await cases_service.update_case(
        case,
        CaseUpdate(status=CaseStatus.RESOLVED),
    )
    assert updated_case.status == CaseStatus.RESOLVED

    values = await duration_service.compute_for_case(updated_case)
    assert len(values) == 1
    value = values[0]
    assert value.end_event_id is not None
    assert value.ended_at is not None
    assert value.duration is not None
    assert value.duration.total_seconds() >= 0


@pytest.mark.anyio
async def test_duration_filters_match_event_payload(
    session: AsyncSession, svc_role
) -> None:
    cases_service = CasesService(session=session, role=svc_role)
    duration_service = CaseDurationService(session=session, role=svc_role)

    await duration_service.create_definition(
        CaseDurationCreate(
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

    values = await duration_service.compute_for_case(case)
    assert len(values) == 1
    value = values[0]
    assert value.end_event_id is None
    assert value.duration is None

    # Now transition to closed which should satisfy the filter
    await cases_service.update_case(case, CaseUpdate(status=CaseStatus.CLOSED))

    values = await duration_service.compute_for_case(case)
    value = values[0]
    assert value.end_event_id is not None
    assert value.duration is not None


@pytest.mark.anyio
async def test_duration_anchor_selection_first_vs_last(
    session: AsyncSession, svc_role
) -> None:
    cases_service = CasesService(session=session, role=svc_role)
    duration_service = CaseDurationService(session=session, role=svc_role)

    await duration_service.create_definition(
        CaseDurationCreate(
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
    await duration_service.create_definition(
        CaseDurationCreate(
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

    values = await duration_service.compute_for_case(case)
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
    duration_service = CaseDurationService(session=session, role=svc_role)

    await duration_service.create_definition(
        CaseDurationCreate(
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

    values = await duration_service.compute_for_case(case)
    assert len(values) == 1
    metric = values[0]

    assert metric.start_event_id is not None
    assert metric.end_event_id is not None
    assert metric.started_at is not None
    assert metric.ended_at is not None
    assert metric.duration is not None
    assert metric.duration.total_seconds() >= 0
