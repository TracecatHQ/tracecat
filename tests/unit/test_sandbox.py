import os

import httpx
import pytest
import pytest_mock

from tracecat.auth.credentials import TemporaryRole
from tracecat.auth.sandbox import AuthSandbox
from tracecat.contexts import ctx_role
from tracecat.db.schemas import Secret
from tracecat.secrets.encryption import encrypt_keyvalues
from tracecat.secrets.models import SecretKeyValue
from tracecat.types.auth import Role


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

    # Mock httpx.Response
    mock_response = mocker.Mock(spec=httpx.Response)
    mock_response.raise_for_status = mocker.Mock()
    mock_response.content = mock_secret.model_dump_json().encode()

    mock_client = mocker.AsyncMock()
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None
    mock_client.get.return_value = mock_response

    # Patch the AuthenticatedAPIClient to return the mock client
    mocker.patch(
        "tracecat.auth.sandbox.AuthenticatedAPIClient", return_value=mock_client
    )

    async with AuthSandbox(secrets=["my_secret"], target="context") as sandbox:
        assert sandbox.secrets == {"my_secret": {"SECRET_KEY": "my_secret_key"}}

    # Assert that the secrets API was called with the correct parameters
    mock_client.get.assert_called_once_with("/secrets/my_secret")

    # Assert that raise_for_status was called
    mock_response.raise_for_status.assert_called_once()


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
