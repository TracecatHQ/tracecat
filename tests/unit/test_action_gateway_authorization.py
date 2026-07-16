from __future__ import annotations

import json
import uuid
from collections.abc import Callable
from typing import Any

import pytest
from starlette.requests import Request

from tracecat.agent.internal_router import run_agent_endpoint
from tracecat.auth.executor_tokens import ExecutorTokenPayload
from tracecat.cases.internal_router import get_case
from tracecat.executor.action_gateway.capabilities import (
    _agent_gateway_action_allowed,
    resolve_gateway_actions,
)
from tracecat.tables.internal_router import lookup_rows
from tracecat.variables.internal_router import (
    get_variable_by_name,
    get_variable_value,
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

    required_actions = await resolve_gateway_actions(request, get_case)

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

    required_actions = await resolve_gateway_actions(request, lookup_rows)

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

    required_actions = await resolve_gateway_actions(request, run_agent_endpoint)

    assert required_actions == expected_actions
    assert _agent_gateway_action_allowed(claims, required_actions) is allowed


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("endpoint", "path"),
    [
        pytest.param(
            get_variable_by_name,
            "/internal/variables/config",
            id="variable-metadata",
        ),
        pytest.param(
            get_variable_value,
            "/internal/variables/config/value",
            id="variable-value",
        ),
    ],
)
async def test_run_python_grant_does_not_imply_unmapped_variable_access(
    endpoint: Callable[..., Any],
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

    required_actions = await resolve_gateway_actions(request, endpoint)

    assert required_actions is None
    assert not _agent_gateway_action_allowed(claims, required_actions)
