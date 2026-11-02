"""Forms routes over HTTP: the flag and entitlement gate, and the error responses."""

import re
import uuid
from typing import Any
from unittest.mock import AsyncMock, Mock, patch

import orjson
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from tests._route_utils import EffectiveRoute, iter_effective_api_routes
from tracecat import config
from tracecat.api.app import app as api_app
from tracecat.auth.types import Role
from tracecat.exceptions import EntitlementRequired
from tracecat.executor.action_gateway.app import create_app as create_gateway_app
from tracecat.feature_flags import FeatureFlag
from tracecat.forms.dependencies import check_forms_enabled
from tracecat.forms.enums import FormErrorCode, FormFieldErrorCode
from tracecat.forms.exceptions import FormError, form_error_handler
from tracecat.forms.schemas import FormFieldError
from tracecat.tiers import defaults as tier_defaults

GATEWAY_APP = create_gateway_app()


def _routes(app: FastAPI, *prefixes: str) -> list[EffectiveRoute]:
    return [
        route
        for route in iter_effective_api_routes(app)
        if any(route.path == p or route.path.startswith(f"{p}/") for p in prefixes)
    ]


ROUTES = [
    *(
        pytest.param("client", route, id=f"{sorted(route.methods)} {route.path}")
        for route in _routes(
            api_app, "/workspaces/{workspace_id}/forms", "/forms", "/intake"
        )
    ),
    *(
        pytest.param(
            "action_gateway_client", route, id=f"{sorted(route.methods)} {route.path}"
        )
        for route in _routes(GATEWAY_APP, "/internal/forms")
    ),
]


def test_the_gateway_serves_the_paths_core_forms_calls() -> None:
    routes = {
        (method, route.path)
        for route in _routes(GATEWAY_APP, "/internal/forms")
        for method in route.methods
    }

    assert routes == {
        ("GET", "/internal/forms"),
        ("GET", "/internal/forms/{form_id}"),
        ("PUT", "/internal/forms/workflows/{workflow_id}"),
        ("GET", "/internal/forms/{form_id}/submissions"),
        ("GET", "/internal/forms/submissions/{submission_id}"),
        ("DELETE", "/internal/forms/submissions/{submission_id}"),
        ("GET", "/internal/forms/files/{file_id}/download"),
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("flags", "entitled", "status_code"),
    [(set(), True, 404), ({FeatureFlag.FORMS}, False, 403)],
    ids=["flag_off", "entitlement_off"],
)
@pytest.mark.parametrize(("client_name", "route"), ROUTES)
async def test_every_forms_route_needs_the_flag_and_the_entitlement(
    request: pytest.FixtureRequest,
    test_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    client_name: str,
    route: EffectiveRoute,
    flags: set[str],
    entitled: bool,
    status_code: int,
) -> None:
    client: TestClient = request.getfixturevalue(client_name)
    monkeypatch.setattr(config, "TRACECAT__FEATURE_FLAGS", flags)
    path = route.path.replace("{workspace_id}", str(test_admin_role.workspace_id))
    path = re.sub(r"\{[a-z_]+\}", str(uuid.uuid4()), path)
    [method] = route.methods
    is_org_entitled = AsyncMock(return_value=entitled)

    with patch("tracecat.tiers.access.is_org_entitled", new=is_org_entitled):
        response = client.request(
            method,
            path,
            params={"workspace_id": str(test_admin_role.workspace_id)},
            json={},
        )

    assert response.status_code == status_code
    if status_code == 404:
        assert response.json() == {"detail": "Feature not enabled"}
        is_org_entitled.assert_not_awaited()
    else:
        assert response.json()["type"] == "EntitlementRequired"


@pytest.mark.anyio
async def test_one_forms_token_turns_on_the_flag_and_the_entitlement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a single-tenant deployment, ``forms`` in the flags is all it takes."""
    role = Role(type="user", organization_id=uuid.uuid4(), service_id="tracecat-api")
    monkeypatch.setattr(config, "TRACECAT__FEATURE_FLAGS", {FeatureFlag.FORMS})
    monkeypatch.setattr(config, "TRACECAT__EE_MULTI_TENANT", False)

    for flags, allowed in (("forms", True), ("git-sync", False)):
        entitlements = tier_defaults.resolve_oss_default_entitlements(flags)
        monkeypatch.setattr(tier_defaults, "DEFAULT_ENTITLEMENTS", entitlements)
        if allowed:
            await check_forms_enabled(AsyncMock(), role)
        else:
            with pytest.raises(EntitlementRequired):
                await check_forms_enabled(AsyncMock(), role)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("code", "status_code"),
    [
        (FormErrorCode.NOT_FOUND, 404),
        (FormErrorCode.ALREADY_EXISTS, 409),
        (FormErrorCode.FORM_CONFIG_INVALID, 422),
        (FormErrorCode.FORM_WORKFLOW_UNPUBLISHED, 409),
        (FormErrorCode.FORM_OFFLINE, 409),
        (FormErrorCode.FORM_UNAVAILABLE, 409),
        (FormErrorCode.FORM_CHANGED, 409),
        (FormErrorCode.FORM_VALIDATION_ERROR, 422),
        (FormErrorCode.WORKFLOW_START_FAILED, 503),
        (FormErrorCode.FILE_FIELD_INVALID, 422),
        (FormErrorCode.FILE_INVALID, 400),
        (FormErrorCode.FILE_TYPE_NOT_ALLOWED, 415),
        (FormErrorCode.FILE_TOO_LARGE, 413),
        (FormErrorCode.FILE_LIMIT_REACHED, 409),
        (FormErrorCode.FILE_UPLOAD_FAILED, 503),
    ],
)
async def test_form_errors_answer_with_their_status_and_detail(
    code: FormErrorCode, status_code: int
) -> None:
    error = FormFieldError(
        field="model", code=FormFieldErrorCode.REQUIRED, message="Required"
    )

    response = await form_error_handler(
        Mock(), FormError(code, "Wrong", errors=[error])
    )

    assert response.status_code == status_code
    body: dict[str, Any] = orjson.loads(response.body)
    assert body == {
        "detail": {
            "code": code.value,
            "message": "Wrong",
            "errors": [{"field": "model", "code": "required", "message": "Required"}],
            "issues": None,
        }
    }
    for app in (api_app, GATEWAY_APP):
        assert app.exception_handlers[FormError] is form_error_handler
