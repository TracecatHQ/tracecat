"""Registration tests for the product-scoped Microsoft OAuth providers.

Microsoft Graph Security and Microsoft Outlook Mail each ship both grants, and the
registry key is what turns a provider id plus grant type into the token variable
the registry SDK wrappers read.
"""

from typing import Literal

import pytest
from tracecat_registry import RegistryOAuthSecret

from tracecat.integrations.enums import OAuthGrantType
from tracecat.integrations.providers import get_provider_class
from tracecat.integrations.providers.microsoft.graph.outlook import (
    MicrosoftOutlookACProvider,
    MicrosoftOutlookCCProvider,
)
from tracecat.integrations.providers.microsoft.graph.security import (
    MicrosoftGraphSecurityACProvider,
    MicrosoftGraphSecurityCCProvider,
)
from tracecat.integrations.schemas import ProviderKey


@pytest.mark.parametrize(
    ("provider_id", "grant_type", "expected_class"),
    [
        (
            "microsoft_graph_security",
            OAuthGrantType.AUTHORIZATION_CODE,
            MicrosoftGraphSecurityACProvider,
        ),
        (
            "microsoft_graph_security",
            OAuthGrantType.CLIENT_CREDENTIALS,
            MicrosoftGraphSecurityCCProvider,
        ),
        (
            "microsoft_outlook",
            OAuthGrantType.AUTHORIZATION_CODE,
            MicrosoftOutlookACProvider,
        ),
        (
            "microsoft_outlook",
            OAuthGrantType.CLIENT_CREDENTIALS,
            MicrosoftOutlookCCProvider,
        ),
    ],
)
def test_both_grants_are_registered(
    provider_id: str, grant_type: OAuthGrantType, expected_class: type
) -> None:
    key = ProviderKey(id=provider_id, grant_type=grant_type)
    assert get_provider_class(key) is expected_class


@pytest.mark.parametrize(
    ("provider_id", "grant_type", "expected_token_name"),
    [
        (
            "microsoft_graph_security",
            "client_credentials",
            "MICROSOFT_GRAPH_SECURITY_SERVICE_TOKEN",
        ),
        (
            "microsoft_graph_security",
            "authorization_code",
            "MICROSOFT_GRAPH_SECURITY_USER_TOKEN",
        ),
        ("microsoft_outlook", "client_credentials", "MICROSOFT_OUTLOOK_SERVICE_TOKEN"),
        ("microsoft_outlook", "authorization_code", "MICROSOFT_OUTLOOK_USER_TOKEN"),
    ],
)
def test_provider_ids_derive_the_expected_token_names(
    provider_id: str,
    grant_type: Literal["authorization_code", "client_credentials"],
    expected_token_name: str,
) -> None:
    secret = RegistryOAuthSecret(
        provider_id=provider_id,
        grant_type=grant_type,
        optional=True,
    )
    assert secret.token_name == expected_token_name
    assert secret.name == f"{provider_id}_oauth"


def test_outlook_application_setup_instructions_cover_exchange_scoping() -> None:
    instructions = MicrosoftOutlookCCProvider.metadata.setup_instructions
    assert instructions is not None
    assert "Entra app registration" in instructions
    assert "application-rbac" in instructions


def test_outlook_delegated_setup_instructions_cover_shared_mailboxes() -> None:
    instructions = MicrosoftOutlookACProvider.metadata.setup_instructions
    assert instructions is not None
    assert "Mail.Send.Shared" in instructions
    assert "Send As" in instructions
    assert "Full Access" in instructions


@pytest.mark.parametrize(
    "provider_class",
    [
        MicrosoftGraphSecurityACProvider,
        MicrosoftGraphSecurityCCProvider,
        MicrosoftOutlookACProvider,
        MicrosoftOutlookCCProvider,
    ],
)
def test_product_setup_instructions_cover_national_cloud_audiences(
    provider_class: type,
) -> None:
    instructions = provider_class.metadata.setup_instructions
    assert instructions is not None
    assert "graph.microsoft.us" in instructions
    assert "dod-graph.microsoft.us" in instructions
    assert "microsoftgraph.chinacloudapi.cn" in instructions
    assert "login.chinacloudapi.cn" in instructions
    assert "not interchangeable" in instructions
