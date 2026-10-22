import asyncio
import uuid
from unittest.mock import AsyncMock, patch

import pytest
from sqlalchemy import delete
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import Workspace, WorkspaceVariable
from tracecat.exceptions import TracecatConflictError, TracecatNotFoundError
from tracecat.variables.schemas import VariableCreate, VariableSearch, VariableUpdate
from tracecat.variables.service import VariablesService

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
async def test_concurrent_variable_creates_have_one_conflict(svc_role: Role) -> None:
    barrier = asyncio.Barrier(2)
    role = svc_role.model_copy(update={"user_id": None})
    params = VariableCreate(
        name=f"synthetic_{uuid.uuid4().hex}", values={"key": "value"}
    )

    async def create() -> WorkspaceVariable:
        async with VariablesService.with_session(role=role) as service:
            commit = service.session.commit

            async def synchronized_commit() -> None:
                await barrier.wait()
                await commit()

            with patch.object(
                service.session, "commit", AsyncMock(side_effect=synchronized_commit)
            ):
                return await service.create_variable(params)

    results = await asyncio.wait_for(
        asyncio.gather(create(), create(), return_exceptions=True), timeout=15
    )
    assert sum(isinstance(result, TracecatConflictError) for result in results) == 1
    variables = [result for result in results if isinstance(result, WorkspaceVariable)]
    assert len(variables) == 1
    async with VariablesService.with_session(role=role) as service:
        await service.delete_variable(await service.get_variable(variables[0].id))


@pytest.mark.anyio
async def test_create_duplicate_rolls_back(service: VariablesService) -> None:
    params = VariableCreate(name="synthetic", values={"key": "value"})
    await service.create_variable(params)
    with pytest.raises(TracecatConflictError):
        await service.create_variable(params)
    assert (await service.get_variable_by_name("synthetic")).values == {"key": "value"}


@pytest.mark.anyio
async def test_update_unique_conflict_rolls_back(service: VariablesService) -> None:
    await service.create_variable(
        VariableCreate(name="synthetic", values={"key": "value"})
    )
    other = await service.create_variable(
        VariableCreate(name="synthetic_other", values={"key": "value"})
    )
    other_id = other.id
    with pytest.raises(TracecatConflictError):
        await service.update_variable(other, VariableUpdate(name="synthetic"))
    assert (await service.get_variable(other_id)).name == "synthetic_other"


@pytest.mark.anyio
async def test_update_deleted_variable_is_not_found(service: VariablesService) -> None:
    variable = await service.create_variable(
        VariableCreate(name="synthetic", values={"key": "value"})
    )
    await service.session.execute(
        delete(WorkspaceVariable)
        .where(WorkspaceVariable.id == variable.id)
        .execution_options(synchronize_session=False)
    )
    await service.session.commit()
    with pytest.raises(TracecatNotFoundError):
        await service.update_variable(
            variable, VariableUpdate(name="synthetic_renamed")
        )
    assert await service.list_variables() == []


@pytest.mark.anyio
async def test_variable_access_remains_workspace_scoped(
    service: VariablesService, svc_role: Role
) -> None:
    workspace = Workspace(
        name="Synthetic other workspace", organization_id=svc_role.organization_id
    )
    service.session.add(workspace)
    await service.session.commit()
    other = VariablesService(
        service.session, svc_role.model_copy(update={"workspace_id": workspace.id})
    )
    params = VariableCreate(name="synthetic", values={"key": "value"})
    foreign = await other.create_variable(params)
    with pytest.raises(TracecatNotFoundError):
        await service.get_variable(foreign.id)
    with pytest.raises(TracecatNotFoundError):
        await service.get_variable_by_name("synthetic")
    assert await service.get_variable_value("synthetic", "key") is None
    assert await service.list_variables() == []
    local = await service.create_variable(params)
    assert local.id != foreign.id
    assert [
        variable.id
        for variable in await service.search_variables(
            VariableSearch(names={"synthetic"})
        )
    ] == [local.id]


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
