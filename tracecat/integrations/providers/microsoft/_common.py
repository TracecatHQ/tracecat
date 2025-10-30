"""Shared helpers and base classes for Microsoft OAuth providers."""

from __future__ import annotations

from typing import Final

MICROSOFT_CLOUD_AUTHORITIES: Final[dict[str, str]] = {
    "public": "https://login.microsoftonline.com",
    "us_gov": "https://login.microsoftonline.us",
}
MICROSOFT_OAUTH_PATH: Final[str] = "oauth2/v2.0"

TENANT_ID_HELP: Final[str] = (
    "Replace {tenant_id} with your Entra ID app's tenant (directory) ID"
    "or use `common` for multi-tenant applications, `organizations` for single-tenant applications, or `consumers` for personal accounts"
)
MICROSOFT_AUTH_ENDPOINT_HELP: Final[str] = (
    "Cloud endpoints:\n"
    "- Public: https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/authorize\n"
    "- US Gov: https://login.microsoftonline.us/{tenant_id}/oauth2/v2.0/authorize\n"
) + TENANT_ID_HELP

MICROSOFT_TOKEN_ENDPOINT_HELP: Final[str] = (
    "Cloud endpoints:\n"
    "- Public: https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token\n"
    "- US Gov: https://login.microsoftonline.us/{tenant_id}/oauth2/v2.0/token\n"
) + TENANT_ID_HELP


def get_ac_setup_steps(service: str = "Microsoft service") -> list[str]:
    """Reusable setup steps for Authorization Code flow providers."""

    return [
        "Register your application in Azure Portal",
        "Add the redirect URI shown above to 'Redirect URIs'",
        f"Configure required API permissions for {service}",
        "Copy Client ID and Client Secret",
        (
            "Configure the authorization and token endpoints for your tenant "
            "(defaults use the Azure Public cloud with the class-specific tenant)"
        ),
    ]


def get_cc_setup_steps(service: str = "Microsoft service") -> list[str]:
    """Reusable setup steps for Client Credentials flow providers."""

    return [
        "Register your application in Azure Portal",
        f"Configure API permissions for {service} with Application permissions (not Delegated)",
        "Grant admin consent for the application permissions",
        "Copy Client ID and Client Secret",
        (
            "Configure the authorization and token endpoints for your tenant "
            "(defaults use the Azure Public cloud with the class-specific tenant)"
        ),
    ]


DEFAULT_AUTHORIZATION_ENDPOINT: Final[str] = (
    "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
)
DEFAULT_TOKEN_ENDPOINT: Final[str] = (
    "https://login.microsoftonline.com/common/oauth2/v2.0/token"
)
