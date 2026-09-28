"""Registration and scope tests for the product-scoped Microsoft OAuth providers.

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

GRAPH_SECURITY_DELEGATED_SCOPES = [
    "offline_access",
    "https://graph.microsoft.com/SecurityAlert.ReadWrite.All",
    "https://graph.microsoft.com/SecurityIncident.ReadWrite.All",
    "https://graph.microsoft.com/SecurityEvents.Read.All",
    "https://graph.microsoft.com/ThreatHunting.Read.All",
    "https://graph.microsoft.com/ThreatIntelligence.Read.All",
    "https://graph.microsoft.com/AuditLogsQuery-Entra.Read.All",
    "https://graph.microsoft.com/SecurityData.Manage.All",
]

OUTLOOK_DELEGATED_SCOPES = [
    "offline_access",
    "https://graph.microsoft.com/User.Read",
    "https://graph.microsoft.com/Mail.ReadWrite",
    "https://graph.microsoft.com/Mail.ReadWrite.Shared",
    "https://graph.microsoft.com/Mail.Send",
    "https://graph.microsoft.com/Mail.Send.Shared",
    "https://graph.microsoft.com/MailboxSettings.ReadWrite",
]

APPLICATION_SCOPES = ["https://graph.microsoft.com/.default"]


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


@pytest.mark.parametrize(
    ("provider_class", "expected_scopes"),
    [
        (MicrosoftGraphSecurityACProvider, GRAPH_SECURITY_DELEGATED_SCOPES),
        (MicrosoftGraphSecurityCCProvider, APPLICATION_SCOPES),
        (MicrosoftOutlookACProvider, OUTLOOK_DELEGATED_SCOPES),
        (MicrosoftOutlookCCProvider, APPLICATION_SCOPES),
    ],
)
def test_default_scopes(provider_class: type, expected_scopes: list[str]) -> None:
    assert provider_class.scopes.default == expected_scopes


@pytest.mark.parametrize(
    ("provider_class", "expected_name"),
    [
        (MicrosoftGraphSecurityACProvider, "Microsoft Graph Security (Delegated)"),
        (
            MicrosoftGraphSecurityCCProvider,
            "Microsoft Graph Security (Service account)",
        ),
        (MicrosoftOutlookACProvider, "Microsoft Outlook Mail (Delegated)"),
        (MicrosoftOutlookCCProvider, "Microsoft Outlook Mail (Service account)"),
    ],
)
def test_provider_display_names(provider_class: type, expected_name: str) -> None:
    assert provider_class.metadata.name == expected_name


@pytest.mark.parametrize(
    "provider_class", [MicrosoftOutlookACProvider, MicrosoftOutlookCCProvider]
)
def test_outlook_metadata_points_at_the_mail_api_overview(
    provider_class: type,
) -> None:
    assert provider_class.metadata.api_docs_url == (
        "https://learn.microsoft.com/en-us/graph/api/resources/"
        "mail-api-overview?view=graph-rest-1.0"
    )


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
