import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.exceptions import TracecatNotFoundError
from tracecat.variables.schemas import VariableCreate, VariableSearch, VariableUpdate
from tracecat.variables.service import VariablesService

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
async def service(session: AsyncSession, svc_role: Role) -> VariablesService:
    return VariablesService(session=session, role=svc_role)


@pytest.fixture
def variable_create_params() -> VariableCreate:
    return VariableCreate(
        name="api_config",
        description="Primary API configuration",
        values={"base_url": "https://example.com", "token": "abc123"},
        environment="test",
    )


@pytest.mark.anyio
class TestVariablesService:
    async def test_create_and_get_variable(
        self, service: VariablesService, variable_create_params: VariableCreate
    ) -> None:
        created = await service.create_variable(variable_create_params)
        assert created.name == variable_create_params.name
        assert created.values == variable_create_params.values

        fetched = await service.get_variable_by_name(variable_create_params.name)
        assert fetched.name == variable_create_params.name
        assert fetched.values == variable_create_params.values
        assert fetched.environment == variable_create_params.environment

    async def test_update_variable(
        self, service: VariablesService, variable_create_params: VariableCreate
    ) -> None:
        created = await service.create_variable(variable_create_params)

        variable = await service.get_variable(created.id)
        updated = await service.update_variable(
            variable,
            VariableUpdate(
                values={"base_url": "https://api.example.com", "timeout": 30}
            ),
        )
        assert updated.values == {
            "base_url": "https://api.example.com",
            "timeout": 30,
        }

    async def test_delete_variable(
        self, service: VariablesService, variable_create_params: VariableCreate
    ) -> None:
        created = await service.create_variable(variable_create_params)

        variable = await service.get_variable(created.id)
        await service.delete_variable(variable)

        with pytest.raises(TracecatNotFoundError):
            await service.get_variable(created.id)

    async def test_list_variables_filters_environment(
        self, service: VariablesService, variable_create_params: VariableCreate
    ) -> None:
        await service.create_variable(variable_create_params)
        await service.create_variable(
            VariableCreate(
                name="api_config_prod",
                description="Prod config",
                values={"base_url": "https://prod.example.com"},
                environment="prod",
            )
        )

        all_variables = await service.list_variables()
        assert {var.environment for var in all_variables} >= {"test", "prod"}

        test_variables = await service.list_variables(environment="test")
        assert all(var.environment == "test" for var in test_variables)
        assert any(var.name == variable_create_params.name for var in test_variables)

    async def test_search_variables(
        self, service: VariablesService, variable_create_params: VariableCreate
    ) -> None:
        created = await service.create_variable(variable_create_params)

        results = await service.search_variables(
            VariableSearch(names={variable_create_params.name}, environment="test")
        )
        assert len(results) == 1
        assert results[0].id == created.id
