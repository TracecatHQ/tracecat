"""SAML domain admission policy.

A leaf module so both the SAML callback and the password-login guard apply the
same policy; ``tracecat.auth.saml`` imports ``tracecat.auth.users``, so the
policy cannot live in either.
"""

from __future__ import annotations

from tracecat import config
from tracecat.organization.domains import normalize_domain


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
