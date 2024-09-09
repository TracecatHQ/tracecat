import os

import pytest
import pytest_mock

from tracecat.auth.credentials import TemporaryRole
from tracecat.auth.sandbox import AuthSandbox
from tracecat.contexts import ctx_role
from tracecat.db.schemas import Secret
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
