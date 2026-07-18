from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from starlette.requests import Request

from tracecat.auth.executor_tokens import ExecutorTokenPayload
from tracecat.executor.action_gateway.capabilities import (
    GATEWAY_CAPABILITIES,
    GatewayCapability,
    GatewayRouteKey,
    _agent_gateway_action_allowed,
    _index_capabilities,
    resolve_gateway_actions,
)


def _claims(*allowed_actions: str) -> ExecutorTokenPayload:
    """Build a superuser caller whose Agent grant is intentionally narrow."""
    return ExecutorTokenPayload(
        workspace_id=uuid.uuid4(),
        user_id=None,
        scopes=frozenset({"*"}),
        allowed_actions=frozenset({"core.script.run_python", *allowed_actions}),
        action="core.script.run_python",
        wf_id="wf-1",
        wf_exec_id="run-1",
    )


def _json_request(payload: dict[str, Any]) -> Request:
    """Build the small Starlette request shape used by body-aware resolvers."""
    body = json.dumps(payload).encode()

    async def receive() -> dict[str, Any]:
        return {"type": "http.request", "body": body, "more_body": False}

    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/internal/test",
            "query_string": b"",
            "headers": [(b"content-type", b"application/json")],
        },
        receive=receive,
    )


def test_agent_run_python_cannot_use_unconfigured_action_as_superuser() -> None:
    claims = _claims()

    assert not _agent_gateway_action_allowed(
        claims, frozenset({"core.cases.list_cases"})
    )


def test_duplicate_capability_declarations_are_rejected() -> None:
    capability = GatewayCapability(
        method="GET",
        path="/internal/test",
        actions=frozenset({"action.a"}),
    )

    with pytest.raises(ValueError, match="Duplicate Action Gateway capability"):
        _index_capabilities((capability, capability))


@pytest.mark.anyio
async def test_capability_resolver_cannot_expand_declared_actions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    route_key = GatewayRouteKey("POST", "/internal/test")

    async def resolver(_request: Request) -> frozenset[str]:
        return frozenset({"action.b"})

    monkeypatch.setitem(
        GATEWAY_CAPABILITIES,
        route_key,
        GatewayCapability(
            method="POST",
            path="/internal/test",
            actions=frozenset({"action.a"}),
            resolver=resolver,
        ),
    )

    assert await resolve_gateway_actions(_json_request({}), route_key) is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("query_string", "expected_actions", "allowed"),
    [
        pytest.param(
            b"",
            frozenset({"core.cases.get_case"}),
            True,
            id="base-case",
        ),
        pytest.param(
            b"include_rows=true",
            frozenset({"core.cases.get_linked_case_rows"}),
            False,
            id="linked-rows",
        ),
    ],
)
async def test_get_case_grant_is_bounded_by_requested_detail(
    query_string: bytes,
    expected_actions: frozenset[str],
    allowed: bool,
) -> None:
    claims = _claims("core.cases.get_case")
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/internal/cases/case-id",
            "query_string": query_string,
            "headers": [],
        }
    )

    required_actions = await resolve_gateway_actions(
        request,
        GatewayRouteKey("GET", "/internal/cases/{case_id}"),
    )

    assert required_actions == expected_actions
    assert _agent_gateway_action_allowed(claims, required_actions) is allowed


@pytest.mark.anyio
@pytest.mark.parametrize(
    "route_key",
    [
        pytest.param(
            GatewayRouteKey("POST", "/internal/cases/{case_id}/comments"),
            id="api-response",
        ),
        pytest.param(
            GatewayRouteKey("POST", "/internal/cases/{case_id}/comments/simple"),
            id="udf-response",
        ),
    ],
)
@pytest.mark.parametrize(
    ("payload", "granted_action", "expected_actions", "allowed"),
    [
        pytest.param(
            {"content": "Top-level comment"},
            "core.cases.reply_to_comment",
            frozenset({"core.cases.create_comment"}),
            False,
            id="reply-grant-cannot-create-top-level",
        ),
        pytest.param(
            {"content": "Top-level comment"},
            "core.cases.create_comment",
            frozenset({"core.cases.create_comment"}),
            True,
            id="create-grant-can-create-top-level",
        ),
        pytest.param(
            {"content": "Reply", "parent_id": "parent-comment-id"},
            "core.cases.reply_to_comment",
            frozenset({"core.cases.create_comment", "core.cases.reply_to_comment"}),
            True,
            id="reply-grant-can-reply",
        ),
        pytest.param(
            {"content": "Reply", "parent_id": "parent-comment-id"},
            "core.cases.create_comment",
            frozenset({"core.cases.create_comment", "core.cases.reply_to_comment"}),
            True,
            id="create-grant-can-reply",
        ),
    ],
)
async def test_comment_grant_is_bounded_by_parent_id(
    route_key: GatewayRouteKey,
    payload: dict[str, Any],
    granted_action: str,
    expected_actions: frozenset[str],
    allowed: bool,
) -> None:
    claims = _claims(granted_action)
    request = _json_request(payload)

    required_actions = await resolve_gateway_actions(request, route_key)

    assert required_actions == expected_actions
    assert _agent_gateway_action_allowed(claims, required_actions) is allowed


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("limit", "expected_actions", "allowed"),
    [
        pytest.param(
            1,
            frozenset({"core.table.lookup", "core.table.lookup_many"}),
            True,
            id="single-row",
        ),
        pytest.param(
            None,
            frozenset({"core.table.lookup_many"}),
            False,
            id="batch",
        ),
    ],
)
async def test_single_row_lookup_grant_is_bounded_by_limit(
    limit: int | None,
    expected_actions: frozenset[str],
    allowed: bool,
) -> None:
    claims = _claims("core.table.lookup")
    payload: dict[str, Any] = {
        "table": "indicators",
        "columns": ["value"],
        "values": ["example"],
    }
    if limit is not None:
        payload["limit"] = limit
    request = _json_request(payload)

    required_actions = await resolve_gateway_actions(
        request,
        GatewayRouteKey("POST", "/internal/tables/{table_name}/lookup"),
    )

    assert required_actions == expected_actions
    assert _agent_gateway_action_allowed(claims, required_actions) is allowed


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("payload", "expected_actions", "allowed"),
    [
        pytest.param(
            {"config": {}, "user_prompt": "hello"},
            frozenset({"ai.agent"}),
            True,
            id="ad-hoc-agent",
        ),
        pytest.param(
            {"preset_slug": "example", "user_prompt": "hello"},
            frozenset({"ai.preset_agent"}),
            False,
            id="preset-agent",
        ),
    ],
)
async def test_ad_hoc_agent_grant_is_bounded_by_run_type(
    payload: dict[str, Any],
    expected_actions: frozenset[str],
    allowed: bool,
) -> None:
    claims = _claims("ai.agent")
    request = _json_request(payload)

    required_actions = await resolve_gateway_actions(
        request,
        GatewayRouteKey("POST", "/internal/agent/run"),
    )

    assert required_actions == expected_actions
    assert _agent_gateway_action_allowed(claims, required_actions) is allowed


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("route_key", "path"),
    [
        pytest.param(
            GatewayRouteKey("GET", "/internal/variables/{variable_name}"),
            "/internal/variables/config",
            id="variable-metadata",
        ),
        pytest.param(
            GatewayRouteKey("GET", "/internal/variables/{variable_name}/value"),
            "/internal/variables/config/value",
            id="variable-value",
        ),
    ],
)
async def test_run_python_grant_does_not_imply_unmapped_variable_access(
    route_key: GatewayRouteKey,
    path: str,
) -> None:
    """Keep script execution separate from non-registry SDK capabilities."""
    claims = _claims()
    request = Request(
        {
            "type": "http",
            "method": "GET",
            "path": path,
            "query_string": b"",
            "headers": [],
        }
    )

    required_actions = await resolve_gateway_actions(request, route_key)

    assert required_actions is None
    assert not _agent_gateway_action_allowed(claims, required_actions)
