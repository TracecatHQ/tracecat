import asyncio
import os

import pytest
import pytest_mock

from tracecat.auth.credentials import TemporaryRole
from tracecat.auth.sandbox import AuthSandbox
from tracecat.contexts import ctx_env, ctx_role
from tracecat.db.schemas import Secret
from tracecat.secrets import secrets_manager
from tracecat.secrets.encryption import encrypt_keyvalues
from tracecat.secrets.models import (
    CreateSecretParams,
    SearchSecretsParams,
    SecretKeyValue,
)
from tracecat.secrets.service import SecretsService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatCredentialsError


@pytest.mark.asyncio
async def test_auth_sandbox_with_secrets(mocker: pytest_mock.MockFixture, test_role):
    role = ctx_role.get()
    assert role is not None

    mock_secret_keys = [SecretKeyValue(key="SECRET_KEY", value="my_secret_key")]
    mock_secret = Secret(
        name="my_secret",
        owner_id=role.workspace_id,
        encrypted_keys=encrypt_keyvalues(
            mock_secret_keys, key=os.environ["TRACECAT__DB_ENCRYPTION_KEY"]
        ),
    )

    mocker.patch.object(AuthSandbox, "_get_secrets", return_value=[mock_secret])

    async with AuthSandbox(secrets=["my_secret"], target="context") as sandbox:
        assert sandbox.secrets == {"my_secret": {"SECRET_KEY": "my_secret_key"}}

    AuthSandbox._get_secrets.assert_called_once()


@pytest.mark.asyncio
async def test_auth_sandbox_without_secrets(test_role, mock_user_id):
    # Auth sandbox has a different role.
    with TemporaryRole(
        type="service", user_id=mock_user_id, service_id="tracecat-service"
    ):
        async with AuthSandbox() as sandbox:
            assert sandbox.secrets == {}
            assert sandbox._role == Role(
                type="service",
                workspace_id=mock_user_id,
                service_id="tracecat-service",
            )


@pytest.mark.asyncio
async def test_auth_sandbox_env_target(mocker: pytest_mock.MockFixture, test_role):
    role = ctx_role.get()
    assert role is not None
    mock_secret_keys = [SecretKeyValue(key="SECRET_KEY", value="my_secret_key")]
    mock_secret = Secret(
        name="my_secret",
        owner_id=role.workspace_id,
        environment="default",
        encrypted_keys=encrypt_keyvalues(
            mock_secret_keys, key=os.environ["TRACECAT__DB_ENCRYPTION_KEY"]
        ),
    )

    # Mock SecretsService
    mock_secrets_service = mocker.AsyncMock(spec=SecretsService)
    mock_secrets_service.search_secrets.return_value = [mock_secret]
    mocker.patch(
        "tracecat.auth.sandbox.SecretsService.with_session",
        return_value=mocker.AsyncMock(
            __aenter__=mocker.AsyncMock(return_value=mock_secrets_service)
        ),
    )

    async with AuthSandbox(secrets=["my_secret"], target="env"):
        assert "SECRET_KEY" in os.environ
        assert os.environ["SECRET_KEY"] == "my_secret_key"

    assert "SECRET_KEY" not in os.environ


@pytest.mark.asyncio
async def test_auth_sandbox_missing_secret(mocker: pytest_mock.MockFixture, test_role):
    role = ctx_role.get()
    assert role is not None

    # Mock SecretsService to return None (missing secret)
    mock_secrets_service = mocker.AsyncMock(spec=SecretsService)
    mock_secrets_service.search_secrets.return_value = []
    mocker.patch(
        "tracecat.auth.sandbox.SecretsService.with_session",
        return_value=mocker.AsyncMock(
            __aenter__=mocker.AsyncMock(return_value=mock_secrets_service)
        ),
    )

    with pytest.raises(TracecatCredentialsError):
        async with AuthSandbox(
            secrets=["missing_secret"], target="context", environment="default"
        ):
            pass

    # Assert that the SecretsService was called with the correct parameters
    mock_secrets_service.search_secrets.assert_called_once_with(
        SearchSecretsParams(names=["missing_secret"], environment="default")
    )


@pytest.mark.asyncio
async def test_auth_sandbox_custom_env_target(
    mocker: pytest_mock.MockFixture, test_role
):
    # Add secrets to the db
    async with SecretsService.with_session(role=test_role) as service:
        await service.create_secret(
            CreateSecretParams(
                name="test_secret",
                environment="__FIRST__",
                keys=[SecretKeyValue(key="KEY", value="FIRST_VALUE")],
            )
        )
        await service.create_secret(
            CreateSecretParams(
                name="test_secret",
                environment="__SECOND__",
                keys=[SecretKeyValue(key="KEY", value="SECOND_VALUE")],
            )
        )

    # Verify that the correct secret is picked up
    async with AuthSandbox(
        secrets=["test_secret"], target="env", environment="__FIRST__"
    ):
        assert "KEY" in os.environ
        assert os.environ["KEY"] == "FIRST_VALUE"

    assert "KEY" not in os.environ

    # Verify that if we change the environment, the incorrect secret is picked up
    async with AuthSandbox(
        secrets=["test_secret"], target="env", environment="__SECOND__"
    ):
        assert "KEY" in os.environ
        assert os.environ["KEY"] == "SECOND_VALUE"

    assert "KEY" not in os.environ


def test_env_sandbox_initial_env():
    """Test that env_sandbox correctly sets up the initial environment."""
    initial_env = {"API_KEY": "abc123", "SECRET_TOKEN": "xyz789"}
    with secrets_manager.env_sandbox(initial_env):
        assert secrets_manager.get("API_KEY") == "abc123"
        assert secrets_manager.get("SECRET_TOKEN") == "xyz789"
        assert secrets_manager.get("NON_EXISTENT_KEY") is None


def test_env_sandbox_isolation():
    """Test that changes inside the sandbox don't affect the outer environment."""
    outer_env = ctx_env.get()
    with secrets_manager.env_sandbox({"TEMP_KEY": "temp_value"}):
        secrets_manager.set("NEW_KEY", "new_value")
        assert secrets_manager.get("NEW_KEY") == "new_value"

    assert "NEW_KEY" not in ctx_env.get()
    assert ctx_env.get() == outer_env


def test_env_sandbox_nested():
    """Test that nested env_sandbox calls work correctly."""
    with secrets_manager.env_sandbox({"OUTER": "outer_value"}):
        assert secrets_manager.get("OUTER") == "outer_value"
        with secrets_manager.env_sandbox({"INNER": "inner_value"}):
            assert secrets_manager.get("OUTER") is None
            assert secrets_manager.get("INNER") == "inner_value"
        assert secrets_manager.get("OUTER") == "outer_value"
        assert secrets_manager.get("INNER") is None


@pytest.mark.asyncio
async def test_env_sandbox_async():
    """Test that env_sandbox works in async contexts."""

    async def async_function():
        assert secrets_manager.get("ASYNC_KEY") == "async_value"

    with secrets_manager.env_sandbox({"ASYNC_KEY": "async_value"}):
        await async_function()


def test_env_sandbox_exception():
    """Test that env_sandbox resets the environment even if an exception occurs."""
    outer_env = ctx_env.get()
    try:
        with secrets_manager.env_sandbox({"EXCEPTION_KEY": "exception_value"}):
            raise ValueError("Test exception")
    except ValueError:
        pass

    assert ctx_env.get() == outer_env
    assert secrets_manager.get("EXCEPTION_KEY") is None


@pytest.mark.asyncio
async def test_env_sandbox_sync_in_async():
    """Test that env_sandbox context is propagated correctly when running a sync function in asyncio.to_thread."""

    def sync_function():
        """Synchronous function to be run in a separate thread."""
        return secrets_manager.get("THREAD_KEY")

    with secrets_manager.env_sandbox({"THREAD_KEY": "thread_value"}):
        # Run the sync function in a separate thread using asyncio.to_thread
        result = await asyncio.to_thread(sync_function)

    # Check that the context was correctly propagated to the thread
    assert result == "thread_value"
    # Verify that the environment is reset after exiting the sandbox
    assert secrets_manager.get("THREAD_KEY") is None
