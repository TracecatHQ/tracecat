import httpx
import pytest
import pytest_mock

from tracecat.auth.sandbox import AuthSandbox
from tracecat.contexts import ctx_role
from tracecat.db.schemas import Secret
from tracecat.types.auth import Role
from tracecat.types.secrets import SecretKeyValue


@pytest.mark.asyncio
async def test_auth_sandbox_with_secrets(mocker: pytest_mock.MockFixture, auth_sandbox):
    role = ctx_role.get()
    assert role is not None

    mock_secret_keys = [SecretKeyValue(key="SECRET_KEY", value="my_secret_key")]
    mock_secret = Secret(name="my_secret", owner_id=role.user_id)
    mock_secret.keys = mock_secret_keys

    mock_client = mocker.AsyncMock()
    mock_client.get.return_value = httpx.Response(
        200, content=mock_secret.model_dump_json().encode()
    )
    mock_client.__aenter__.return_value = mock_client
    mock_client.__aexit__.return_value = None

    # Patch the AuthenticatedAPIClient to return the mock client
    mocker.patch(
        "tracecat.auth.sandbox.AuthenticatedAPIClient", return_value=mock_client
    )

    async with AuthSandbox(secrets=["my_secret"], target="context") as sandbox:
        assert sandbox.secrets == {"my_secret": {"SECRET_KEY": "my_secret_key"}}

    # Assert that the secrets API was called with the correct parameters
    mock_client.get.assert_called_once_with("/secrets/my_secret")


@pytest.mark.asyncio
async def test_auth_sandbox_without_secrets(auth_sandbox):
    async with AuthSandbox() as sandbox:
        assert sandbox.secrets == {}
        assert sandbox._role == Role(
            type="service", user_id="test-tracecat-user", service_id="tracecat-testing"
        )
