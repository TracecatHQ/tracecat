"""Request-time enforcement of organization IP allowlists.

Enforcement happens after authentication, once the organization scope of the
request is known. The allowlist is cached in-process for a short TTL so the
hot path is an in-memory CIDR membership test.
"""

from __future__ import annotations

from async_lru import alru_cache
from fastapi import HTTPException, status

from tracecat.auth.ip_allowlist import (
    IP_ALLOWLIST_CACHE_TTL_SECONDS,
    IP_ALLOWLIST_DENIED_DETAIL,
    IP_ALLOWLIST_ENABLED_KEY,
    IP_ALLOWLISTS_KEY,
    IPAddress,
    OrgIPAllowlist,
    compile_allowlist,
    parse_client_ip,
)
from tracecat.contexts import ctx_request_audit
from tracecat.db.engine import get_async_session_auth_context_manager
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger
from tracecat.settings.schemas import ip_allowlist_cidrs, parse_stored_ip_allowlists
from tracecat.settings.service import get_setting_from_bypass_session


@alru_cache(ttl=IP_ALLOWLIST_CACHE_TTL_SECONDS)
async def get_org_ip_allowlist(organization_id: OrganizationID) -> OrgIPAllowlist:
    """Load and compile the allowlist for an organization (cached)."""
    async with get_async_session_auth_context_manager() as session:
        enabled = await get_setting_from_bypass_session(
            IP_ALLOWLIST_ENABLED_KEY,
            organization_id=organization_id,
            session=session,
            default=False,
        )
        stored = await get_setting_from_bypass_session(
            IP_ALLOWLISTS_KEY,
            organization_id=organization_id,
            session=session,
            default=[],
        )
    allowlists = parse_stored_ip_allowlists(stored)
    return compile_allowlist(
        enabled=bool(enabled), cidrs=ip_allowlist_cidrs(allowlists)
    )


def clear_ip_allowlist_cache() -> None:
    """Make committed allowlist changes visible to the next request."""
    get_org_ip_allowlist.cache_clear()


def current_client_ip() -> IPAddress | None:
    """Client IP attributed to the current request by the request middleware."""
    audit = ctx_request_audit.get()
    return parse_client_ip(audit.client_ip if audit else None)


async def is_ip_allowed_for_org(
    organization_id: OrganizationID, client_ip: IPAddress | None
) -> bool:
    """Whether ``client_ip`` may access ``organization_id``.

    Organizations without an enforced allowlist admit every address. An
    enforced allowlist denies requests whose client IP could not be resolved.
    """
    allowlist = await get_org_ip_allowlist(organization_id)
    if not allowlist.enforced:
        return True
    if client_ip is None:
        return False
    return allowlist.match(client_ip) is not None


async def enforce_org_ip_allowlist(
    organization_id: OrganizationID,
    *,
    bypass: bool = False,
) -> None:
    """Reject the current request if the org allowlist excludes its client IP.

    Args:
        organization_id: Organization the request is scoped to.
        bypass: Skip enforcement (platform superusers, break-glass access).

    Raises:
        HTTPException(403): If the client IP is not in the allowlist.
    """
    if bypass:
        return
    client_ip = current_client_ip()
    if await is_ip_allowed_for_org(organization_id, client_ip):
        return
    logger.warning(
        "Request denied by organization IP allowlist",
        organization_id=organization_id,
        client_ip=str(client_ip) if client_ip else None,
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=IP_ALLOWLIST_DENIED_DETAIL,
    )
