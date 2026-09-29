"""SAML admission policy.

A leaf module so the SAML callback, the password-login guard, discovery, and
invitation acceptance apply the same policy; ``tracecat.auth.saml`` imports
``tracecat.auth.users``, so the policy cannot live in either.
"""

from __future__ import annotations

from tracecat import config
from tracecat.auth.enums import AuthType
from tracecat.db.engine import SupportsExecute
from tracecat.identifiers import OrganizationID
from tracecat.organization.domains import normalize_domain
from tracecat.settings.service import get_setting_from_bypass_session


def env_allowed_domains() -> set[str]:
    """Return normalized env-domain allowlist for SAML checks."""
    normalized_domains: set[str] = set()
    for raw_domain in config.TRACECAT__AUTH_ALLOWED_DOMAINS:
        domain = raw_domain.strip().lower()
        if not domain:
            continue
        try:
            normalized_domains.add(normalize_domain(domain).normalized_domain)
        except ValueError:
            continue
    return normalized_domains


def is_domain_allowed_for_org(
    *,
    normalized_domain: str,
    active_domains: set[str],
) -> bool:
    """Apply runtime SAML domain policy for a normalized email domain."""
    if active_domains:
        return normalized_domain in active_domains

    if config.TRACECAT__EE_MULTI_TENANT:
        return False

    allowed = env_allowed_domains()
    if allowed:
        return normalized_domain in allowed
    return True


async def is_org_saml_enforced(
    session: SupportsExecute, organization_id: OrganizationID
) -> bool:
    """Whether SAML is the organization's only way in."""
    if AuthType.SAML not in config.TRACECAT__AUTH_TYPES:
        return False
    if not await get_setting_from_bypass_session(
        "saml_enabled", organization_id=organization_id, session=session, default=True
    ):
        return False
    return bool(
        await get_setting_from_bypass_session(
            "saml_enforced",
            organization_id=organization_id,
            session=session,
            default=False,
        )
    )
