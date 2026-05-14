from __future__ import annotations

import uuid

from tracecat import config
from tracecat.agent.tokens import (
    UserMCPServerClaim,
    mint_mcp_token,
    verify_mcp_token,
)
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


def test_mcp_token_accepts_legacy_user_mcp_server_claim_shape(monkeypatch) -> None:
    """Tokens minted with the pre-rollout claim shape (no ``id``, inline ``url``
    and ``headers``) must still verify. This locks in replay compatibility for
    JWTs minted before the refs-only cutover.
    """
    workspace_id, organization_id, session_id = _setup_service_key(monkeypatch)

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
            )
        ],
    )

    claims = verify_mcp_token(token)
    assert len(claims.user_mcp_servers) == 1
    ref = claims.user_mcp_servers[0]
    assert ref.name == "legacy-mcp"
    assert ref.id is None
    assert ref.url == "https://legacy.example.com/mcp"
    assert ref.transport == "http"
    assert ref.headers == {"Authorization": "Bearer legacy-secret"}
    assert ref.timeout == 30


def test_mcp_token_round_trips_refs_only_user_mcp_server_claim(monkeypatch) -> None:
    """New-shape claims (``name`` + ``id`` only) must round-trip cleanly so the
    trusted server has the integration id it needs to re-resolve secrets.
    """
    workspace_id, organization_id, session_id = _setup_service_key(monkeypatch)
    integration_id = uuid.uuid4()

    token = mint_mcp_token(
        workspace_id=workspace_id,
        organization_id=organization_id,
        allowed_actions=["core.http_request"],
        session_id=session_id,
        registry_lock=_registry_lock(),
        user_mcp_servers=[UserMCPServerClaim(name="modern-mcp", id=integration_id)],
    )

    claims = verify_mcp_token(token)
    assert len(claims.user_mcp_servers) == 1
    ref = claims.user_mcp_servers[0]
    assert ref.name == "modern-mcp"
    assert ref.id == integration_id
    # New-shape claims should not carry secret material.
    assert ref.url is None
    assert ref.headers == {}
    assert ref.timeout is None


def test_mcp_token_accepts_mixed_legacy_and_refs_user_mcp_servers(monkeypatch) -> None:
    """A token carrying both shapes in the same claim list must decode without loss."""
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
                name="legacy",
                url="https://legacy.example.com/mcp",
                transport="http",
                headers={"Authorization": "Bearer legacy"},
            ),
            UserMCPServerClaim(name="modern", id=integration_id),
        ],
    )

    claims = verify_mcp_token(token)
    assert [(ref.name, ref.id, ref.url) for ref in claims.user_mcp_servers] == [
        ("legacy", None, "https://legacy.example.com/mcp"),
        ("modern", integration_id, None),
    ]
