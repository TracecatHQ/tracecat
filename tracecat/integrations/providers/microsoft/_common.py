"""Shared helpers and constants for Microsoft OAuth providers.

This module centralizes common Microsoft identity platform details:
- Default authorization and token endpoints (Public cloud)
- Cloud endpoint help text (Public, US Gov, China, Germany)
- Reusable setup steps for AC and CC flows
"""

from __future__ import annotations

from typing import Final

# Default endpoints for Microsoft identity platform (Public cloud)
DEFAULT_COMMERCIAL_AUTHORIZATION_ENDPOINT: Final[str] = (
    "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
)
DEFAULT_COMMERCIAL_TOKEN_ENDPOINT: Final[str] = (
    "https://login.microsoftonline.com/common/oauth2/v2.0/token"
)


# Help text referencing sovereign cloud endpoint patterns. Tenants should replace
# {tenant} with a specific tenant ID or domain for production.
MICROSOFT_AUTH_ENDPOINT_HELP: Final[str] = (
    "Cloud endpoints:\n"
    "- Public: https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize\n"
    "- US Gov: https://login.microsoftonline.us/{tenant}/oauth2/v2.0/authorize\n"
)

MICROSOFT_TOKEN_ENDPOINT_HELP: Final[str] = (
    "Cloud endpoints:\n"
    "- Public: https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token\n"
    "- US Gov: https://login.microsoftonline.us/{tenant}/oauth2/v2.0/token\n"
)


def get_ac_setup_steps(service: str = "Microsoft service") -> list[str]:
    """Reusable setup steps for Authorization Code flow providers.

    Args:
        service: A short label for the target service (e.g., "Microsoft Graph").
    """

    return [
        "Register your application in Azure Portal",
        "Add the redirect URI shown above to 'Redirect URIs'",
        f"Configure required API permissions for {service}",
        "Copy Client ID and Client Secret",
        (
            "Configure the authorization and token endpoints for your tenant "
            "(defaults use the Azure Public cloud with 'common')"
        ),
    ]


def get_cc_setup_steps(service: str = "Microsoft service") -> list[str]:
    """Reusable setup steps for Client Credentials flow providers.

    Args:
        service: A short label for the target service (e.g., "Azure Management").
    """

    return [
        "Register your application in Azure Portal",
        f"Configure API permissions for {service} with Application permissions (not Delegated)",
        "Grant admin consent for the application permissions",
        "Copy Client ID and Client Secret",
        (
            "Configure the authorization and token endpoints for your tenant "
            "(defaults use the Azure Public cloud with 'common')"
        ),
    ]
