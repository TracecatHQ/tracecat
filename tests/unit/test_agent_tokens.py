from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

import jwt
import pytest

from tracecat import config
from tracecat.agent.tokens import (
    AGENT_OTEL_TOKEN_AUDIENCE,
    AGENT_OTEL_TOKEN_ISSUER,
    AGENT_OTEL_TOKEN_SUBJECT,
    UserMCPServerClaim,
    mint_agent_otel_token,
    mint_mcp_token,
    verify_agent_otel_token,
    verify_mcp_token,
)
from tracecat.auth.secrets import get_service_key
from tracecat.registry.lock.types import RegistryLock


def _registry_lock() -> RegistryLock:
    return RegistryLock(
        origins={"tracecat_registry": "test-version"},
        actions={"core.http_request": "tracecat_registry"},
    )


def _setup_service_key(monkeypatch) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")
    return uuid.uuid4(), uuid.uuid4(), uuid.uuid4()


def test_mcp_token_round_trips_parent_agent_workflow_metadata(
    monkeypatch,
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    session_id = uuid.uuid4()
    user_id = uuid.uuid4()

    token = mint_mcp_token(
        workspace_id=workspace_id,
        organization_id=organization_id,
        user_id=user_id,
        allowed_actions=["core.http_request"],
        session_id=session_id,
        parent_agent_workflow_id=f"agent/{session_id}",
        parent_agent_run_id="run-123",
        registry_lock=_registry_lock(),
    )

    claims = verify_mcp_token(token)
    assert claims.workspace_id == workspace_id
    assert claims.organization_id == organization_id
    assert claims.session_id == session_id
    assert claims.user_id == user_id
    assert claims.parent_agent_workflow_id == f"agent/{session_id}"
    assert claims.parent_agent_run_id == "run-123"
    assert claims.registry_lock == _registry_lock()


def test_mcp_token_round_trips_legacy_and_refs_user_mcp_server_claims(
    monkeypatch,
) -> None:
    """Both user MCP claim shapes decode without loss, alone or mixed.

    Pre-rollout claims (no ``id``, inline ``url`` and ``headers``) must still
    verify for replay compatibility. Refs-only claims carry the integration id
    the trusted server needs to re-resolve secrets, and no secret material.
    """
    workspace_id, organization_id, session_id = _setup_service_key(monkeypatch)
    integration_id = uuid.uuid4()

    token = mint_mcp_token(
        workspace_id=workspace_id,
        organization_id=organization_id,
        allowed_actions=["core.http_request"],
        session_id=session_id,
        registry_lock=_registry_lock(),
        user_mcp_servers=[
            UserMCPServerClaim(
                name="legacy-mcp",
                url="https://legacy.example.com/mcp",
                transport="http",
                headers={"Authorization": "Bearer legacy-secret"},
                timeout=30,
            ),
            UserMCPServerClaim(name="modern-mcp", id=integration_id),
        ],
    )

    legacy, modern = verify_mcp_token(token).user_mcp_servers
    assert legacy.name == "legacy-mcp"
    assert legacy.id is None
    assert legacy.url == "https://legacy.example.com/mcp"
    assert legacy.transport == "http"
    assert legacy.headers == {"Authorization": "Bearer legacy-secret"}
    assert legacy.timeout == 30
    assert modern.name == "modern-mcp"
    assert modern.id == integration_id
    assert modern.url is None
    assert modern.headers == {}
    assert modern.timeout is None


def test_mcp_token_round_trips_deferred_actions(monkeypatch) -> None:
    workspace_id, organization_id, session_id = _setup_service_key(monkeypatch)

    token = mint_mcp_token(
        workspace_id=workspace_id,
        organization_id=organization_id,
        allowed_actions=["core.http_request"],
        deferred_actions=["core.cases.delete_case", "mcp__Jira__deleteIssue"],
        session_id=session_id,
        registry_lock=_registry_lock(),
    )

    claims = verify_mcp_token(token)
    assert claims.allowed_actions == ["core.http_request"]
    assert claims.deferred_actions == [
        "core.cases.delete_case",
        "mcp__Jira__deleteIssue",
    ]


def test_mcp_token_without_deferred_actions_claim_still_verifies(monkeypatch) -> None:
    """Tokens signed before the deferred_actions claim existed defer nothing."""
    workspace_id, organization_id, session_id = _setup_service_key(monkeypatch)
    token = mint_mcp_token(
        workspace_id=workspace_id,
        organization_id=organization_id,
        allowed_actions=["core.http_request"],
        session_id=session_id,
        registry_lock=_registry_lock(),
    )
    payload = jwt.decode(token, options={"verify_signature": False})
    del payload["deferred_actions"]
    legacy = jwt.encode(payload, get_service_key(), algorithm="HS256")

    claims = verify_mcp_token(legacy)
    assert claims.allowed_actions == ["core.http_request"]
    assert claims.deferred_actions == []


def test_mcp_token_rejects_action_both_allowed_and_deferred(monkeypatch) -> None:
    workspace_id, organization_id, session_id = _setup_service_key(monkeypatch)

    with pytest.raises(ValueError, match="both allowed and deferred"):
        mint_mcp_token(
            workspace_id=workspace_id,
            organization_id=organization_id,
            allowed_actions=["core.http_request"],
            deferred_actions=["core.http_request"],
            session_id=session_id,
            registry_lock=_registry_lock(),
        )


@pytest.mark.parametrize(
    ("allowed_name", "deferred_name"),
    [
        ("core.http_request", "core__http_request"),
        ("core.http_request", "mcp__tracecat-registry__core__http_request"),
        ("core.http_request", "mcp.tracecat_registry.core.http_request"),
        ("mcp__example__a__b", "mcp__tracecat-registry__mcp__example__a__b"),
    ],
)
def test_mcp_token_rejects_overlap_in_another_spelling(
    monkeypatch, allowed_name: str, deferred_name: str
) -> None:
    workspace_id, organization_id, session_id = _setup_service_key(monkeypatch)

    with pytest.raises(ValueError, match="both allowed and deferred"):
        mint_mcp_token(
            workspace_id=workspace_id,
            organization_id=organization_id,
            allowed_actions=[allowed_name],
            deferred_actions=[deferred_name],
            session_id=session_id,
            registry_lock=_registry_lock(),
        )


def test_mcp_token_canonicalizes_deferred_actions(monkeypatch) -> None:
    """Deferred names decode in the form execution checks calls under."""
    workspace_id, organization_id, session_id = _setup_service_key(monkeypatch)
    token = mint_mcp_token(
        workspace_id=workspace_id,
        organization_id=organization_id,
        allowed_actions=[],
        deferred_actions=[
            "mcp__tracecat-registry__core__http_request",
            "core.http_request",
            "mcp__tracecat-registry__mcp__Jira__deleteIssue",
            "mcp__tracecat-registry__mcp__example__a__b",
        ],
        session_id=session_id,
        registry_lock=_registry_lock(),
    )

    claims = verify_mcp_token(token)
    assert claims.deferred_actions == [
        "core.http_request",
        "mcp__Jira__deleteIssue",
        "mcp__example__a__b",
    ]


@pytest.mark.parametrize("deferred_name", ["mcp.example.a.b", "mcp.Jira.deleteIssue"])
def test_mcp_token_rejects_dotted_user_mcp_deferred_name(
    monkeypatch, deferred_name: str
) -> None:
    """The dotted spelling replaces "__" with ".", so it cannot name one tool.

    Remote tool a__b is listed as mcp__example__a__b, but its dotted spelling
    mcp.example.a.b could also be tool a.b.
    """
    workspace_id, organization_id, session_id = _setup_service_key(monkeypatch)

    with pytest.raises(ValueError, match="Ambiguous user MCP tool name"):
        mint_mcp_token(
            workspace_id=workspace_id,
            organization_id=organization_id,
            allowed_actions=["mcp__example__a__b"],
            deferred_actions=[deferred_name],
            session_id=session_id,
            registry_lock=_registry_lock(),
        )


def test_mcp_token_with_dotted_user_mcp_deferred_name_fails_verification(
    monkeypatch,
) -> None:
    workspace_id, organization_id, session_id = _setup_service_key(monkeypatch)
    token = mint_mcp_token(
        workspace_id=workspace_id,
        organization_id=organization_id,
        allowed_actions=["mcp__example__a__b"],
        session_id=session_id,
        registry_lock=_registry_lock(),
    )
    payload = jwt.decode(token, options={"verify_signature": False})
    payload["deferred_actions"] = ["mcp.example.a.b"]
    signed = jwt.encode(payload, get_service_key(), algorithm="HS256")

    with pytest.raises(ValueError, match="Invalid MCP token claims"):
        verify_mcp_token(signed)


def test_agent_otel_token_round_trips(monkeypatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    workspace_id = uuid.uuid4()
    organization_id = uuid.uuid4()
    session_id = uuid.uuid4()

    token = mint_agent_otel_token(
        workspace_id=workspace_id,
        organization_id=organization_id,
        session_id=session_id,
    )

    claims = verify_agent_otel_token(token)
    assert claims.workspace_id == workspace_id
    assert claims.organization_id == organization_id
    assert claims.session_id == session_id


@pytest.mark.parametrize(
    ("token_factory", "match"),
    [
        pytest.param(lambda: "not-a-jwt", "Invalid Agent OTel token", id="not-a-jwt"),
        pytest.param(
            lambda: jwt.encode(
                _agent_otel_payload(aud="wrong-audience"),
                get_service_key(),
                algorithm="HS256",
            ),
            "Invalid Agent OTel token",
            id="wrong-audience",
        ),
        pytest.param(
            lambda: jwt.encode(
                _agent_otel_payload(sub="wrong-subject"),
                get_service_key(),
                algorithm="HS256",
            ),
            "Invalid Agent OTel token subject",
            id="wrong-subject",
        ),
        pytest.param(
            lambda: mint_agent_otel_token(
                workspace_id=uuid.uuid4(),
                organization_id=uuid.uuid4(),
                session_id=uuid.uuid4(),
                ttl_seconds=-1,
            ),
            "Invalid Agent OTel token",
            id="expired",
        ),
    ],
)
def test_agent_otel_token_rejects_invalid_tokens(
    monkeypatch, token_factory: Callable[[], str], match: str
) -> None:
    monkeypatch.setattr(config, "TRACECAT__SERVICE_KEY", "test-service-key")

    with pytest.raises(ValueError, match=match):
        verify_agent_otel_token(token_factory())


def _agent_otel_payload(
    *,
    aud: str = AGENT_OTEL_TOKEN_AUDIENCE,
    sub: str = AGENT_OTEL_TOKEN_SUBJECT,
) -> dict[str, object]:
    now = datetime.now(UTC)
    return {
        "iss": AGENT_OTEL_TOKEN_ISSUER,
        "aud": aud,
        "sub": sub,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=60)).timestamp()),
        "workspace_id": str(uuid.uuid4()),
        "organization_id": str(uuid.uuid4()),
        "session_id": str(uuid.uuid4()),
    }


def test_mcp_token_rejects_internal_tool_both_allowed_and_deferred(monkeypatch) -> None:
    workspace_id, organization_id, session_id = _setup_service_key(monkeypatch)

    with pytest.raises(ValueError, match="both allowed and deferred"):
        mint_mcp_token(
            workspace_id=workspace_id,
            organization_id=organization_id,
            allowed_actions=[],
            allowed_internal_tools=["internal.builder.update_preset"],
            deferred_actions=["internal.builder.update_preset"],
            session_id=session_id,
            registry_lock=_registry_lock(),
        )
