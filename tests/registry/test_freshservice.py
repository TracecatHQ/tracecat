from base64 import b64encode
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx
from tracecat_registry import SecretNotFoundError
from tracecat_registry.integrations import freshservice


def _secret_getter(values: dict[str, str]) -> Any:
    return lambda key, default=None: values.get(key, default)


def test_freshservice_secret_form() -> None:
    assert freshservice.freshservice_secret.name == "freshservice"
    assert freshservice.freshservice_secret.keys == ["FRESHSERVICE_API_KEY"]
    assert freshservice.freshservice_secret.optional_keys == ["FRESHSERVICE_BASE_URL"]


def test_resolve_base_url_adds_scheme_and_api_path() -> None:
    assert (
        freshservice._resolve_base_url("example.freshservice.com")
        == "https://example.freshservice.com/api/v2"
    )
    assert (
        freshservice._resolve_base_url("https://example.freshservice.com/")
        == "https://example.freshservice.com/api/v2"
    )
    assert (
        freshservice._resolve_base_url("https://example.freshservice.com/api/v2/")
        == "https://example.freshservice.com/api/v2"
    )


def test_resolve_base_url_uses_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        freshservice.secrets,
        "get_or_default",
        _secret_getter({"FRESHSERVICE_BASE_URL": "example.freshservice.com"}),
    )

    assert freshservice._resolve_base_url(None) == (
        "https://example.freshservice.com/api/v2"
    )


def test_resolve_base_url_requires_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(freshservice.secrets, "get_or_default", _secret_getter({}))

    with pytest.raises(SecretNotFoundError, match="Freshservice calls require"):
        freshservice._resolve_base_url(None)


def test_normalize_path_rejects_absolute_urls() -> None:
    with pytest.raises(ValueError, match="must be relative"):
        freshservice._normalize_path("https://example.freshservice.com/api/v2/tickets")


@pytest.mark.anyio
@respx.mock
async def test_call_endpoint_uses_basic_auth(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(freshservice.secrets, "get", lambda key: "api-key")
    monkeypatch.setattr(freshservice.secrets, "get_or_default", _secret_getter({}))
    route = respx.post(
        "https://example.freshservice.com/api/v2/tickets",
        params={"source": "tracecat"},
    ).mock(return_value=httpx.Response(201, json={"ticket": {"id": 123}}))

    result = await freshservice.call_endpoint(
        method="POST",
        path="/api/v2/tickets",
        query_params={"source": "tracecat"},
        json_body={"subject": "Test"},
        base_url="example.freshservice.com",
    )

    assert result == {"ticket": {"id": 123}}
    assert route.called
    request = route.calls[0].request
    assert request.headers["authorization"] == (
        "Basic " + b64encode(b"api-key:X").decode()
    )
    assert request.headers["accept"] == "application/json"
    assert request.headers["content-type"] == "application/json"


@pytest.mark.anyio
async def test_list_tickets_paginates(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_request(
        **kwargs: Any,
    ) -> tuple[dict[str, Any], httpx.Headers]:
        calls.append(kwargs)
        page = kwargs["query_params"]["page"]
        if page == 2:
            return {"tickets": [{"id": 3}]}, httpx.Headers({})
        return {"tickets": [{"id": 1}, {"id": 2}]}, httpx.Headers(
            {
                "link": (
                    "<https://example.freshservice.com/api/v2/tickets?"
                    'page=2&per_page=2>; rel="next"'
                )
            }
        )

    monkeypatch.setattr(freshservice, "_request_freshservice_http", fake_request)

    result = await freshservice.list_tickets(
        query_params={"filter": "new_and_my_open"},
        page=1,
        per_page=2,
        base_url="https://example.freshservice.com",
    )

    assert result == {
        "items": [{"id": 1}, {"id": 2}, {"id": 3}],
        "pages": 2,
        "next_page": None,
    }
    assert calls[0]["query_params"] == {
        "filter": "new_and_my_open",
        "page": 1,
        "per_page": 2,
    }
    assert calls[1]["query_params"] == {
        "filter": "new_and_my_open",
        "page": 2,
        "per_page": 2,
    }


def test_extract_next_page_follows_link_header() -> None:
    headers = httpx.Headers(
        {
            "link": (
                "<https://example.freshservice.com/api/v2/tickets?"
                'page=3&per_page=30>; rel="next"'
            )
        }
    )

    assert freshservice._extract_next_page(headers, fallback_page=2) == 3
    assert freshservice._extract_next_page(httpx.Headers({}), fallback_page=2) is None


@pytest.mark.anyio
async def test_create_ticket_calls_tickets_endpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_request(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"ticket": {"id": 123}}

    monkeypatch.setattr(freshservice, "_request_freshservice_api", fake_request)

    result = await freshservice.create_ticket(
        ticket={"subject": "Test", "priority": 1, "status": 2},
        base_url="https://example.freshservice.com",
    )

    assert result == {"ticket": {"id": 123}}
    assert calls == [
        {
            "method": "POST",
            "path": "/tickets",
            "json_body": {"subject": "Test", "priority": 1, "status": 2},
            "base_url": "https://example.freshservice.com",
        }
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("call", "expected"),
    [
        (
            lambda: freshservice.get_ticket(
                ticket_id=123,
                include="conversations",
                base_url="https://example.freshservice.com",
            ),
            {
                "method": "GET",
                "path": "/tickets/123",
                "query_params": {"include": "conversations"},
                "base_url": "https://example.freshservice.com",
            },
        ),
        (
            lambda: freshservice.update_ticket(
                ticket_id=123,
                ticket={"priority": 2},
                base_url="https://example.freshservice.com",
            ),
            {
                "method": "PUT",
                "path": "/tickets/123",
                "json_body": {"priority": 2},
                "base_url": "https://example.freshservice.com",
            },
        ),
        (
            lambda: freshservice.delete_ticket(
                ticket_id=123,
                base_url="https://example.freshservice.com",
            ),
            {
                "method": "DELETE",
                "path": "/tickets/123",
                "base_url": "https://example.freshservice.com",
            },
        ),
        (
            lambda: freshservice.create_ticket_note(
                ticket_id=123,
                note={"body": "note"},
                base_url="https://example.freshservice.com",
            ),
            {
                "method": "POST",
                "path": "/tickets/123/notes",
                "json_body": {"body": "note"},
                "base_url": "https://example.freshservice.com",
            },
        ),
        (
            lambda: freshservice.reply_to_ticket(
                ticket_id=123,
                reply={"body": "reply"},
                base_url="https://example.freshservice.com",
            ),
            {
                "method": "POST",
                "path": "/tickets/123/reply",
                "json_body": {"body": "reply"},
                "base_url": "https://example.freshservice.com",
            },
        ),
        (
            lambda: freshservice.get_requester(
                requester_id=123,
                base_url="https://example.freshservice.com",
            ),
            {
                "method": "GET",
                "path": "/requesters/123",
                "base_url": "https://example.freshservice.com",
            },
        ),
        (
            lambda: freshservice.create_requester(
                requester={"primary_email": "user@example.com"},
                base_url="https://example.freshservice.com",
            ),
            {
                "method": "POST",
                "path": "/requesters",
                "json_body": {"primary_email": "user@example.com"},
                "base_url": "https://example.freshservice.com",
            },
        ),
        (
            lambda: freshservice.update_requester(
                requester_id=123,
                requester={"first_name": "A"},
                base_url="https://example.freshservice.com",
            ),
            {
                "method": "PUT",
                "path": "/requesters/123",
                "json_body": {"first_name": "A"},
                "base_url": "https://example.freshservice.com",
            },
        ),
        (
            lambda: freshservice.get_agent(
                agent_id=123,
                base_url="https://example.freshservice.com",
            ),
            {
                "method": "GET",
                "path": "/agents/123",
                "base_url": "https://example.freshservice.com",
            },
        ),
        (
            lambda: freshservice.get_group(
                group_id=123,
                base_url="https://example.freshservice.com",
            ),
            {
                "method": "GET",
                "path": "/groups/123",
                "base_url": "https://example.freshservice.com",
            },
        ),
        (
            lambda: freshservice.get_change(
                change_id=123,
                base_url="https://example.freshservice.com",
            ),
            {
                "method": "GET",
                "path": "/changes/123",
                "base_url": "https://example.freshservice.com",
            },
        ),
        (
            lambda: freshservice.create_change(
                change={"subject": "Change"},
                base_url="https://example.freshservice.com",
            ),
            {
                "method": "POST",
                "path": "/changes",
                "json_body": {"subject": "Change"},
                "base_url": "https://example.freshservice.com",
            },
        ),
        (
            lambda: freshservice.update_change(
                change_id=123,
                change={"priority": 1},
                base_url="https://example.freshservice.com",
            ),
            {
                "method": "PUT",
                "path": "/changes/123",
                "json_body": {"priority": 1},
                "base_url": "https://example.freshservice.com",
            },
        ),
    ],
)
async def test_resource_wrappers_use_documented_contracts(
    monkeypatch: pytest.MonkeyPatch,
    call: Callable[[], Any],
    expected: dict[str, Any],
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_request(**kwargs: Any) -> dict[str, Any]:
        calls.append(kwargs)
        return {"ok": True}

    monkeypatch.setattr(freshservice, "_request_freshservice_api", fake_request)

    result = await call()

    assert result == {"ok": True}
    assert calls == [expected]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("call", "expected_path", "expected_items_key"),
    [
        (freshservice.list_tickets, "/tickets", "tickets"),
        (freshservice.list_requesters, "/requesters", "requesters"),
        (freshservice.list_agents, "/agents", "agents"),
        (freshservice.list_groups, "/groups", "groups"),
        (freshservice.list_changes, "/changes", "changes"),
    ],
)
async def test_list_wrappers_use_documented_contracts(
    monkeypatch: pytest.MonkeyPatch,
    call: Callable[..., Any],
    expected_path: str,
    expected_items_key: str,
) -> None:
    calls: list[dict[str, Any]] = []

    async def fake_paginated(**kwargs: Any) -> freshservice.FreshservicePaginatedResult:
        calls.append(kwargs)
        return {"items": [], "pages": 0, "next_page": None}

    monkeypatch.setattr(freshservice, "_call_paginated_endpoint", fake_paginated)

    result = await call(
        query_params={"updated_since": "2026-01-01"},
        page=2,
        per_page=10,
        max_pages=3,
        base_url="https://example.freshservice.com",
    )

    assert result == {"items": [], "pages": 0, "next_page": None}
    assert calls == [
        {
            "path": expected_path,
            "query_params": {"updated_since": "2026-01-01"},
            "items_key": expected_items_key,
            "page": 2,
            "per_page": 10,
            "max_pages": 3,
            "base_url": "https://example.freshservice.com",
        }
    ]
