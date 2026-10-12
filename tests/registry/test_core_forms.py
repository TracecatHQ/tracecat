"""Tests for the core.forms actions and the forms SDK client."""

from __future__ import annotations

from collections.abc import AsyncIterator, Callable
from datetime import UTC, datetime
from typing import Any, cast
from unittest.mock import AsyncMock, MagicMock

import httpx
import orjson
import pytest
from tracecat_registry.context import RegistryContext, clear_context, set_context
from tracecat_registry.core import forms as core_forms
from tracecat_registry.sdk.client import TracecatClient
from tracecat_registry.sdk.forms import FormsClient

FORM_ID = "0b0c8f8e-0d0b-4d3a-9a57-3d0f2a3e6b11"
SUBMISSION_ID = "5f2d1f4a-9c1e-4c77-8a43-6a1f0d1b2c3d"
WORKFLOW_ID = "wf_4itKqkgCZrLhgYiq5L211X"

ACTIONS: dict[str, Callable[..., Any]] = {
    "list_forms": core_forms.list_forms,
    "get_form": core_forms.get_form,
    "update_form": core_forms.update_form,
    "list_submissions": core_forms.list_submissions,
    "get_submission": core_forms.get_submission,
    "delete_submission": core_forms.delete_submission,
}


# ----- Registration -----


@pytest.mark.parametrize("name", sorted(ACTIONS))
def test_action_is_registered_under_core_forms_behind_the_entitlement(
    name: str,
) -> None:
    # The decorator stores its arguments under a mangled-looking name, so
    # plain attribute access is not possible.
    kwargs = getattr(ACTIONS[name], "__tracecat_udf_kwargs")
    key = getattr(ACTIONS[name], "__tracecat_udf_key")

    assert key == f"core.forms.{name}"
    assert kwargs["namespace"] == "core.forms"
    assert kwargs["required_entitlements"] == ["forms"]


def test_file_download_is_left_for_the_file_uploads_milestone() -> None:
    assert not hasattr(core_forms, "download_file")


# ----- Actions delegate to the SDK -----


@pytest.fixture
async def forms_sdk() -> AsyncIterator[MagicMock]:
    """A registry context whose forms client records calls."""
    context = MagicMock()
    context.forms = MagicMock()
    set_context(cast(RegistryContext, context))
    try:
        yield context.forms
    finally:
        clear_context()


@pytest.mark.anyio
async def test_list_forms_delegates(forms_sdk: MagicMock) -> None:
    forms_sdk.list_forms = AsyncMock(return_value={"items": []})

    assert await core_forms.list_forms(limit=5, cursor="c1") == {"items": []}
    forms_sdk.list_forms.assert_awaited_once_with(limit=5, cursor="c1")


@pytest.mark.anyio
async def test_get_form_delegates(forms_sdk: MagicMock) -> None:
    forms_sdk.get_form = AsyncMock(return_value={"id": FORM_ID})

    assert await core_forms.get_form(form_id=FORM_ID) == {"id": FORM_ID}
    forms_sdk.get_form.assert_awaited_once_with(FORM_ID)


@pytest.mark.anyio
async def test_update_form_delegates(forms_sdk: MagicMock) -> None:
    forms_sdk.update_form = AsyncMock(return_value={"id": FORM_ID})
    config = {"title": "Laptop request"}

    result = await core_forms.update_form(
        workflow_id=WORKFLOW_ID, status="online", config=config
    )

    assert result == {"id": FORM_ID}
    forms_sdk.update_form.assert_awaited_once_with(
        workflow_id=WORKFLOW_ID, status="online", config=config
    )


@pytest.mark.anyio
async def test_list_submissions_delegates(forms_sdk: MagicMock) -> None:
    forms_sdk.list_submissions = AsyncMock(return_value={"items": []})
    after = datetime(2026, 1, 1, tzinfo=UTC)

    await core_forms.list_submissions(form_id=FORM_ID, submitted_after=after)

    forms_sdk.list_submissions.assert_awaited_once_with(
        FORM_ID, limit=20, cursor=None, submitted_after=after, submitted_before=None
    )


@pytest.mark.anyio
async def test_get_and_delete_submission_delegate(forms_sdk: MagicMock) -> None:
    forms_sdk.get_submission = AsyncMock(return_value={"id": SUBMISSION_ID})
    forms_sdk.delete_submission = AsyncMock(return_value=None)

    assert await core_forms.get_submission(submission_id=SUBMISSION_ID) == {
        "id": SUBMISSION_ID
    }
    assert await core_forms.delete_submission(submission_id=SUBMISSION_ID) is None
    forms_sdk.get_submission.assert_awaited_once_with(SUBMISSION_ID)
    forms_sdk.delete_submission.assert_awaited_once_with(SUBMISSION_ID)


# ----- SDK paths -----
#
# Subclients inherit the client's ``/internal`` prefix, so they pass relative
# paths. These pin the exact path of every helper.


@pytest.fixture
def http() -> MagicMock:
    client = MagicMock()
    client.get = AsyncMock(return_value={})
    client.put = AsyncMock(return_value={})
    client.delete = AsyncMock(return_value=None)
    return client


@pytest.fixture
def forms_client(http: MagicMock) -> FormsClient:
    return FormsClient(http)


@pytest.mark.anyio
async def test_list_forms_path(forms_client: FormsClient, http: MagicMock) -> None:
    await forms_client.list_forms(limit=5)
    http.get.assert_awaited_once_with("/forms", params={"limit": 5})

    http.get.reset_mock()
    await forms_client.list_forms(limit=5, cursor="c1")
    http.get.assert_awaited_once_with("/forms", params={"limit": 5, "cursor": "c1"})


@pytest.mark.anyio
async def test_get_form_path(forms_client: FormsClient, http: MagicMock) -> None:
    await forms_client.get_form(FORM_ID)
    http.get.assert_awaited_once_with(f"/forms/{FORM_ID}")


@pytest.mark.anyio
async def test_update_form_path_sends_only_what_was_set(
    forms_client: FormsClient, http: MagicMock
) -> None:
    await forms_client.update_form(workflow_id=WORKFLOW_ID, status="offline")
    http.put.assert_awaited_once_with(
        f"/forms/workflows/{WORKFLOW_ID}", json={"status": "offline"}
    )

    http.put.reset_mock()
    await forms_client.update_form(workflow_id=WORKFLOW_ID, config={"layout": []})
    http.put.assert_awaited_once_with(
        f"/forms/workflows/{WORKFLOW_ID}", json={"config": {"layout": []}}
    )


@pytest.mark.anyio
async def test_list_submissions_path(
    forms_client: FormsClient, http: MagicMock
) -> None:
    await forms_client.list_submissions(
        FORM_ID,
        limit=10,
        cursor="c1",
        submitted_after=datetime(2026, 1, 1, tzinfo=UTC),
        submitted_before=datetime(2026, 2, 1, tzinfo=UTC),
    )

    http.get.assert_awaited_once_with(
        f"/forms/{FORM_ID}/submissions",
        params={
            "limit": 10,
            "cursor": "c1",
            "submitted_after": "2026-01-01T00:00:00+00:00",
            "submitted_before": "2026-02-01T00:00:00+00:00",
        },
    )


@pytest.mark.anyio
async def test_submission_paths(forms_client: FormsClient, http: MagicMock) -> None:
    await forms_client.get_submission(SUBMISSION_ID)
    await forms_client.delete_submission(SUBMISSION_ID)

    http.get.assert_awaited_once_with(f"/forms/submissions/{SUBMISSION_ID}")
    http.delete.assert_awaited_once_with(f"/forms/submissions/{SUBMISSION_ID}")


@pytest.mark.anyio
async def test_update_form_reaches_the_exact_gateway_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Through the real client, the path lands under /internal exactly once."""
    requests: list[httpx.Request] = []

    def handle(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json={"id": FORM_ID})

    def create_transport(*, uds: str) -> httpx.MockTransport:
        return httpx.MockTransport(handle)

    monkeypatch.setattr(httpx, "AsyncHTTPTransport", create_transport)
    client = TracecatClient(action_gateway_socket="/tmp/forms-test.sock")

    result = await client.forms.update_form(workflow_id=WORKFLOW_ID, status="online")

    assert result == {"id": FORM_ID}
    [request] = requests
    assert request.method == "PUT"
    assert (
        str(request.url)
        == f"http://tracecat-action-gateway/internal/forms/workflows/{WORKFLOW_ID}"
    )
    assert orjson.loads(request.content) == {"status": "online"}
