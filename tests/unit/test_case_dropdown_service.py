import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.authz.scopes import VIEWER_SCOPES
from tracecat.cases.dropdowns.schemas import (
    CaseDropdownDefinitionCreate,
    CaseDropdownDefinitionUpdate,
    CaseDropdownOptionCreate,
    CaseDropdownOptionUpdate,
    CaseDropdownValueInput,
)
from tracecat.cases.dropdowns.service import (
    CaseDropdownDefinitionsService,
    CaseDropdownValuesService,
)
from tracecat.exceptions import (
    ScopeDeniedError,
    TracecatNotFoundError,
    TracecatValidationError,
)

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

    async def test_mutations_require_case_scopes(
        self,
        session: AsyncSession,
        dropdown_service: CaseDropdownDefinitionsService,
    ) -> None:
        """Mutation methods should reject roles without case mutation scopes."""
        definition = await _create_definition(dropdown_service)
        option = await dropdown_service.add_option(
            definition.id,
            CaseDropdownOptionCreate(label="High", ref="high"),
        )

        viewer_role = dropdown_service.role.model_copy(update={"scopes": VIEWER_SCOPES})
        viewer_service = CaseDropdownDefinitionsService(
            session=session, role=viewer_role
        )

        with pytest.raises(ScopeDeniedError):
            await viewer_service.create_definition(
                CaseDropdownDefinitionCreate(name="Blocked", ref="blocked", options=[])
            )
        with pytest.raises(ScopeDeniedError):
            await viewer_service.update_definition(
                definition, CaseDropdownDefinitionUpdate(name="Blocked")
            )
        with pytest.raises(ScopeDeniedError):
            await viewer_service.delete_definition(definition)
        with pytest.raises(ScopeDeniedError):
            await viewer_service.add_option(
                definition.id, CaseDropdownOptionCreate(label="Low", ref="low")
            )
        with pytest.raises(ScopeDeniedError):
            await viewer_service.update_option(
                definition.id, option.id, CaseDropdownOptionUpdate(label="Blocked")
            )
        with pytest.raises(ScopeDeniedError):
            await viewer_service.delete_option(definition.id, option.id)

        viewer_values_service = CaseDropdownValuesService(
            session=session, role=viewer_role
        )
        with pytest.raises(ScopeDeniedError):
            await viewer_values_service.apply_values(uuid.uuid4(), [])
        with pytest.raises(ScopeDeniedError):
            await viewer_values_service.set_value_from_input(
                uuid.uuid4(),
                CaseDropdownValueInput(definition_ref="analyst_verdict"),
            )

    async def test_set_value_requires_update_scope(
        self,
        session: AsyncSession,
        dropdown_service: CaseDropdownDefinitionsService,
    ) -> None:
        """A create-only role may not set dropdown values on existing cases."""
        create_only_role = dropdown_service.role.model_copy(
            update={"scopes": VIEWER_SCOPES | {"case:create"}}
        )
        values_service = CaseDropdownValuesService(
            session=session, role=create_only_role
        )

        with pytest.raises(ScopeDeniedError):
            await values_service.set_value_from_input(
                uuid.uuid4(),
                CaseDropdownValueInput(definition_ref="analyst_verdict"),
            )

        # apply_values serves the scope-guarded CasesService create path, so a
        # create-only role passes its scope gate (and fails later on lookup).
        with pytest.raises(TracecatNotFoundError):
            await values_service.apply_values(uuid.uuid4(), [])

    async def test_list_definitions_order_is_deterministic(
        self, dropdown_service: CaseDropdownDefinitionsService
    ) -> None:
        """Position ties should fall back to creation order, stable across calls."""
        refs = ["first", "second", "third"]
        for ref in refs:
            await _create_definition(dropdown_service, name=ref.title(), ref=ref)

        listed = await dropdown_service.list_definitions()
        assert [d.ref for d in listed] == refs
        relisted = await dropdown_service.list_definitions()
        assert [d.id for d in relisted] == [d.id for d in listed]

    async def test_reads_require_case_read_scope(
        self,
        session: AsyncSession,
        dropdown_service: CaseDropdownDefinitionsService,
    ) -> None:
        """Definition reads should reject roles without case:read."""
        definition = await _create_definition(dropdown_service)

        scopeless_role = dropdown_service.role.model_copy(
            update={"scopes": frozenset()}
        )
        scopeless_service = CaseDropdownDefinitionsService(
            session=session, role=scopeless_role
        )

        with pytest.raises(ScopeDeniedError):
            await scopeless_service.list_definitions()
        with pytest.raises(ScopeDeniedError):
            await scopeless_service.get_definition(definition.id)
        with pytest.raises(ScopeDeniedError):
            await scopeless_service.get_definition_by_ref(definition.ref)

        viewer_role = dropdown_service.role.model_copy(update={"scopes": VIEWER_SCOPES})
        viewer_service = CaseDropdownDefinitionsService(
            session=session, role=viewer_role
        )
        definitions = await viewer_service.list_definitions()
        assert [d.id for d in definitions] == [definition.id]

        # Write-only roles can fetch a definition (mutation flows look it up
        # first) but cannot list the read surface. case:create is used here
        # because case:update implies case:read platform-wide.
        create_only_role = dropdown_service.role.model_copy(
            update={"scopes": frozenset({"case:create"})}
        )
        create_only_service = CaseDropdownDefinitionsService(
            session=session, role=create_only_role
        )
        fetched = await create_only_service.get_definition(definition.id)
        assert fetched.id == definition.id
        with pytest.raises(ScopeDeniedError):
            await create_only_service.list_definitions()
