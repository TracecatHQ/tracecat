import uuid
from collections.abc import AsyncGenerator

import pytest
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient

from tracecat.contexts import RunContext
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.models import ActionStatement, RunActionInput
from tracecat.executor.router import router
from tracecat.logger import logger
from tracecat.registry.actions.models import RegistryActionErrorInfo
from tracecat.types.auth import Role
from tracecat.types.exceptions import WrappedExecutionError

# Unit tests


@pytest.fixture
def mock_workspace_id():
    return uuid.uuid4()


@pytest.fixture
def mock_role(mock_org_id: uuid.UUID, mock_workspace_id: uuid.UUID):
    return Role(
        type="service",
        user_id=mock_org_id,
        workspace_id=mock_workspace_id,
        service_id="tracecat-runner",
    )


@pytest.fixture
def override_role_dependency(mock_role: Role):
    async def dep(*args, **kwargs):
        return mock_role

    return dep


@pytest.fixture
async def test_client_noauth(
    monkeypatch: pytest.MonkeyPatch, override_role_dependency: Role
) -> AsyncGenerator[AsyncClient, None]:
    app = FastAPI()
    app.include_router(router)
    # Override the role dependency with our custom role
    monkeypatch.setattr(
        "tracecat.auth.credentials._role_dependency", override_role_dependency
    )

    host = "localhost"
    port = 8000
    async with AsyncClient(
        transport=ASGITransport(app=app, client=(host, port)),  # type: ignore
        base_url="http://test",
    ) as client:
        yield client


async def mock_dispatch_error(*args, **kwargs):
    """Mock dispatch that raises a WrappedExecutionError"""
    error_info = RegistryActionErrorInfo(
        type="ValueError",
        message="Test error message",
        action_name="test.action",
        filename="test_file.py",
        function="test_function",
    )
    raise WrappedExecutionError(error=error_info)


@pytest.mark.anyio
async def test_run_action_endpoint_error_handling(
    test_client_noauth: AsyncClient,
    mock_run_context: RunContext,
    mock_role: Role,
    monkeypatch: pytest.MonkeyPatch,
):
    """Test that the run_action endpoint properly handles wrapped execution errors."""

    # Mock the dispatch function to raise our error
    if not mock_role.workspace_id:
        return pytest.fail("Workspace ID is not set in test role")

    monkeypatch.setattr(
        "tracecat.executor.service.dispatch_action_on_cluster", mock_dispatch_error
    )

    # Create test input
    input_data = RunActionInput(
        task=ActionStatement(
            ref="test",
            action="test.action",
            args={},
            run_if=None,
            for_each=None,
        ),
        exec_context=create_default_execution_context(),
        run_context=mock_run_context,
    ).model_dump(mode="json")

    logger.warning("BASE URL", base_url=test_client_noauth.base_url)
    response = await test_client_noauth.post(
        "/run/test.action",
        params={"workspace_id": str(mock_role.workspace_id)},
        json=input_data,
    )

    # Verify response
    logger.info("RESPONSE", response=response)
    assert response.status_code == 500
    error_detail = response.json()
    err_info = RegistryActionErrorInfo.model_validate(error_detail["detail"])
    assert err_info.type == "ValueError"
    assert err_info.message == "Test error message"
    assert err_info.action_name == "test.action"
    assert err_info.filename == "test_file.py"
    assert err_info.function == "mock_dispatch_error"
