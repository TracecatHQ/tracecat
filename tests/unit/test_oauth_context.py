from contextlib import asynccontextmanager

import pytest
from pydantic import SecretStr
from tracecat_registry import RegistryOAuthSecret

from tracecat.executor.service import (
    build_legacy_oauth_context,
    build_registry_oauth_context,
    merge_oauth_contexts,
)
from tracecat.integrations.enums import OAuthGrantType
from tracecat.secrets.secrets_manager import get_oauth_context
from tracecat.types.exceptions import TracecatCredentialsError


class DummyIntegration:
    def __init__(self, provider_id: str, grant_type: OAuthGrantType) -> None:
        self.provider_id = provider_id
        self.grant_type = grant_type


@pytest.mark.anyio
async def test_get_oauth_context_builds_structure(mocker):
    integrations = [
        DummyIntegration("github", OAuthGrantType.AUTHORIZATION_CODE),
        DummyIntegration("okta", OAuthGrantType.CLIENT_CREDENTIALS),
    ]

    service = mocker.AsyncMock()
    service.list_integrations.return_value = integrations
    service.refresh_token_if_needed.side_effect = integrations
    service.get_access_token.side_effect = [
        SecretStr("github-user-token"),
        SecretStr("okta-service-token"),
    ]

    @asynccontextmanager
    async def service_cm():
        yield service

    mocker.patch(
        "tracecat.secrets.secrets_manager.IntegrationService.with_session",
        return_value=service_cm(),
    )

    context = await get_oauth_context({"github.USER_TOKEN", "okta.SERVICE_TOKEN"})

    assert context == {
        "github": {"USER_TOKEN": "github-user-token"},
        "okta": {"SERVICE_TOKEN": "okta-service-token"},
    }
    assert service.refresh_token_if_needed.await_count == 2
    assert service.get_access_token.await_count == 2


@pytest.mark.anyio
async def test_get_oauth_context_missing_required_integration_raises(mocker):
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
        await get_oauth_context({"github.USER_TOKEN"})


@pytest.mark.anyio
async def test_get_oauth_context_ignores_invalid_expression(mocker):
    integrations = [DummyIntegration("github", OAuthGrantType.AUTHORIZATION_CODE)]

    service = mocker.AsyncMock()
    service.list_integrations.return_value = integrations
    service.refresh_token_if_needed.side_effect = integrations
    service.get_access_token.return_value = SecretStr("token")

    @asynccontextmanager
    async def service_cm():
        yield service

    mocker.patch(
        "tracecat.secrets.secrets_manager.IntegrationService.with_session",
        return_value=service_cm(),
    )

    # Include an invalid expression alongside a valid one
    context = await get_oauth_context({"github.USER_TOKEN", "invalid-expression"})

    assert context == {"github": {"USER_TOKEN": "token"}}


def test_build_registry_oauth_context_merges_tokens():
    secrets = {
        "github": {"GITHUB_USER_TOKEN": "user-token"},
        "okta": {"OKTA_SERVICE_TOKEN": "service-token"},
    }
    action_secrets: set[RegistryOAuthSecret] = {
        RegistryOAuthSecret(provider_id="github", grant_type="authorization_code"),
        RegistryOAuthSecret(provider_id="okta", grant_type="client_credentials"),
    }

    context = build_registry_oauth_context(
        secrets=secrets, action_secrets=action_secrets
    )

    assert context == {
        "github": {"USER_TOKEN": "user-token"},
        "okta": {"SERVICE_TOKEN": "service-token"},
    }


def test_build_legacy_oauth_context():
    secrets = {
        "microsoft_entra_oauth": {
            "MICROSOFT_ENTRA_USER_TOKEN": "user-token",
            "MICROSOFT_ENTRA_SERVICE_TOKEN": "svc-token",
        },
        "linear": {
            "LINEAR_SERVICE_TOKEN": "linear-svc",
            "LINEAR_USER_TOKEN": "linear-user",
        },
        "github": {"GITHUB_USER_TOKEN": "fresh-token"},
    }

    context = build_legacy_oauth_context(secrets)

    assert context == {
        "microsoft_entra": {
            "USER_TOKEN": "user-token",
            "SERVICE_TOKEN": "svc-token",
        },
        "linear": {
            "SERVICE_TOKEN": "linear-svc",
            "USER_TOKEN": "linear-user",
        },
        "github": {"USER_TOKEN": "fresh-token"},
    }


def test_merge_oauth_contexts_overwrites_by_priority():
    base = {"github": {"USER_TOKEN": "base"}}
    override = {"github": {"USER_TOKEN": "override", "SERVICE_TOKEN": "svc"}}
    merged = merge_oauth_contexts(base, override)

    assert merged == {"github": {"USER_TOKEN": "override", "SERVICE_TOKEN": "svc"}}
