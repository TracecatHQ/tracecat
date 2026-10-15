"""core.forms actions, end to end through the registry SDK client."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import orjson
import pytest
from tracecat_registry.context import RegistryContext, clear_context, set_context
from tracecat_registry.core import forms as core_forms
from tracecat_registry.sdk.client import TracecatClient

FILE_ID = "7a1c2e3f-4b5d-4e6f-8a9b-0c1d2e3f4a5b"
FORM_ID = "0b0c8f8e-0d0b-4d3a-9a57-3d0f2a3e6b11"
SUBMISSION_ID = "5f2d1f4a-9c1e-4c77-8a43-6a1f0d1b2c3d"
WORKFLOW_ID = "wf_4itKqkgCZrLhgYiq5L211X"
RESPONSE = {"id": FORM_ID}

DATES = {
    "submitted_after": datetime(2026, 1, 1, tzinfo=UTC),
    "submitted_before": datetime(2026, 2, 1, tzinfo=UTC),
}

# (action, arguments, method, path under /internal, query, JSON body)
CALLS: list[tuple[Callable[..., Any], dict[str, Any], str, str, dict, Any]] = [
    (core_forms.list_forms, {}, "GET", "/forms", {"limit": "20"}, None),
    (core_forms.get_form, {"form_id": FORM_ID}, "GET", f"/forms/{FORM_ID}", {}, None),
    (
        core_forms.update_form,
        {"workflow_id": WORKFLOW_ID, "config": {"layout": []}},
        "PUT",
        f"/forms/workflows/{WORKFLOW_ID}",
        {},
        {"config": {"layout": []}},
    ),
    (
        core_forms.list_submissions,
        {"form_id": FORM_ID, "limit": 10, "cursor": "c1", **DATES},
        "GET",
        f"/forms/{FORM_ID}/submissions",
        {"limit": "10", "cursor": "c1"}
        | {key: value.isoformat() for key, value in DATES.items()},
        None,
    ),
    (
        core_forms.get_submission,
        {"submission_id": SUBMISSION_ID},
        "GET",
        f"/forms/submissions/{SUBMISSION_ID}",
        {},
        None,
    ),
    (
        core_forms.delete_submission,
        {"submission_id": SUBMISSION_ID},
        "DELETE",
        f"/forms/submissions/{SUBMISSION_ID}",
        {},
        None,
    ),
    (
        core_forms.download_file,
        {"file_id": FILE_ID},
        "GET",
        f"/forms/files/{FILE_ID}/download",
        {},
        None,
    ),
]


@pytest.fixture
async def requests(
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncIterator[list[httpx.Request]]:
    """Run actions against a real SDK client whose gateway records requests."""
    sent: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=RESPONSE)

    def transport(*, uds: str) -> httpx.MockTransport:
        return httpx.MockTransport(handle)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", transport)
    client = TracecatClient(action_gateway_socket="/tmp/forms-test.sock")
    set_context(
        RegistryContext(workspace_id="", workflow_id="", run_id="", _client=client)
    )
    try:
        yield sent
    finally:
        clear_context()


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("action", "arguments", "method", "path", "query", "body"), CALLS
)
async def test_action_is_gated_and_calls_its_exact_internal_route(
    requests: list[httpx.Request],
    action: Callable[..., Any],
    arguments: dict[str, Any],
    method: str,
    path: str,
    query: dict[str, str],
    body: Any,
) -> None:
    # The decorator stores its arguments under a mangled-looking name, so
    # plain attribute access is not possible.
    registered = getattr(action, "__tracecat_udf_kwargs")
    assert getattr(action, "__tracecat_udf_key") == f"core.forms.{action.__name__}"
    assert registered["required_entitlements"] == ["forms"]

    result = await action(**arguments)

    [request] = requests
    assert request.method == method
    assert str(request.url.copy_with(query=None)) == (
        f"http://tracecat-action-gateway/internal{path}"
    )
    assert dict(request.url.params) == query
    assert (orjson.loads(request.content) if request.content else None) == body
    assert result == (None if method == "DELETE" else RESPONSE)
