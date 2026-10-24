import uuid

import pytest
from fastapi import HTTPException, status

from tracecat.auth import ip_allowlist_enforcement
from tracecat.auth.credentials import _require_superuser
from tracecat.auth.ip_allowlist import (
    PLATFORM_ADMIN_IP_ALLOWLIST_DENIED_DETAIL,
    OrgIPAllowlist,
    compile_allowlist,
)
from tracecat.auth.types import PlatformRole
from tracecat.contexts import RequestAuditContext, ctx_request_audit
from tracecat.db.models import User


@pytest.fixture(autouse=True)
def platform_allowlist(monkeypatch: pytest.MonkeyPatch) -> dict[str, OrgIPAllowlist]:
    state = {"allowlist": OrgIPAllowlist(enabled=False, networks=())}
    calls: list[None] = []

    async def fake_loader() -> OrgIPAllowlist:
        calls.append(None)
        return state["allowlist"]

    monkeypatch.setattr(
        ip_allowlist_enforcement, "get_platform_admin_ip_allowlist", fake_loader
    )
    state["calls"] = calls  # type: ignore[assignment]
    return state


@pytest.mark.anyio
async def test_require_superuser_allows_superuser() -> None:
    user = User(id=uuid.uuid4(), is_superuser=True)
    role = await _require_superuser(user=user)

    # Verify PlatformRole is returned
    assert isinstance(role, PlatformRole)
    assert role.type == "user"
    assert role.user_id == user.id
    assert role.service_id == "tracecat-api"
    # PlatformRole is always a platform superuser
    assert role.is_platform_superuser is True
    # Note: PlatformRole doesn't have organization_id - it's platform-scoped


@pytest.mark.anyio
async def test_require_superuser_denies_non_superuser() -> None:
    user = User(id=uuid.uuid4(), is_superuser=False)
    with pytest.raises(HTTPException) as exc:
        await _require_superuser(user=user)

    assert exc.value.status_code == status.HTTP_403_FORBIDDEN


def _set_client_ip(ip: str | None) -> None:
    ctx_request_audit.set(
        RequestAuditContext(client_ip=ip, user_agent=None, raw_user_agent=None)
    )


@pytest.mark.anyio
async def test_require_superuser_enforces_platform_admin_allowlist(
    platform_allowlist,
) -> None:
    platform_allowlist["allowlist"] = compile_allowlist(
        enabled=True, cidrs=["203.0.113.0/24"]
    )
    user = User(id=uuid.uuid4(), is_superuser=True)

    _set_client_ip("203.0.113.9")
    assert isinstance(await _require_superuser(user=user), PlatformRole)

    _set_client_ip("198.51.100.9")
    with pytest.raises(HTTPException) as exc:
        await _require_superuser(user=user)
    assert exc.value.status_code == status.HTTP_403_FORBIDDEN
    assert exc.value.detail == PLATFORM_ADMIN_IP_ALLOWLIST_DENIED_DETAIL


@pytest.mark.anyio
async def test_require_superuser_checks_superuser_before_allowlist(
    platform_allowlist,
) -> None:
    platform_allowlist["allowlist"] = compile_allowlist(
        enabled=True, cidrs=["203.0.113.0/24"]
    )
    _set_client_ip("198.51.100.9")
    user = User(id=uuid.uuid4(), is_superuser=False)
    with pytest.raises(HTTPException) as exc:
        await _require_superuser(user=user)
    assert exc.value.detail == "Forbidden"
    assert platform_allowlist["calls"] == []


def test_fastapi_users_superuser_routes_require_platform_admin_access() -> None:
    from fastapi.routing import APIRoute

    from tracecat.api.app import _users_router
    from tracecat.auth.credentials import require_superuser, require_superuser_step_up

    routes = {
        route.name: route
        for route in _users_router().routes
        if isinstance(route, APIRoute)
    }
    assert any(
        dep.dependency is require_superuser for dep in routes["users:user"].dependencies
    )
    # Changing or deleting another user also needs a recent security key.
    for name in ("users:patch_user", "users:delete_user"):
        assert any(
            dep.dependency is require_superuser_step_up
            for dep in routes[name].dependencies
        )
    for name in ("users:current_user", "users:patch_current_user"):
        assert all(
            dep.dependency is not require_superuser for dep in routes[name].dependencies
        )
