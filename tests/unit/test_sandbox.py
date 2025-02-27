import asyncio
import os
from datetime import datetime

import pytest
import pytest_mock
from pydantic import SecretStr

from tracecat.auth.credentials import TemporaryRole
from tracecat.auth.sandbox import AuthSandbox
from tracecat.contexts import ctx_role, get_env
from tracecat.db.schemas import BaseSecret
from tracecat.secrets import secrets_manager
from tracecat.secrets.encryption import encrypt_keyvalues
from tracecat.secrets.models import (
    SecretCreate,
    SecretKeyValue,
    SecretSearch,
)
from tracecat.secrets.service import SecretsService
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatCredentialsError


@pytest.mark.anyio
async def test_auth_sandbox_with_secrets(mocker: pytest_mock.MockFixture, test_role):
    from datetime import datetime

    role = ctx_role.get()
    assert role is not None
    assert role.workspace_id is not None

    mock_secret_keys = [
        SecretKeyValue(key="SECRET_KEY", value=SecretStr("my_secret_key"))
    ]

    mock_secret = BaseSecret(
        name="my_secret",
        owner_id=role.workspace_id,
        encrypted_keys=encrypt_keyvalues(
            mock_secret_keys, key=os.environ["TRACECAT__DB_ENCRYPTION_KEY"]
        ),
        created_at=datetime.now(),
        updated_at=datetime.now(),
        tags={},
    )

    mocker.patch.object(AuthSandbox, "_get_secrets", return_value=[mock_secret])

    async with AuthSandbox(secrets=["my_secret"]) as sandbox:
        assert sandbox.secrets == {"my_secret": {"SECRET_KEY": "my_secret_key"}}

    AuthSandbox._get_secrets.assert_called_once()


@pytest.mark.anyio
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


@pytest.mark.anyio
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
        async with AuthSandbox(secrets=["missing_secret"], environment="default"):
            pass

    # Assert that the SecretsService was called with the correct parameters
    mock_secrets_service.search_secrets.assert_called_once_with(
        SecretSearch(names={"missing_secret"}, environment="default")
    )


@pytest.mark.anyio
async def test_auth_sandbox_custom_runtime_env_target(
    mocker: pytest_mock.MockFixture, test_role
):
    # Add secrets to the db
    async with SecretsService.with_session(role=test_role) as service:
        await service.create_secret(
            SecretCreate(
                name="test_secret",
                environment="__FIRST__",
                keys=[SecretKeyValue(key="KEY", value=SecretStr("FIRST_VALUE"))],
            )
        )
        await service.create_secret(
            SecretCreate(
                name="test_secret",
                environment="__SECOND__",
                keys=[SecretKeyValue(key="KEY", value=SecretStr("SECOND_VALUE"))],
            )
        )

    # Verify that the correct secret is picked up
    async with AuthSandbox(secrets=["test_secret"], environment="__FIRST__") as sandbox:
        assert "test_secret" in sandbox.secrets
        assert "KEY" in sandbox.secrets["test_secret"]
        assert sandbox.secrets["test_secret"]["KEY"] == "FIRST_VALUE"

    assert "KEY" not in sandbox.secrets

    # Verify that if we change the environment, the incorrect secret is picked up
    async with AuthSandbox(
        secrets=["test_secret"], environment="__SECOND__"
    ) as sandbox:
        assert "test_secret" in sandbox.secrets
        assert "KEY" in sandbox.secrets["test_secret"]
        assert sandbox.secrets["test_secret"]["KEY"] == "SECOND_VALUE"

    assert "KEY" not in sandbox.secrets


def test_env_sandbox_initial_env():
    """Test that env_sandbox correctly sets up the initial environment."""
    initial_env = {"API_KEY": "abc123", "SECRET_TOKEN": "xyz789"}
    with secrets_manager.env_sandbox(initial_env):
        assert secrets_manager.get("API_KEY") == "abc123"
        assert secrets_manager.get("SECRET_TOKEN") == "xyz789"
        assert secrets_manager.get("NON_EXISTENT_KEY") is None


def test_env_sandbox_isolation():
    """Test that changes inside the sandbox don't affect the outer environment."""
    outer_env = get_env()
    with secrets_manager.env_sandbox({"TEMP_KEY": "temp_value"}):
        secrets_manager.set("NEW_KEY", "new_value")
        assert secrets_manager.get("NEW_KEY") == "new_value"

    assert "NEW_KEY" not in get_env()
    assert get_env() == outer_env


def test_env_sandbox_nested():
    """Test that nested env_sandbox calls work correctly."""
    with secrets_manager.env_sandbox({"OUTER": "outer_value"}):
        assert secrets_manager.get("OUTER") == "outer_value"
        with secrets_manager.env_sandbox({"INNER": "inner_value"}):
            assert secrets_manager.get("OUTER") is None
            assert secrets_manager.get("INNER") == "inner_value"
        assert secrets_manager.get("OUTER") == "outer_value"
        assert secrets_manager.get("INNER") is None


@pytest.mark.anyio
async def test_env_sandbox_async():
    """Test that env_sandbox works in async contexts."""

    async def async_function():
        assert secrets_manager.get("ASYNC_KEY") == "async_value"

    with secrets_manager.env_sandbox({"ASYNC_KEY": "async_value"}):
        await async_function()


def test_env_sandbox_exception():
    """Test that env_sandbox resets the environment even if an exception occurs."""
    outer_env = get_env()
    try:
        with secrets_manager.env_sandbox({"EXCEPTION_KEY": "exception_value"}):
            raise ValueError("Test exception")
    except ValueError:
        pass

    assert get_env() == outer_env
    assert secrets_manager.get("EXCEPTION_KEY") is None


@pytest.mark.anyio
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


@pytest.mark.anyio
async def test_auth_sandbox_optional_secrets(
    mocker: pytest_mock.MockFixture, test_role
):
    """Test that AuthSandbox handles both required and optional secrets correctly."""

    role = ctx_role.get()
    assert role is not None
    assert role.workspace_id is not None
    # Create mock secrets
    required_secret = BaseSecret(
        name="required_secret",
        owner_id=role.workspace_id,
        encrypted_keys=encrypt_keyvalues(
            [SecretKeyValue(key="REQ_KEY", value=SecretStr("required_value"))],
            key=os.environ["TRACECAT__DB_ENCRYPTION_KEY"],
        ),
        created_at=datetime.now(),
        updated_at=datetime.now(),
        tags={},
    )

    # Mock SecretsService to return only the required secret
    mock_secrets_service = mocker.AsyncMock(spec=SecretsService)
    mock_secrets_service.search_secrets.return_value = [required_secret]
    mocker.patch(
        "tracecat.auth.sandbox.SecretsService.with_session",
        return_value=mocker.AsyncMock(
            __aenter__=mocker.AsyncMock(return_value=mock_secrets_service)
        ),
    )

    # Test with both required and optional secrets
    async with AuthSandbox(
        secrets=["required_secret", "optional_secret"],
        optional_secrets=["optional_secret"],
    ) as sandbox:
        # Required secret should be present
        assert "required_secret" in sandbox.secrets
        assert sandbox.secrets["required_secret"]["REQ_KEY"] == "required_value"

        # Optional secret can be missing without raising an error
        assert "optional_secret" not in sandbox.secrets

    # Verify search was called with both secret names
    mock_secrets_service.search_secrets.assert_called_once_with(
        SecretSearch(
            names={"required_secret", "optional_secret"}, environment="default"
        )
    )

    # Test that missing required secret still raises an error
    mock_secrets_service.search_secrets.return_value = []
    with pytest.raises(TracecatCredentialsError) as exc_info:
        async with AuthSandbox(
            secrets=["required_secret", "optional_secret"],
            optional_secrets=["optional_secret"],
        ):
            pass

    assert "Missing secrets: required_secret" in str(exc_info.value)


@pytest.mark.anyio
async def test_auth_sandbox_all_secrets_present(
    mocker: pytest_mock.MockFixture, test_role
):
    """Test AuthSandbox when both required and optional secrets are available."""

    role = ctx_role.get()
    assert role is not None
    assert role.workspace_id is not None
    # Create mock secrets for both required and optional
    required_secret = BaseSecret(
        name="required_secret",
        owner_id=role.workspace_id,
        encrypted_keys=encrypt_keyvalues(
            [
                SecretKeyValue(key="REQ_KEY1", value=SecretStr("required_value1")),
                SecretKeyValue(key="REQ_KEY2", value=SecretStr("required_value2")),
            ],
            key=os.environ["TRACECAT__DB_ENCRYPTION_KEY"],
        ),
        created_at=datetime.now(),
        updated_at=datetime.now(),
        tags={},
    )

    optional_secret = BaseSecret(
        name="optional_secret",
        owner_id=role.workspace_id,
        encrypted_keys=encrypt_keyvalues(
            [SecretKeyValue(key="OPT_KEY", value=SecretStr("optional_value"))],
            key=os.environ["TRACECAT__DB_ENCRYPTION_KEY"],
        ),
        created_at=datetime.now(),
        updated_at=datetime.now(),
        tags={},
    )

    # Mock SecretsService to return both secrets
    mock_secrets_service = mocker.AsyncMock(spec=SecretsService)
    mock_secrets_service.search_secrets.return_value = [
        required_secret,
        optional_secret,
    ]
    mocker.patch(
        "tracecat.auth.sandbox.SecretsService.with_session",
        return_value=mocker.AsyncMock(
            __aenter__=mocker.AsyncMock(return_value=mock_secrets_service)
        ),
    )

    # Test with both secrets present
    async with AuthSandbox(
        secrets=["required_secret", "optional_secret"],
        optional_secrets=["optional_secret"],
    ) as sandbox:
        # Required secret should be present with all keys
        assert "required_secret" in sandbox.secrets
        assert sandbox.secrets["required_secret"]["REQ_KEY1"] == "required_value1"
        assert sandbox.secrets["required_secret"]["REQ_KEY2"] == "required_value2"

        # Optional secret should also be present when available
        assert "optional_secret" in sandbox.secrets
        assert sandbox.secrets["optional_secret"]["OPT_KEY"] == "optional_value"

    # Verify search was called with both secret names
    mock_secrets_service.search_secrets.assert_called_once_with(
        SecretSearch(
            names={"required_secret", "optional_secret"}, environment="default"
        )
    )


@pytest.mark.anyio
async def test_auth_sandbox_optional_secret_with_all_keys(
    mocker: pytest_mock.MockFixture, test_role
):
    """Test AuthSandbox when optional secret has all required and optional keys."""

    role = ctx_role.get()
    assert role is not None
    assert role.workspace_id is not None
    # Create mock optional secret with all keys
    optional_secret = BaseSecret(
        name="optional_secret",
        owner_id=role.workspace_id,
        encrypted_keys=encrypt_keyvalues(
            [
                SecretKeyValue(key="REQUIRED_KEY1", value=SecretStr("required_value1")),
                SecretKeyValue(key="REQUIRED_KEY2", value=SecretStr("required_value2")),
                SecretKeyValue(key="OPTIONAL_KEY1", value=SecretStr("optional_value1")),
            ],
            key=os.environ["TRACECAT__DB_ENCRYPTION_KEY"],
        ),
        created_at=datetime.now(),
        updated_at=datetime.now(),
        tags={},
    )

    mock_secrets_service = mocker.AsyncMock(spec=SecretsService)
    mock_secrets_service.search_secrets.return_value = [optional_secret]
    mocker.patch(
        "tracecat.auth.sandbox.SecretsService.with_session",
        return_value=mocker.AsyncMock(
            __aenter__=mocker.AsyncMock(return_value=mock_secrets_service)
        ),
    )

    async with AuthSandbox(
        secrets=["optional_secret"],
        optional_secrets=["optional_secret"],
    ) as sandbox:
        assert "optional_secret" in sandbox.secrets
        assert sandbox.secrets["optional_secret"]["REQUIRED_KEY1"] == "required_value1"
        assert sandbox.secrets["optional_secret"]["REQUIRED_KEY2"] == "required_value2"
        assert sandbox.secrets["optional_secret"]["OPTIONAL_KEY1"] == "optional_value1"


@pytest.mark.anyio
async def test_auth_sandbox_missing_optional_secret(
    mocker: pytest_mock.MockFixture, test_role
):
    """Test AuthSandbox when optional secret is completely missing."""
    role = ctx_role.get()
    assert role is not None

    mock_secrets_service = mocker.AsyncMock(spec=SecretsService)
    mock_secrets_service.search_secrets.return_value = []
    mocker.patch(
        "tracecat.auth.sandbox.SecretsService.with_session",
        return_value=mocker.AsyncMock(
            __aenter__=mocker.AsyncMock(return_value=mock_secrets_service)
        ),
    )

    # Should not raise error since secret is optional
    async with AuthSandbox(
        secrets=["optional_secret"],
        optional_secrets=["optional_secret"],
    ) as sandbox:
        assert "optional_secret" not in sandbox.secrets


@pytest.mark.anyio
async def test_auth_sandbox_optional_secret_missing_optional_key(
    mocker: pytest_mock.MockFixture, test_role
):
    """Test AuthSandbox when optional secret has required keys but missing optional keys."""

    role = ctx_role.get()
    assert role is not None
    assert role.workspace_id is not None
    # Create mock optional secret with only required keys
    partial_optional_secret = BaseSecret(
        name="optional_secret",
        owner_id=role.workspace_id,
        encrypted_keys=encrypt_keyvalues(
            [
                SecretKeyValue(key="REQUIRED_KEY1", value=SecretStr("required_value1")),
                SecretKeyValue(key="REQUIRED_KEY2", value=SecretStr("required_value2")),
            ],
            key=os.environ["TRACECAT__DB_ENCRYPTION_KEY"],
        ),
        created_at=datetime.now(),
        updated_at=datetime.now(),
        tags={},
    )

    mock_secrets_service = mocker.AsyncMock(spec=SecretsService)
    mock_secrets_service.search_secrets.return_value = [partial_optional_secret]
    mocker.patch(
        "tracecat.auth.sandbox.SecretsService.with_session",
        return_value=mocker.AsyncMock(
            __aenter__=mocker.AsyncMock(return_value=mock_secrets_service)
        ),
    )

    async with AuthSandbox(
        secrets=["optional_secret"],
        optional_secrets=["optional_secret"],
    ) as sandbox:
        assert "optional_secret" in sandbox.secrets
        assert sandbox.secrets["optional_secret"]["REQUIRED_KEY1"] == "required_value1"
        assert sandbox.secrets["optional_secret"]["REQUIRED_KEY2"] == "required_value2"
        assert "OPTIONAL_KEY1" not in sandbox.secrets["optional_secret"]
