from contextlib import asynccontextmanager

import pytest
from pydantic import SecretStr
from tracecat_registry import (
    RegistryOAuthSecret,
    RegistrySecret,
    RegistrySecretType,
)

from tracecat.exceptions import TracecatCredentialsError
from tracecat.integrations.enums import OAuthGrantType
from tracecat.secrets import secrets_manager


@pytest.mark.anyio
async def test_get_action_secrets_passes_sets_to_auth_sandbox(mocker):
    """Test that get_action_secrets correctly passes secrets as sets to AuthSandbox."""
    # Create registry secrets with both required and optional
    action_secrets: set[RegistrySecretType] = {
        RegistrySecret(name="required_secret1", keys=["REQ_KEY1"], optional=False),
        RegistrySecret(name="required_secret2", keys=["REQ_KEY2"], optional=False),
        RegistrySecret(name="optional_secret1", keys=["OPT_KEY1"], optional=True),
        RegistrySecret(name="optional_secret2", keys=["OPT_KEY2"], optional=True),
    }

    # Mock templated secrets from args
    mocker.patch(
        "tracecat.expressions.eval.extract_templated_secrets",
        return_value=["args_secret1", "args_secret2"],
    )
    mocker.patch(
        "tracecat.secrets.secrets_manager.get_runtime_env", return_value="test_env"
    )

    # Mock AuthSandbox to capture call arguments
    mock_sandbox = mocker.MagicMock()
    mock_sandbox.secrets = {}
    mock_sandbox.__aenter__.return_value = mock_sandbox
    mock_sandbox.__aexit__.return_value = None

    auth_sandbox_mock = mocker.patch("tracecat.secrets.secrets_manager.AuthSandbox")
    auth_sandbox_mock.return_value = mock_sandbox

    # Run the function
    await secrets_manager.get_action_secrets(
        secret_exprs={"args_secret1", "args_secret2"}, action_secrets=action_secrets
    )

    # Verify AuthSandbox was called with sets, not lists
    auth_sandbox_mock.assert_called_once()
    _call_args, call_kwargs = auth_sandbox_mock.call_args

    # Verify that secrets parameter is a set
    assert isinstance(call_kwargs["secrets"], set)
    expected_secrets = {
        "required_secret1",
        "required_secret2",
        "optional_secret1",
        "optional_secret2",
        "args_secret1",
        "args_secret2",
    }
    assert call_kwargs["secrets"] == expected_secrets

    # Verify that optional_secrets parameter is a set
    assert isinstance(call_kwargs["optional_secrets"], set)
    expected_optional_secrets = {"optional_secret1", "optional_secret2"}
    assert call_kwargs["optional_secrets"] == expected_optional_secrets

    # Verify environment parameter
    assert call_kwargs["environment"] == "test_env"


@pytest.mark.anyio
async def test_get_action_secrets_skips_optional_oauth(mocker):
    """Ensure optional OAuth integrations do not raise when missing."""

    action_secrets: set[RegistrySecretType] = {
        RegistryOAuthSecret(
            provider_id="azure_log_analytics",
            grant_type="authorization_code",
        ),
        RegistryOAuthSecret(
            provider_id="azure_log_analytics",
            grant_type="client_credentials",
            optional=True,
        ),
    }

    mocker.patch("tracecat.expressions.eval.extract_templated_secrets", return_value=[])
    mocker.patch(
        "tracecat.secrets.secrets_manager.get_runtime_env", return_value="test_env"
    )

    sandbox = mocker.AsyncMock()
    sandbox.secrets = {}
    sandbox.__aenter__.return_value = sandbox
    sandbox.__aexit__.return_value = None
    mocker.patch("tracecat.secrets.secrets_manager.AuthSandbox", return_value=sandbox)

    delegated_integration = mocker.MagicMock()
    delegated_integration.provider_id = "azure_log_analytics"
    delegated_integration.grant_type = OAuthGrantType.AUTHORIZATION_CODE

    service = mocker.AsyncMock()
    service.list_integrations.return_value = [delegated_integration]
    service.refresh_token_if_needed.return_value = delegated_integration
    service.get_access_token.return_value = SecretStr("user-token")

    @asynccontextmanager
    async def service_cm():
        yield service

    mocker.patch(
        "tracecat.secrets.secrets_manager.IntegrationService.with_session",
        return_value=service_cm(),
    )

    secrets = await secrets_manager.get_action_secrets(
        secret_exprs=set(), action_secrets=action_secrets
    )
    assert (
        secrets["azure_log_analytics_oauth"]["AZURE_LOG_ANALYTICS_USER_TOKEN"]
        == "user-token"
    )
    assert (
        "AZURE_LOG_ANALYTICS_SERVICE_TOKEN" not in secrets["azure_log_analytics_oauth"]
    )


@pytest.mark.anyio
async def test_get_action_secrets_merges_multiple_oauth_tokens(mocker):
    """Ensure both delegated and service tokens are returned when available."""

    action_secrets: set[RegistrySecretType] = {
        RegistryOAuthSecret(
            provider_id="azure_log_analytics",
            grant_type="authorization_code",
        ),
        RegistryOAuthSecret(
            provider_id="azure_log_analytics",
            grant_type="client_credentials",
            optional=True,
        ),
    }

    mocker.patch("tracecat.expressions.eval.extract_templated_secrets", return_value=[])
    mocker.patch(
        "tracecat.secrets.secrets_manager.get_runtime_env", return_value="test_env"
    )

    sandbox = mocker.AsyncMock()
    sandbox.secrets = {}
    sandbox.__aenter__.return_value = sandbox
    sandbox.__aexit__.return_value = None
    mocker.patch("tracecat.secrets.secrets_manager.AuthSandbox", return_value=sandbox)

    delegated_integration = mocker.MagicMock()
    delegated_integration.provider_id = "azure_log_analytics"
    delegated_integration.grant_type = OAuthGrantType.AUTHORIZATION_CODE

    service_integration = mocker.MagicMock()
    service_integration.provider_id = "azure_log_analytics"
    service_integration.grant_type = OAuthGrantType.CLIENT_CREDENTIALS

    service = mocker.AsyncMock()
    service.list_integrations.return_value = [
        delegated_integration,
        service_integration,
    ]
    service.refresh_token_if_needed.side_effect = lambda integration: integration

    def _get_access_token(integration):
        if integration.grant_type == OAuthGrantType.AUTHORIZATION_CODE:
            return SecretStr("user-token")
        if integration.grant_type == OAuthGrantType.CLIENT_CREDENTIALS:
            return SecretStr("service-token")
        return None

    service.get_access_token.side_effect = _get_access_token

    @asynccontextmanager
    async def service_cm():
        yield service

    mocker.patch(
        "tracecat.secrets.secrets_manager.IntegrationService.with_session",
        return_value=service_cm(),
    )

    secrets = await secrets_manager.get_action_secrets(
        secret_exprs=set(), action_secrets=action_secrets
    )
    assert (
        secrets["azure_log_analytics_oauth"]["AZURE_LOG_ANALYTICS_USER_TOKEN"]
        == "user-token"
    )
    assert (
        secrets["azure_log_analytics_oauth"]["AZURE_LOG_ANALYTICS_SERVICE_TOKEN"]
        == "service-token"
    )


@pytest.mark.anyio
async def test_get_action_secrets_missing_required_oauth_raises(mocker):
    """Required OAuth integrations should surface a credentials error."""

    action_secrets: set[RegistrySecretType] = {
        RegistryOAuthSecret(
            provider_id="azure_log_analytics",
            grant_type="authorization_code",
        )
    }

    mocker.patch("tracecat.expressions.eval.extract_templated_secrets", return_value=[])
    mocker.patch(
        "tracecat.secrets.secrets_manager.get_runtime_env", return_value="test_env"
    )

    sandbox = mocker.AsyncMock()
    sandbox.secrets = {}
    sandbox.__aenter__.return_value = sandbox
    sandbox.__aexit__.return_value = None
    mocker.patch("tracecat.secrets.secrets_manager.AuthSandbox", return_value=sandbox)

    service = mocker.AsyncMock()
    service.list_integrations.return_value = []

    @asynccontextmanager
    async def service_cm():
        yield service

    mocker.patch(
        "tracecat.secrets.secrets_manager.IntegrationService.with_session",
        return_value=service_cm(),
    )

    with pytest.raises(TracecatCredentialsError):
        await secrets_manager.get_action_secrets(
            secret_exprs=set(), action_secrets=action_secrets
        )


@pytest.mark.anyio
async def test_extract_templated_secrets_detects_nested_complex_expressions():
    from tracecat.expressions.eval import extract_templated_secrets

    expr = '${{ FN.to_base64(SECRETS.zendesk.ZENDESK_EMAIL + "/token:" + SECRETS.zendesk.ZENDESK_API_TOKEN) }}'
    secrets = extract_templated_secrets(expr)
    assert sorted(secrets) == sorted(
        [
            "zendesk.ZENDESK_EMAIL",
            "zendesk.ZENDESK_API_TOKEN",
        ]
    )
