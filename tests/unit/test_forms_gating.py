"""The forms gating dependency needs the feature flag and the entitlement."""

from collections.abc import AsyncIterator
from typing import get_args
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import httpx
import pytest
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.responses import JSONResponse

from tracecat import config
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session
from tracecat.exceptions import EntitlementRequired
from tracecat.feature_flags import FeatureFlag
from tracecat.forms.dependencies import require_forms_enabled
from tracecat.tiers import defaults as tier_defaults
from tracecat.tiers.enums import Entitlement

pytestmark = pytest.mark.anyio


@pytest.fixture
def role() -> Role:
    return Role(
        type="user",
        organization_id=uuid4(),
        workspace_id=uuid4(),
        user_id=uuid4(),
        service_id="tracecat-api",
        scopes=frozenset({"form:read"}),
    )


async def _list_forms() -> dict[str, bool]:
    return {"ok": True}


def _app(role: Role) -> FastAPI:
    router = APIRouter(dependencies=[Depends(require_forms_enabled)])
    router.add_api_route("/forms", _list_forms)

    role_dependency = get_args(WorkspaceActorRouteRole)[1].dependency
    app = FastAPI()
    app.include_router(router)
    app.dependency_overrides[role_dependency] = lambda: role

    async def session() -> AsyncIterator[AsyncMock]:
        yield AsyncMock()

    app.dependency_overrides[get_async_session] = session

    async def entitlement_required(request: Request, exc: Exception) -> JSONResponse:
        assert isinstance(exc, EntitlementRequired)
        return JSONResponse(status_code=403, content={"entitlement": exc.entitlement})

    app.add_exception_handler(EntitlementRequired, entitlement_required)
    return app


async def _get_forms(role: Role) -> httpx.Response:
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=_app(role)), base_url="http://test"
    ) as http:
        return await http.get("/forms")


@pytest.fixture
def flag_on(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__FEATURE_FLAGS", {FeatureFlag.FORMS})


@pytest.fixture
def flag_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__FEATURE_FLAGS", set())


def _entitled(value: bool) -> AsyncMock:
    return AsyncMock(return_value=value)


@pytest.mark.usefixtures("flag_off")
@pytest.mark.parametrize("entitled", [True, False])
async def test_flag_off_returns_404_whatever_the_entitlement(
    role: Role, entitled: bool
) -> None:
    is_org_entitled = _entitled(entitled)
    with patch("tracecat.tiers.access.is_org_entitled", new=is_org_entitled):
        response = await _get_forms(role)

    assert response.status_code == 404
    is_org_entitled.assert_not_awaited()


@pytest.mark.usefixtures("flag_on")
async def test_entitlement_off_returns_403(role: Role) -> None:
    with patch("tracecat.tiers.access.is_org_entitled", new=_entitled(False)):
        response = await _get_forms(role)

    assert response.status_code == 403
    assert response.json() == {"entitlement": Entitlement.FORMS.value}


@pytest.mark.usefixtures("flag_on")
async def test_flag_and_entitlement_on_reach_the_route(role: Role) -> None:
    is_org_entitled = _entitled(True)
    with patch("tracecat.tiers.access.is_org_entitled", new=is_org_entitled):
        response = await _get_forms(role)

    assert response.status_code == 200
    assert is_org_entitled.await_args is not None
    assert is_org_entitled.await_args.args[1:] == (
        role.organization_id,
        Entitlement.FORMS,
    )


@pytest.mark.usefixtures("flag_on")
async def test_single_tenant_forms_token_grants_the_entitlement(
    role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One ``forms`` token in TRACECAT__FEATURE_FLAGS turns on flag and entitlement."""
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)
    monkeypatch.setattr(
        tier_defaults,
        "DEFAULT_ENTITLEMENTS",
        tier_defaults.resolve_oss_default_entitlements("forms"),
    )

    response = await _get_forms(role)

    assert response.status_code == 200


@pytest.mark.usefixtures("flag_on")
async def test_single_tenant_without_forms_token_is_not_entitled(
    role: Role, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)
    monkeypatch.setattr(
        tier_defaults,
        "DEFAULT_ENTITLEMENTS",
        tier_defaults.resolve_oss_default_entitlements("git-sync"),
    )

    response = await _get_forms(role)

    assert response.status_code == 403
