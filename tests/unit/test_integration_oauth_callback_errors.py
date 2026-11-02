"""OAuth callback regressions using the real token exchange and mocked I/O."""

import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from fastapi import HTTPException
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.models import OAuthStateDB
from tracecat.integrations import router
from tracecat.integrations.providers.base import AuthorizationCodeOAuthProvider
from tracecat.integrations.providers.github.oauth import GitHubOAuthProvider
from tracecat.integrations.providers.slack.oauth import SlackOAuthProvider
from tracecat.integrations.service import IntegrationService
from tracecat.logger import logger


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


@dataclass(frozen=True, slots=True)
class CallbackContext:
    session: AsyncMock
    service: MagicMock
    provider: AuthorizationCodeOAuthProvider
    role: Role
    state: OAuthStateDB
    fetch_token: AsyncMock
    logs: list[str]


@pytest.fixture(
    params=[GitHubOAuthProvider, SlackOAuthProvider], ids=["github", "slack"]
)
def callback_context(
    monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest
) -> Iterator[CallbackContext]:
    provider_impl: type[AuthorizationCodeOAuthProvider] = request.param
    role = Role(type="user", user_id=uuid.uuid4(), service_id="tracecat-api")
    state = OAuthStateDB(
        state=uuid.uuid4(),
        user_id=role.user_id,
        workspace_id=uuid.uuid4(),
        provider_id=provider_impl.id,
        expires_at=datetime.now(UTC) + timedelta(minutes=5),
        code_verifier="synthetic-pkce-verifier",
    )
    session = AsyncMock(spec=AsyncSession)
    session.get.return_value = state
    service = MagicMock(spec=IntegrationService)
    service._is_custom_mcp_oauth_provider.return_value = False
    service.resolve_provider_impl.return_value = provider_impl
    provider = provider_impl(client_id="synthetic-client")
    fetch_token = AsyncMock(
        return_value={
            "access_token": "synthetic-access-token",
            "refresh_token": "synthetic-refresh-token",
            "expires_in": 1800,
            "scope": " ".join(provider.requested_scopes),
        }
    )
    if provider_impl is SlackOAuthProvider:
        fetch_token.return_value = {"authed_user": fetch_token.return_value}
    monkeypatch.setattr(provider.client, "fetch_token", fetch_token)
    monkeypatch.setattr(provider_impl, "instantiate", AsyncMock(return_value=provider))
    monkeypatch.setattr(router, "IntegrationService", MagicMock(return_value=service))
    monkeypatch.setattr(config, "TRACECAT__RLS_MODE", config.RLSMode.OFF)
    monkeypatch.setattr(config, "TRACECAT__PUBLIC_APP_URL", "https://tracecat.test")
    logs: list[str] = []
    sink = logger.add(logs.append, format="{level} {message} {extra}")
    role_token = ctx_role.set(None)
    try:
        yield CallbackContext(
            session, service, provider, role, state, fetch_token, logs
        )
    finally:
        ctx_role.reset(role_token)
        logger.remove(sink)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("failure", "expected_status"),
    [
        ("invalid_client", 400),
        ("invalid_grant", 400),
        ("connect", 502),
        ("timeout", 502),
        ("status", 502),
    ],
)
async def test_token_exchange_failures_are_safe(
    callback_context: CallbackContext, failure: str, expected_status: int
) -> None:
    context = callback_context
    sensitive = "synthetic-provider-detail-with-secret"
    # Exercise authlib's actual OAuth response parsing for provider rejections.
    if failure in {"invalid_client", "invalid_grant"}:
        context.fetch_token.side_effect = lambda *args, **kwargs: (
            context.provider.client.parse_response_token(
                httpx.Response(
                    400, json={"error": failure, "error_description": sensitive}
                )
            )
        )
    elif failure == "connect":
        context.fetch_token.side_effect = httpx.ConnectError(sensitive)
    elif failure == "timeout":
        context.fetch_token.side_effect = httpx.ReadTimeout(sensitive)
    else:
        request = httpx.Request("POST", f"https://provider.test/token?code={sensitive}")
        context.fetch_token.side_effect = httpx.HTTPStatusError(
            sensitive, request=request, response=httpx.Response(503, text=sensitive)
        )

    with pytest.raises(HTTPException) as caught:
        await router.oauth_callback(
            session=context.session,
            role=context.role,
            code="synthetic-code",
            state=str(context.state.state),
        )

    assert caught.value.status_code == expected_status
    assert caught.value.__cause__ is None
    assert caught.value.__context__ is None
    assert sensitive not in str(caught.value.detail)
    assert sensitive not in "".join(context.logs)
    assert sum("WARNING" in line for line in context.logs) == 1
    assert not any("ERROR" in line for line in context.logs)
    context.service.store_integration.assert_not_awaited()
    context.session.delete.assert_awaited_once_with(context.state)
    context.session.commit.assert_awaited_once()


@pytest.mark.anyio
async def test_successful_callback_stores_tokens_and_redirects(
    callback_context: CallbackContext,
) -> None:
    context = callback_context
    result = await router.oauth_callback(
        session=context.session,
        role=context.role,
        code="synthetic-code",
        state=str(context.state.state),
    )

    assert result.status == "connected"
    assert result.provider_id == context.provider.id
    assert result.redirect_url == (
        f"https://tracecat.test/workspaces/{context.state.workspace_id}/integrations"
    )
    context.fetch_token.assert_awaited_once_with(
        context.provider.token_endpoint,
        code="synthetic-code",
        state=str(context.state.state),
        code_verifier="synthetic-pkce-verifier",
    )
    context.service.store_integration.assert_awaited_once()
    stored = context.service.store_integration.call_args.kwargs
    assert stored["user_id"] == context.role.user_id
    assert stored["access_token"] == SecretStr("synthetic-access-token")
    assert stored["refresh_token"] == SecretStr("synthetic-refresh-token")
    assert stored["expires_in"] == 1800
    assert stored["scope"] == " ".join(context.provider.requested_scopes)


@pytest.mark.anyio
async def test_unexpected_exchange_error_is_not_reclassified(
    callback_context: CallbackContext,
) -> None:
    context = callback_context
    failure = RuntimeError("synthetic-unexpected-error")
    context.fetch_token.side_effect = failure

    with pytest.raises(RuntimeError) as caught:
        await router.oauth_callback(
            session=context.session,
            role=context.role,
            code="synthetic-code",
            state=str(context.state.state),
        )

    assert caught.value is failure
    context.service.store_integration.assert_not_awaited()


@pytest.mark.anyio
async def test_callback_resolves_scopes_for_state_workspace(
    callback_context: CallbackContext, monkeypatch: pytest.MonkeyPatch
) -> None:
    context = callback_context
    workspace_scopes = frozenset({"integration:create", "integration:read"})
    compute_scopes = AsyncMock(return_value=workspace_scopes)
    monkeypatch.setattr(router, "compute_effective_scopes", compute_scopes)

    await router.oauth_callback(
        session=context.session,
        role=context.role,
        code="synthetic-code",
        state=str(context.state.state),
    )

    compute_scopes.assert_awaited_once()
    assert compute_scopes.await_args is not None
    resolved_for = compute_scopes.await_args.args[0]
    assert resolved_for.workspace_id == context.state.workspace_id
    service_factory = cast(MagicMock, router.IntegrationService)
    service_role = service_factory.call_args.kwargs["role"]
    assert service_role.workspace_id == context.state.workspace_id
    assert service_role.scopes == workspace_scopes
    current_role = ctx_role.get()
    assert current_role is not None
    assert current_role.scopes == workspace_scopes
