"""Request-time enforcement of organization and platform admin IP allowlists.

Organization enforcement happens after authentication, once the organization
scope of the request is known. Platform admin enforcement happens in the
superuser dependency that guards the platform admin API. Allowlists are cached
in-process for a short TTL so the hot path is an in-memory CIDR membership test.
"""

from __future__ import annotations

import orjson
from async_lru import alru_cache
from fastapi import HTTPException, status
from sqlalchemy import select

from tracecat.auth.ip_allowlist import (
    IP_ALLOWLIST_CACHE_TTL_SECONDS,
    IP_ALLOWLIST_DENIED_DETAIL,
    IP_ALLOWLIST_ENABLED_KEY,
    IP_ALLOWLISTS_KEY,
    PLATFORM_ADMIN_IP_ALLOWLIST_DENIED_DETAIL,
    PLATFORM_ADMIN_IP_ALLOWLIST_ENABLED_KEY,
    PLATFORM_ADMIN_IP_ALLOWLISTS_KEY,
    IPAddress,
    OrgIPAllowlist,
    compile_allowlist,
    parse_client_ip,
)
from tracecat.contexts import ctx_request_audit
from tracecat.db.engine import get_async_session_auth_context_manager
from tracecat.db.models import PlatformSetting
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger
from tracecat.settings.schemas import (
    IPAllowlist,
    IPAllowlistCheckResult,
    ip_allowlist_cidrs,
    parse_stored_ip_allowlists,
)
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


@alru_cache(ttl=IP_ALLOWLIST_CACHE_TTL_SECONDS)
async def get_platform_admin_ip_allowlist() -> OrgIPAllowlist:
    """Load and compile the platform admin allowlist (cached).

    Missing or malformed rows yield a non-enforced allowlist so a bad row can
    never lock platform admins out.
    """
    keys = (PLATFORM_ADMIN_IP_ALLOWLIST_ENABLED_KEY, PLATFORM_ADMIN_IP_ALLOWLISTS_KEY)
    async with get_async_session_auth_context_manager() as session:
        result = await session.execute(
            select(PlatformSetting).where(PlatformSetting.key.in_(keys))
        )
        rows = {setting.key: setting for setting in result.scalars().all()}
    values: dict[str, object] = {}
    for key, setting in rows.items():
        try:
            values[key] = orjson.loads(setting.value)
        except orjson.JSONDecodeError:
            logger.warning("Ignoring malformed platform admin IP allowlist", key=key)
    allowlists = parse_stored_ip_allowlists(
        values.get(PLATFORM_ADMIN_IP_ALLOWLISTS_KEY)
    )
    return compile_allowlist(
        enabled=values.get(PLATFORM_ADMIN_IP_ALLOWLIST_ENABLED_KEY) is True,
        cidrs=ip_allowlist_cidrs(allowlists),
    )


def clear_ip_allowlist_cache() -> None:
    """Make committed allowlist changes visible to the next request."""
    get_org_ip_allowlist.cache_clear()
    get_platform_admin_ip_allowlist.cache_clear()


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
    return _admits(await get_org_ip_allowlist(organization_id), client_ip)


def _admits(allowlist: OrgIPAllowlist, client_ip: IPAddress | None) -> bool:
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


async def enforce_platform_admin_ip_allowlist() -> None:
    """Reject the current platform admin request if its client IP is excluded.

    Platforms without an enforced platform admin allowlist admit every address.

    Raises:
        HTTPException(403): If the client IP is not in the allowlist.
    """
    client_ip = current_client_ip()
    if _admits(await get_platform_admin_ip_allowlist(), client_ip):
        return
    logger.warning(
        "Request denied by platform admin IP allowlist",
        client_ip=str(client_ip) if client_ip else None,
    )
    raise HTTPException(
        status_code=status.HTTP_403_FORBIDDEN,
        detail=PLATFORM_ADMIN_IP_ALLOWLIST_DENIED_DETAIL,
    )


def ensure_allowlist_admits_caller(*, enabled: bool, cidrs: list[str]) -> None:
    """Reject enabling a non-empty allowlist that excludes the caller's own IP.

    Raises:
        HTTPException(400): If the allowlist would lock the caller out.
    """
    if not enabled or not cidrs:
        return
    caller_ip = current_client_ip()
    allowlist = compile_allowlist(enabled=True, cidrs=cidrs)
    if caller_ip is None or allowlist.match(caller_ip) is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "Your current IP address"
                + (f" ({caller_ip})" if caller_ip else "")
                + " is not in the allowlist. Add it before enabling."
            ),
        )


def check_ip_against_allowlists(
    ip_address: str, *, enabled: bool, allowlists: list[IPAllowlist]
) -> IPAllowlistCheckResult:
    """Report whether ``ip_address`` is admitted by the given saved allowlists.

    Raises:
        HTTPException(400): If ``ip_address`` is not a valid IP address.
    """
    ip = parse_client_ip(ip_address)
    if ip is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid IP address",
        )
    allowlist = compile_allowlist(enabled=enabled, cidrs=ip_allowlist_cidrs(allowlists))
    matched = allowlist.match(ip)
    matched_cidr = matched.with_prefixlen if matched else None
    matched_allowlist = None
    if matched_cidr is not None:
        matched_allowlist = next(
            (item.name for item in allowlists if matched_cidr in item.cidrs), None
        )
    return IPAllowlistCheckResult(
        allowed=matched is not None or not allowlist.enforced,
        matched_cidr=matched_cidr,
        matched_allowlist=matched_allowlist,
        enforced=allowlist.enforced,
    )
