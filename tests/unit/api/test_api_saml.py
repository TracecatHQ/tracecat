"""HTTP-level tests for SAML login routing and gating."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import ANY, AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI, Request, status
from fastapi.testclient import TestClient

import tracecat.auth.saml as saml_module
from tracecat.api.common import bootstrap_role
from tracecat.auth.enums import AuthType
from tracecat.db.engine import get_async_session_bypass_rls


def _override_saml_db_session(client: TestClient) -> Mock:
    db_session = Mock()
    db_session.add = Mock()
    db_session.commit = AsyncMock()

    async def override_get_async_session_bypass_rls() -> Mock:
        return db_session

    app = cast(FastAPI, client.app)
    app.dependency_overrides[get_async_session_bypass_rls] = (
        override_get_async_session_bypass_rls
    )
    return db_session


def _result(value: object = None, *, values: list[object] | None = None) -> Mock:
    result = Mock()
    result.scalar_one_or_none.return_value = value
    result.scalars.return_value.all.return_value = values or []
    return result


def _saml_request() -> Request:
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/auth/saml/acs",
            "query_string": b"",
            "headers": [],
        }
    )


def _saml_acs_context(
    monkeypatch: pytest.MonkeyPatch,
    *,
    relay_valid: bool = True,
    is_active: bool = True,
    candidate_emails: list[str] | None = None,
    authorize_email: bool = True,
    callback_denied: bool = False,
) -> SimpleNamespace:
    email = "user@example.com"
    organization_id = uuid.uuid4()
    relay_state = f"{organization_id}:relay-token"
    request_id = "req-1234567890"
    stored_request = SimpleNamespace(
        id=request_id,
        relay_state=relay_state,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    saml_client = Mock()
    saml_client.parse_authn_request_response.return_value = SimpleNamespace(
        in_response_to=request_id
    )
    user = SimpleNamespace(
        id=uuid.uuid4(),
        email=email,
        is_active=is_active,
        is_superuser=False,
    )
    user_manager = Mock()
    callback_error = (
        saml_module.HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="Authentication failed"
        )
        if callback_denied
        else None
    )
    user_manager.saml_callback = AsyncMock(
        return_value=user, side_effect=callback_error
    )
    user_manager._emit_auth_failure_audit = AsyncMock()
    db_session = Mock()
    db_session.execute = AsyncMock(
        side_effect=[
            _result(request_id if relay_valid else None),
            _result(values=[stored_request]),
            _result(stored_request),
            _result(),
        ]
    )
    db_session.commit = db_session.delete = db_session.rollback = AsyncMock()
    patches = {
        "set_rls_context": AsyncMock(),
        "get_org_saml_metadata_url": AsyncMock(
            return_value="https://metadata.example.com"
        ),
        "create_saml_client": AsyncMock(return_value=saml_client),
        "_extract_candidate_emails": Mock(
            return_value=candidate_emails if candidate_emails is not None else [email]
        ),
        "_select_authorized_email": AsyncMock(
            return_value=(email if authorize_email else None, None)
        ),
        "is_superadmin_saml_bootstrap_allowed_for_org": AsyncMock(return_value=False),
    }
    for name, value in patches.items():
        monkeypatch.setattr(saml_module, name, value)
    return SimpleNamespace(
        relay_state=relay_state,
        user=user,
        user_manager=user_manager,
        db_session=db_session,
    )


async def _call_sso_acs(ctx: SimpleNamespace) -> Any:
    return await saml_module.sso_acs(
        _saml_request(),
        saml_response="saml-response",
        relay_state=ctx.relay_state,
        user_manager=cast(Any, ctx.user_manager),
        strategy=cast(Any, Mock()),
        db_session=cast(Any, ctx.db_session),
        role=cast(Any, SimpleNamespace(service_id="tracecat-ui")),
    )


@pytest.mark.anyio
async def test_saml_login_uses_resolved_org_for_auth_gate(
    client: TestClient,
) -> None:
    organization_id = uuid.uuid4()
    _override_saml_db_session(client)
    saml_client = Mock()
    saml_client.prepare_for_authenticate.return_value = (
        "req-123",
        {"headers": [("Location", "https://idp.example.com/sso")]},
    )

    with (
        patch.object(
            saml_module,
            "resolve_auth_organization_id",
            AsyncMock(return_value=organization_id),
        ) as resolve_org_mock,
        patch.object(
            saml_module,
            "verify_auth_type",
            AsyncMock(),
        ) as verify_auth_type_mock,
        patch.object(
            saml_module,
            "get_org_saml_metadata_url",
            AsyncMock(return_value="https://metadata.example.com"),
        ),
        patch.object(
            saml_module,
            "create_saml_client",
            AsyncMock(return_value=saml_client),
        ),
    ):
        response = client.get("/auth/saml/login?org=example-org")

    assert response.status_code == status.HTTP_200_OK
    assert response.json() == {"redirect_url": "https://idp.example.com/sso"}
    resolve_org_mock.assert_awaited_once()
    verify_auth_type_mock.assert_awaited_once_with(
        AuthType.SAML,
        role=bootstrap_role(organization_id),
        session=ANY,
    )


@pytest.mark.anyio
async def test_saml_login_stops_before_handler_when_org_scoped_gate_fails(
    client: TestClient,
) -> None:
    organization_id = uuid.uuid4()
    _override_saml_db_session(client)

    with (
        patch.object(
            saml_module,
            "resolve_auth_organization_id",
            AsyncMock(return_value=organization_id),
        ),
        patch.object(
            saml_module,
            "verify_auth_type",
            AsyncMock(
                side_effect=saml_module.HTTPException(
                    status_code=status.HTTP_403_FORBIDDEN,
                    detail="Auth type saml is not enabled",
                )
            ),
        ),
        patch.object(
            saml_module,
            "get_org_saml_metadata_url",
            AsyncMock(),
        ) as get_metadata_mock,
    ):
        response = client.get("/auth/saml/login?org=example-org")

    assert response.status_code == status.HTTP_403_FORBIDDEN
    assert response.json()["detail"] == "Auth type saml is not enabled"
    get_metadata_mock.assert_not_awaited()


@pytest.mark.parametrize(
    "case",
    ["inactive", "org-denied", "relay", "no-email", "unauthorized", "auto-provision"],
)
@pytest.mark.anyio
async def test_saml_acs_failure_audit_cases(
    monkeypatch: pytest.MonkeyPatch,
    *,
    case: str,
) -> None:
    expected_status = 400 if case in {"inactive", "relay", "no-email"} else 403
    reason = {
        "inactive": "inactive_user",
        "org-denied": "org_access_denied",
    }.get(case)
    ctx = _saml_acs_context(
        monkeypatch,
        relay_valid=case != "relay",
        is_active=case != "inactive",
        candidate_emails=[] if case == "no-email" else None,
        authorize_email=case != "unauthorized",
        callback_denied=case == "auto-provision",
    )

    with pytest.raises(saml_module.HTTPException) as exc_info:
        await _call_sso_acs(ctx)

    assert exc_info.value.status_code == expected_status
    if reason:
        ctx.user_manager._emit_auth_failure_audit.assert_awaited_once_with(
            user=ctx.user,
            auth_method="saml",
            reason=reason,
            org_ids=set(),
        )
    else:
        ctx.user_manager._emit_auth_failure_audit.assert_not_awaited()
    if case in {"no-email", "unauthorized"}:
        ctx.user_manager.saml_callback.assert_not_awaited()
