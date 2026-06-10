import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.cases.dropdowns.schemas import (
    CaseDropdownDefinitionCreate,
    CaseDropdownDefinitionUpdate,
    CaseDropdownOptionCreate,
    CaseDropdownOptionUpdate,
)
from tracecat.cases.dropdowns.service import CaseDropdownDefinitionsService
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError

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

    async def test_update_option_scoped_to_definition(
        self, dropdown_service: CaseDropdownDefinitionsService
    ) -> None:
        """Updating an option through a different definition should fail."""
        definition_a = await _create_definition(
            dropdown_service, name="Analyst Verdict", ref="analyst_verdict"
        )
        definition_b = await _create_definition(
            dropdown_service, name="Threat Level", ref="threat_level"
        )
        option_b = await dropdown_service.add_option(
            definition_b.id,
            CaseDropdownOptionCreate(label="High", ref="high"),
        )

        with pytest.raises(TracecatNotFoundError, match="not found for definition"):
            await dropdown_service.update_option(
                definition_a.id,
                option_b.id,
                CaseDropdownOptionUpdate(label="Hijacked"),
            )

        updated = await dropdown_service.update_option(
            definition_b.id,
            option_b.id,
            CaseDropdownOptionUpdate(label="Very High"),
        )
        assert updated.label == "Very High"

    async def test_delete_option_scoped_to_definition(
        self, dropdown_service: CaseDropdownDefinitionsService
    ) -> None:
        """Deleting an option through a different definition should fail."""
        definition_a = await _create_definition(
            dropdown_service, name="Analyst Verdict", ref="analyst_verdict"
        )
        definition_b = await _create_definition(
            dropdown_service, name="Threat Level", ref="threat_level"
        )
        option_b = await dropdown_service.add_option(
            definition_b.id,
            CaseDropdownOptionCreate(label="High", ref="high"),
        )

        with pytest.raises(TracecatNotFoundError, match="not found for definition"):
            await dropdown_service.delete_option(definition_a.id, option_b.id)

        # The option survives the mismatched delete and is removable through
        # its own definition exactly once.
        await dropdown_service.delete_option(definition_b.id, option_b.id)
        with pytest.raises(TracecatNotFoundError, match="not found for definition"):
            await dropdown_service.delete_option(definition_b.id, option_b.id)
