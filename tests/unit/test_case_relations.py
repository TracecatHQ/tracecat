import pytest
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.models import CaseCreate, CaseUpdate
from tracecat.cases.service import CasesService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatException

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def cases_service(session: AsyncSession, svc_role: Role) -> CasesService:
    return CasesService(session=session, role=svc_role)


@pytest.fixture
def case_params() -> CaseCreate:
    return CaseCreate(
        summary="Case",
        description="Test",
        status=CaseStatus.NEW,
        priority=CasePriority.MEDIUM,
        severity=CaseSeverity.MEDIUM,
    )


@pytest.mark.anyio
async def test_similar_case_relationship(cases_service: CasesService, case_params: CaseCreate) -> None:
    primary = await cases_service.create_case(case_params)
    other_params = case_params.model_copy(update={"summary": "Case B"})
    secondary = await cases_service.create_case(other_params)

    await cases_service.add_similar_case(primary.id, secondary.id)

    similar_cases, merged_cases, merged_into = await cases_service.get_case_relations(primary.id)
    assert merged_cases == []
    assert merged_into is None
    assert {case.id for case in similar_cases} == {secondary.id}

    await cases_service.remove_similar_case(primary.id, secondary.id)
    similar_cases, _, _ = await cases_service.get_case_relations(primary.id)
    assert similar_cases == []


@pytest.mark.anyio
async def test_merge_case_cascades_status(cases_service: CasesService, case_params: CaseCreate) -> None:
    primary = await cases_service.create_case(case_params)
    secondary_params = case_params.model_copy(update={"summary": "Secondary"})
    secondary = await cases_service.create_case(secondary_params)

    primary_obj = await cases_service.get_case(primary.id)
    assert primary_obj is not None
    await cases_service.update_case(primary_obj, CaseUpdate(status=CaseStatus.IN_PROGRESS))

    await cases_service.merge_case(primary.id, secondary.id)

    _, merged_cases, _ = await cases_service.get_case_relations(primary.id)
    assert {case.id for case in merged_cases} == {secondary.id}

    merged_primary = await cases_service.get_case(primary.id)
    merged_secondary = await cases_service.get_case(secondary.id)
    assert merged_primary is not None
    assert merged_secondary is not None
    assert merged_secondary.status == merged_primary.status == CaseStatus.IN_PROGRESS

    merged_primary = await cases_service.get_case(primary.id)
    assert merged_primary is not None
    await cases_service.update_case(merged_primary, CaseUpdate(status=CaseStatus.CLOSED))

    refreshed_secondary = await cases_service.get_case(secondary.id)
    assert refreshed_secondary is not None
    assert refreshed_secondary.status == CaseStatus.CLOSED

    with pytest.raises(TracecatException):
        secondary_obj = await cases_service.get_case(secondary.id)
        assert secondary_obj is not None
        await cases_service.update_case(
            secondary_obj,
            CaseUpdate(status=CaseStatus.NEW),
        )
