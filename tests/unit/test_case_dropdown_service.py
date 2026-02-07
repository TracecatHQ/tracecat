import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.dropdowns.schemas import (
    CaseDropdownDefinitionCreate,
    CaseDropdownDefinitionUpdate,
)
from tracecat.cases.dropdowns.service import CaseDropdownDefinitionsService
from tracecat.exceptions import TracecatValidationError

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def dropdown_service(
    session: AsyncSession, svc_role: Role
) -> CaseDropdownDefinitionsService:
    """Create a dropdown definitions service bound to the test workspace."""
    return CaseDropdownDefinitionsService(session=session, role=svc_role)


async def _create_definition(
    service: CaseDropdownDefinitionsService,
    *,
    name: str = "Analyst Verdict",
    ref: str = "analyst_verdict",
):
    return await service.create_definition(
        CaseDropdownDefinitionCreate(
            name=name,
            ref=ref,
            is_ordered=False,
            options=[],
        )
    )


@pytest.mark.anyio
class TestCaseDropdownDefinitionsService:
    async def test_update_definition_renaming_regenerates_ref(
        self, dropdown_service: CaseDropdownDefinitionsService
    ) -> None:
        """Renaming a dropdown should update its slug reference."""
        definition = await _create_definition(dropdown_service)

        updated = await dropdown_service.update_definition(
            definition,
            CaseDropdownDefinitionUpdate(name="Triage Decision"),
        )

        assert updated.name == "Triage Decision"
        assert updated.ref == "triage_decision"

    async def test_update_definition_explicit_ref_is_respected(
        self, dropdown_service: CaseDropdownDefinitionsService
    ) -> None:
        """An explicit ref should win over the generated ref."""
        definition = await _create_definition(dropdown_service)

        updated = await dropdown_service.update_definition(
            definition,
            CaseDropdownDefinitionUpdate(
                name="Escalation Status",
                ref="custom_ref",
            ),
        )

        assert updated.ref == "custom_ref"

    async def test_update_definition_rejects_name_without_valid_ref(
        self, dropdown_service: CaseDropdownDefinitionsService
    ) -> None:
        """Names that slugify to an empty ref should be rejected."""
        definition = await _create_definition(dropdown_service)

        with pytest.raises(
            TracecatValidationError, match="must produce a valid reference"
        ):
            await dropdown_service.update_definition(
                definition,
                CaseDropdownDefinitionUpdate(name="!!!"),
            )
