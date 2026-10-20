"""Check payload predicates against PostgreSQL before pagination and counting."""

import uuid

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.enums import CasePriority, CaseSeverity
from tracecat.cases.search_schemas import CaseQuery, CaseSearchRequest
from tracecat.cases.service import CasesService
from tracecat.db.models import Case, Workspace

pytestmark = [pytest.mark.anyio, pytest.mark.integration]


async def test_payload_equality_groups_counts_pagination_and_workspace_scope(
    session: AsyncSession, svc_role: Role
) -> None:
    matching_ids: set[uuid.UUID] = set()
    for number in range(5):
        case = Case(
            id=uuid.uuid4(),
            workspace_id=svc_role.workspace_id,
            summary=f"Synthetic payload case {number}",
            description="",
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
            payload={
                "metadata": {
                    "product": {
                        "vendor_name": "Microsoft",
                        "name": "Defender" if number < 3 else "Other",
                    }
                }
            },
        )
        session.add(case)
        if number < 3:
            matching_ids.add(case.id)
    other_workspace = Workspace(
        id=uuid.uuid4(),
        organization_id=svc_role.organization_id,
        name="Synthetic other workspace",
    )
    session.add(other_workspace)
    await session.flush()
    session.add(
        Case(
            id=uuid.uuid4(),
            workspace_id=other_workspace.id,
            summary="Synthetic out-of-scope match",
            description="",
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
            payload={
                "metadata": {
                    "product": {"vendor_name": "Microsoft", "name": "Defender"}
                }
            },
        )
    )
    await session.flush()
    filters = {
        "and": [
            {
                "field": "payload.metadata.product.vendor_name",
                "op": "eq",
                "value": "Microsoft",
            },
            {"field": "payload.metadata.product.name", "op": "eq", "value": "Defender"},
        ]
    }
    service = CasesService(session, svc_role)
    first = await service.query_cases(
        CaseSearchRequest.model_validate({"filters": filters, "limit": 2})
    )
    assert first.has_more
    assert len(first.items) == 2
    assert first.total_estimate == 3
    assert all(item.payload is None for item in first.items)
    second = await service.query_cases(
        CaseSearchRequest.model_validate(
            {"filters": filters, "limit": 2, "cursor": first.next_cursor}
        )
    )
    assert not second.has_more
    assert {item.id for item in [*first.items, *second.items]} == matching_ids
    counts = await service.query_case_aggregates(
        CaseQuery.model_validate({"filters": filters})
    )
    assert counts.total == 3
    assert sum(counts.status_groups.model_dump().values()) == 3


async def test_payload_scalar_types_missing_and_json_null(
    session: AsyncSession, svc_role: Role
) -> None:
    cases = [
        Case(
            id=uuid.uuid4(),
            workspace_id=svc_role.workspace_id,
            summary="Synthetic scalar",
            description="",
            priority=CasePriority.MEDIUM,
            severity=CaseSeverity.LOW,
            payload=payload,
        )
        for payload in [
            {"value": "80"},
            {"value": 80},
            {"value": True},
            {"value": None},
            {},
            {"value": {"nested": 80}},
        ]
    ]
    session.add_all(cases)
    await session.flush()
    service = CasesService(session, svc_role)
    for value, expected in [
        ("80", cases[0].id),
        (80, cases[1].id),
        (True, cases[2].id),
    ]:
        result = await service.query_cases(
            CaseSearchRequest.model_validate(
                {"filters": {"field": "payload.value", "op": "eq", "value": value}}
            )
        )
        assert [item.id for item in result.items] == [expected]
    missing = await service.query_cases(
        CaseSearchRequest.model_validate(
            {"filters": {"field": "payload.value", "op": "is_null"}}
        )
    )
    assert [item.id for item in missing.items] == [cases[4].id]
    excluded = await service.query_cases(
        CaseSearchRequest.model_validate(
            {"filters": {"field": "payload.value", "op": "ne", "value": 80}}
        )
    )
    assert {item.id for item in excluded.items} == {cases[i].id for i in [0, 2, 3, 5]}
    with_unset = await service.query_cases(
        CaseSearchRequest.model_validate(
            {
                "filters": {
                    "or": [
                        {"field": "payload.value", "op": "ne", "value": 80},
                        {"field": "payload.value", "op": "is_null"},
                    ]
                }
            }
        )
    )
    assert {item.id for item in with_unset.items} == {
        cases[i].id for i in [0, 2, 3, 4, 5]
    }
    # Query execution never changes stored JSON nulls or missing keys.
    stored = (
        await session.scalars(
            select(Case).where(Case.id.in_([case.id for case in cases]))
        )
    ).all()
    assert len(stored) == 6
