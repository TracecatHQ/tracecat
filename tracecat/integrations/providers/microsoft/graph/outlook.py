"""Microsoft Outlook Mail OAuth integration built on Microsoft Graph providers."""

from typing import ClassVar

from tracecat.integrations.providers.microsoft.graph.provider import (
    GRAPH_NATIONAL_CLOUD_SETUP_INSTRUCTIONS,
    MicrosoftGraphACProvider,
    MicrosoftGraphCCProvider,
    get_graph_ac_metadata,
    get_graph_cc_metadata,
)
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes

OUTLOOK_API_DOCS_URL = "https://learn.microsoft.com/en-us/graph/api/resources/mail-api-overview?view=graph-rest-1.0"
OUTLOOK_AC_SETUP_GUIDE_URL = (
    "https://learn.microsoft.com/en-us/graph/outlook-send-mail-from-other-user"
)
OUTLOOK_CC_SETUP_GUIDE_URL = (
    "https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac"
)

OUTLOOK_AC_SETUP_INSTRUCTIONS = (
    "Delegated access uses the signed-in user's own mailbox by default. To read or "
    "write another user's mailbox, that user must share the mailbox or grant "
    "delegate access, and the app needs the `.Shared` variants "
    "(Mail.ReadWrite.Shared, Mail.Send.Shared). Sending as or on behalf of another "
    "mailbox additionally requires the Exchange Send As or Send on Behalf mailbox "
    "permission, and addressing the mailbox through `/users/{id}` also requires "
    "Full Access. See "
    "https://learn.microsoft.com/en-us/graph/outlook-send-mail-from-other-user. "
    + GRAPH_NATIONAL_CLOUD_SETUP_INSTRUCTIONS
)
OUTLOOK_CC_SETUP_INSTRUCTIONS = (
    "Application permissions are configured on the Microsoft Entra app registration "
    "and grant tenant-wide mailbox access by default: an app with Mail.Send can send "
    "as any user in the organization. Restrict the app to specific mailboxes with "
    "RBAC for Applications in Exchange Online "
    "(https://learn.microsoft.com/en-us/exchange/permissions-exo/application-rbac), "
    "which replaces the legacy application access policies "
    "(https://learn.microsoft.com/en-us/exchange/permissions-exo/application-access-policies). "
    + GRAPH_NATIONAL_CLOUD_SETUP_INSTRUCTIONS
)


class MicrosoftOutlookACProvider(MicrosoftGraphACProvider):
    """Microsoft Outlook Mail OAuth provider for delegated permissions."""

    id: ClassVar[str] = "microsoft_outlook"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "offline_access",
            "https://graph.microsoft.com/User.Read",
            "https://graph.microsoft.com/Mail.ReadWrite",
            "https://graph.microsoft.com/Mail.ReadWrite.Shared",
            "https://graph.microsoft.com/Mail.Send",
            "https://graph.microsoft.com/Mail.Send.Shared",
            "https://graph.microsoft.com/MailboxSettings.ReadWrite",
        ],
    )
    metadata: ClassVar[ProviderMetadata] = get_graph_ac_metadata(
        id="microsoft_outlook",
        name="Microsoft Outlook Mail",
        api_docs_url=OUTLOOK_API_DOCS_URL,
        setup_guide_url=OUTLOOK_AC_SETUP_GUIDE_URL,
        setup_instructions=OUTLOOK_AC_SETUP_INSTRUCTIONS,
    )


class MicrosoftOutlookCCProvider(MicrosoftGraphCCProvider):
    """Microsoft Outlook Mail OAuth provider for application permissions."""

    id: ClassVar[str] = "microsoft_outlook"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=["https://graph.microsoft.com/.default"],
    )
    metadata: ClassVar[ProviderMetadata] = get_graph_cc_metadata(
        id="microsoft_outlook",
        name="Microsoft Outlook Mail",
        api_docs_url=OUTLOOK_API_DOCS_URL,
        setup_guide_url=OUTLOOK_CC_SETUP_GUIDE_URL,
        setup_instructions=OUTLOOK_CC_SETUP_INSTRUCTIONS,
    )
