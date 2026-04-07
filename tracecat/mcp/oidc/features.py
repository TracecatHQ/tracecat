"""Feature gating helpers for the internal MCP OIDC issuer."""

from __future__ import annotations

from tracecat import config

OFFLINE_ACCESS_SCOPE = "offline_access"
_BASE_OIDC_SCOPES: tuple[str, ...] = ("openid", "profile", "email")


def refresh_tokens_enabled() -> bool:
    """Return whether refresh-token support is enabled for this deployment."""
    return bool(config.TRACECAT__DB_ENCRYPTION_KEY)


def get_supported_scopes() -> list[str]:
    """Return the OIDC scopes this deployment currently supports."""
    scopes: list[str] = list(_BASE_OIDC_SCOPES)
    if refresh_tokens_enabled():
        scopes.append(OFFLINE_ACCESS_SCOPE)
    return scopes


def get_supported_grant_types() -> list[str]:
    """Return the OAuth grant types this deployment currently supports."""
    grant_types = ["authorization_code"]
    if refresh_tokens_enabled():
        grant_types.append("refresh_token")
    return grant_types


def strip_refresh_scope(scope: str) -> str:
    """Remove ``offline_access`` when refresh tokens are disabled."""
    if refresh_tokens_enabled():
        return scope
    return " ".join(part for part in scope.split() if part != OFFLINE_ACCESS_SCOPE)
