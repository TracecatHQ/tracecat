import asyncio
import os
from collections.abc import Callable
from datetime import datetime
from unittest.mock import AsyncMock
from uuid import UUID

import pytest
import pytest_mock
from pydantic import SecretStr

from tracecat.auth.credentials import TemporaryRole
from tracecat.auth.sandbox import AuthSandbox
from tracecat.auth.types import Role
from tracecat.authz.scopes import SERVICE_PRINCIPAL_SCOPES
from tracecat.contexts import ctx_role, get_env
from tracecat.db.models import Secret
from tracecat.exceptions import TracecatCredentialsError
from tracecat.secrets import secrets_manager
from tracecat.secrets.encryption import encrypt_keyvalues
from tracecat.secrets.schemas import (
    SecretCreate,
    SecretKeyValue,
    SecretSearch,
)
from tracecat.secrets.service import SecretsService


@pytest.mark.anyio
async def test_auth_sandbox_with_secrets(mocker: pytest_mock.MockFixture, test_role):
    from datetime import datetime

    role = ctx_role.get()
    assert role is not None
    assert role.workspace_id is not None

    mock_secret_keys = [
        SecretKeyValue(key="SECRET_KEY", value=SecretStr("my_secret_key"))
    ]

    mock_secret = Secret(
        name="my_secret",
        workspace_id=role.workspace_id,
        encrypted_keys=encrypt_keyvalues(
            mock_secret_keys, key=os.environ["TRACECAT__DB_ENCRYPTION_KEY"]
        ),
        created_at=datetime.now(),
        updated_at=datetime.now(),
        tags={},
    )

    mock_get_secrets = mocker.patch.object(
        AuthSandbox, "_get_secrets", return_value=[mock_secret]
    )

    async with AuthSandbox(secrets=["my_secret"]) as sandbox:
        assert sandbox.secrets == {"my_secret": {"SECRET_KEY": "my_secret_key"}}

    mock_get_secrets.assert_called_once()


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
                user_id=mock_user_id,
                service_id="tracecat-service",
                scopes=SERVICE_PRINCIPAL_SCOPES["tracecat-service"],
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


def _secret(name: str, workspace_id: UUID, keys: dict[str, str]) -> Secret:
    return Secret(
        name=name,
        workspace_id=workspace_id,
        encrypted_keys=encrypt_keyvalues(
            [SecretKeyValue(key=k, value=SecretStr(v)) for k, v in keys.items()],
            key=os.environ["TRACECAT__DB_ENCRYPTION_KEY"],
        ),
        created_at=datetime.now(),
        updated_at=datetime.now(),
        tags={},
    )


@pytest.fixture
def secrets_service_returning(
    mocker: pytest_mock.MockFixture,
) -> Callable[..., AsyncMock]:
    def _patch(*secrets: Secret) -> AsyncMock:
        service = mocker.AsyncMock(spec=SecretsService)
        service.search_secrets.return_value = list(secrets)
        mocker.patch(
            "tracecat.auth.sandbox.SecretsService.with_session",
            return_value=mocker.AsyncMock(
                __aenter__=mocker.AsyncMock(return_value=service)
            ),
        )
        return service

    return _patch


@pytest.mark.anyio
async def test_auth_sandbox_optional_secrets(
    secrets_service_returning: Callable[..., AsyncMock], test_role
):
    """Test that AuthSandbox handles both required and optional secrets correctly."""

    role = ctx_role.get()
    assert role is not None
    assert role.workspace_id is not None
    mock_secrets_service = secrets_service_returning(
        _secret("required_secret", role.workspace_id, {"REQ_KEY": "required_value"})
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

    error_message = str(exc_info.value)
    assert "required_secret" in error_message
    assert "Missing" in error_message
    assert {
        "secret_name": "required_secret",
        "environment": "default",
    } in (exc_info.value.detail or [])


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("requested", "available", "expected"),
    [
        pytest.param(
            ["required_secret", "optional_secret"],
            {
                "required_secret": {
                    "REQ_KEY1": "required_value1",
                    "REQ_KEY2": "required_value2",
                },
                "optional_secret": {"OPT_KEY": "optional_value"},
            },
            {
                "required_secret": {
                    "REQ_KEY1": "required_value1",
                    "REQ_KEY2": "required_value2",
                },
                "optional_secret": {"OPT_KEY": "optional_value"},
            },
            id="all_secrets_present",
        ),
        pytest.param(
            ["optional_secret"],
            {
                "optional_secret": {
                    "REQUIRED_KEY1": "required_value1",
                    "REQUIRED_KEY2": "required_value2",
                    "OPTIONAL_KEY1": "optional_value1",
                }
            },
            {
                "optional_secret": {
                    "REQUIRED_KEY1": "required_value1",
                    "REQUIRED_KEY2": "required_value2",
                    "OPTIONAL_KEY1": "optional_value1",
                }
            },
            id="optional_secret_with_all_keys",
        ),
        pytest.param(
            ["optional_secret"],
            {},
            {},
            id="missing_optional_secret",
        ),
        pytest.param(
            ["optional_secret"],
            {
                "optional_secret": {
                    "REQUIRED_KEY1": "required_value1",
                    "REQUIRED_KEY2": "required_value2",
                }
            },
            {
                "optional_secret": {
                    "REQUIRED_KEY1": "required_value1",
                    "REQUIRED_KEY2": "required_value2",
                }
            },
            id="optional_secret_missing_optional_key",
        ),
    ],
)
async def test_auth_sandbox_resolves_available_optional_secrets(
    secrets_service_returning: Callable[..., AsyncMock],
    test_role,
    requested: list[str],
    available: dict[str, dict[str, str]],
    expected: dict[str, dict[str, str]],
):
    role = ctx_role.get()
    assert role is not None
    assert role.workspace_id is not None
    workspace_id = role.workspace_id
    mock_secrets_service = secrets_service_returning(
        *(_secret(name, workspace_id, keys) for name, keys in available.items())
    )

    async with AuthSandbox(
        secrets=requested,
        optional_secrets=["optional_secret"],
    ) as sandbox:
        assert sandbox.secrets == expected

    mock_secrets_service.search_secrets.assert_called_once_with(
        SecretSearch(names=set(requested), environment="default")
    )
