"""HTTP-level tests for SAML login routing and gating."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import ANY, AsyncMock, Mock, patch

import pytest
from fastapi import FastAPI, status
from fastapi.testclient import TestClient
from fastapi_users.exceptions import UserNotExists
from pydantic import AnyUrl

import tracecat.auth.saml as saml_module
from tracecat.api.common import bootstrap_role
from tracecat.auth.enums import AuthType
from tracecat.auth.types import Role
from tracecat.db.engine import get_async_session_bypass_rls
from tracecat.mcp.saml_bridge_state import SAMLMCPAuthTransaction


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


def _make_saml_acs_db_session(
    relay_state: str,
    *,
    include_membership_lookup: bool = False,
) -> tuple[Mock, Mock]:
    request_id = "request-12345"
    stored_request_data = Mock(
        id=request_id,
        relay_state=relay_state,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
    )
    relay_lookup_result = Mock()
    relay_lookup_result.scalar_one_or_none.return_value = request_id
    stored_requests_result = Mock()
    stored_requests_result.scalars.return_value.all.return_value = [stored_request_data]
    request_data_result = Mock()
    request_data_result.scalar_one_or_none.return_value = stored_request_data

    execute_results = [
        relay_lookup_result,
        stored_requests_result,
        request_data_result,
    ]
    if include_membership_lookup:
        membership_result = Mock()
        membership_result.scalar_one_or_none.return_value = None
        execute_results.append(membership_result)

    db_session = Mock()
    db_session.execute = AsyncMock(side_effect=execute_results)
    db_session.delete = AsyncMock()
    db_session.commit = AsyncMock()
    return db_session, stored_request_data


def test_saml_parser_nameid_fallback_without_attribute_statement() -> None:
    parser = saml_module.SAMLParser(
        """
        <saml2p:Response xmlns:saml2p="urn:oasis:names:tc:SAML:2.0:protocol">
          <saml2:Assertion xmlns:saml2="urn:oasis:names:tc:SAML:2.0:assertion">
            <saml2:Subject>
              <saml2:NameID>user@tracecat.com</saml2:NameID>
            </saml2:Subject>
          </saml2:Assertion>
        </saml2p:Response>
        """
    )

    assert parser.parse_to_dict() == {}
    assert saml_module._extract_candidate_emails(parser) == ["user@tracecat.com"]


@pytest.mark.anyio
async def test_resume_authenticated_mcp_transaction_returns_completion_page() -> None:
    transaction_id = "txn-123"
    relay_state = f"{uuid.uuid4()}:mcp:{transaction_id}:token"
    stores = Mock()
    stores.transactions.get = AsyncMock(
        return_value=SAMLMCPAuthTransaction(
            id=transaction_id,
            client_id="client-123",
            client_redirect_uri=AnyUrl("http://localhost:3333/callback"),
            code_challenge="challenge",
            redirect_uri_provided_explicitly=True,
            scopes=["openid", "profile", "email"],
            created_at=1.0,
            expires_at=2.0,
            authenticated_at=1.5,
        )
    )

    with (
        patch.object(saml_module, "create_saml_bridge_stores", return_value=stores),
        patch.object(
            saml_module,
            "complete_saml_mcp_transaction",
            AsyncMock(return_value="http://localhost:3333/callback?code=test-code"),
        ),
    ):
        response = await saml_module._resume_authenticated_mcp_transaction(relay_state)

    assert response is not None
    assert response.status_code == status.HTTP_200_OK
    response_body = bytes(response.body).decode()
    assert "Continue to Claude" in response_body
    assert "http://localhost:3333/callback?code=test-code" in response_body


@pytest.mark.anyio
async def test_resume_authenticated_mcp_transaction_ignores_pending_transaction() -> (
    None
):
    transaction_id = "txn-123"
    relay_state = f"{uuid.uuid4()}:mcp:{transaction_id}:token"
    stores = Mock()
    stores.transactions.get = AsyncMock(
        return_value=SAMLMCPAuthTransaction(
            id=transaction_id,
            client_id="client-123",
            client_redirect_uri=AnyUrl("http://localhost:3333/callback"),
            code_challenge="challenge",
            redirect_uri_provided_explicitly=True,
            scopes=["openid", "profile", "email"],
            created_at=1.0,
            expires_at=2.0,
            authenticated_at=None,
        )
    )

    with patch.object(saml_module, "create_saml_bridge_stores", return_value=stores):
        response = await saml_module._resume_authenticated_mcp_transaction(relay_state)

    assert response is None


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


@pytest.mark.anyio
async def test_sso_acs_returns_mcp_error_page_when_user_does_not_exist() -> None:
    organization_id = uuid.uuid4()
    relay_state = f"{organization_id}:mcp:txn-123:token"
    db_session, _ = _make_saml_acs_db_session(relay_state)
    saml_client = Mock()
    saml_client.parse_authn_request_response.return_value = Mock(
        in_response_to="request-12345"
    )
    user_manager = Mock()
    user_manager.get_by_email = AsyncMock(side_effect=UserNotExists())
    request = Mock()
    role = Role(type="service", service_id="tracecat-ui")

    with (
        patch.object(saml_module, "set_rls_context", AsyncMock()),
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
        patch.object(saml_module, "SAMLParser", return_value=Mock(attributes={})),
        patch.object(
            saml_module,
            "_extract_candidate_emails",
            return_value=["user@example.com"],
        ),
        patch.object(
            saml_module,
            "_select_authorized_email",
            AsyncMock(return_value=("user@example.com", None)),
        ),
        patch.object(
            saml_module,
            "is_superadmin_saml_bootstrap_allowed_for_org",
            AsyncMock(return_value=False),
        ),
    ):
        response = await saml_module.sso_acs(
            request=request,
            saml_response="response",
            relay_state=relay_state,
            user_manager=user_manager,
            strategy=Mock(),
            db_session=db_session,
            role=role,
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    response_body = bytes(response.body).decode()
    assert "No Tracecat account exists for this email." in response_body
    user_manager.saml_callback.assert_not_called()


@pytest.mark.anyio
async def test_sso_acs_returns_mcp_error_page_when_user_lacks_org_access() -> None:
    organization_id = uuid.uuid4()
    relay_state = f"{organization_id}:mcp:txn-123:token"
    db_session, _ = _make_saml_acs_db_session(
        relay_state, include_membership_lookup=True
    )
    saml_client = Mock()
    saml_client.parse_authn_request_response.return_value = Mock(
        in_response_to="request-12345"
    )
    user = Mock(id=uuid.uuid4(), is_active=True, is_superuser=False)
    user_manager = Mock()
    user_manager.get_by_email = AsyncMock(return_value=user)
    user_manager.saml_callback = AsyncMock(return_value=user)
    request = Mock()
    role = Role(type="service", service_id="tracecat-ui")

    with (
        patch.object(saml_module, "set_rls_context", AsyncMock()),
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
        patch.object(saml_module, "SAMLParser", return_value=Mock(attributes={})),
        patch.object(
            saml_module,
            "_extract_candidate_emails",
            return_value=["user@example.com"],
        ),
        patch.object(
            saml_module,
            "_select_authorized_email",
            AsyncMock(return_value=("user@example.com", None)),
        ),
        patch.object(
            saml_module,
            "is_superadmin_saml_bootstrap_allowed_for_org",
            AsyncMock(return_value=False),
        ),
    ):
        response = await saml_module.sso_acs(
            request=request,
            saml_response="response",
            relay_state=relay_state,
            user_manager=user_manager,
            strategy=Mock(),
            db_session=db_session,
            role=role,
        )

    assert response.status_code == status.HTTP_403_FORBIDDEN
    response_body = bytes(response.body).decode()
    assert "Your account is not a member of this organization." in response_body
