"""Security and protocol boundary tests for the Microsoft Graph SDK wrappers.

These exercise Tracecat-owned boundaries only: credential isolation between the
application and delegated tokens, header ownership, restriction of requests to the
approved Graph v1.0 roots, continuation URL validation, JSON round-tripping,
retry safety and bounded pagination. Requests are served by an
`httpx.MockTransport` mounted
underneath the real `msgraph-core` middleware pipeline, so the Kiota request
adapter, authentication provider and serializers all run for real.

The pipeline lives in `_microsoft_graph_transport` and is shared by every product
namespace, so it is covered once here through the generic wrapper. Per-product
credential precedence is covered in `test_microsoft_product_sdks.py`.
"""

import json
from collections.abc import Callable
from contextlib import AbstractContextManager
from typing import Any

import httpx
import pytest
from tracecat_registry import SecretNotFoundError
from tracecat_registry.integrations import _microsoft_graph_transport as transport
from tracecat_registry.integrations import microsoft_graph_sdk as graph
from tracecat_registry.integrations._microsoft_graph_transport import (
    MicrosoftGraphAuthMode,
)

# Mirrors of the `tests/registry/conftest.py` fixture types, declared locally so
# the module does not import from a conftest.
type GraphHandler = Callable[[httpx.Request], httpx.Response]
type InstallGraphTransport = Callable[[GraphHandler], list[httpx.Request]]
type GraphSecrets = Callable[..., AbstractContextManager[None]]

SERVICE_TOKEN_NAME = "MICROSOFT_GRAPH_SERVICE_TOKEN"
USER_TOKEN_NAME = "MICROSOFT_GRAPH_USER_TOKEN"
SERVICE_TOKEN_VALUE = "application-token-placeholder"
USER_TOKEN_VALUE = "delegated-token-placeholder"

PRODUCT_TOKEN_NAMES = [
    "MICROSOFT_GRAPH_SECURITY_SERVICE_TOKEN",
    "MICROSOFT_GRAPH_SECURITY_USER_TOKEN",
    "MICROSOFT_OUTLOOK_SERVICE_TOKEN",
    "MICROSOFT_OUTLOOK_USER_TOKEN",
]


def json_response(payload: Any, status_code: int = 200) -> httpx.Response:
    return httpx.Response(
        status_code=status_code,
        headers={"Content-Type": "application/json"},
        content=json.dumps(payload).encode(),
    )


def ok_handler(request: httpx.Request) -> httpx.Response:
    return json_response({"value": []})


# --- Auth mode resolution -------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("auth_mode", "expected"),
    [
        ("application", SERVICE_TOKEN_VALUE),
        ("delegated", USER_TOKEN_VALUE),
        ("auto", SERVICE_TOKEN_VALUE),
    ],
)
async def test_auth_mode_selects_its_own_token(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
    auth_mode: MicrosoftGraphAuthMode,
    expected: str,
) -> None:
    requests = install_graph_transport(ok_handler)
    with graph_secrets(
        **{
            SERVICE_TOKEN_NAME: SERVICE_TOKEN_VALUE,
            USER_TOKEN_NAME: USER_TOKEN_VALUE,
        }
    ):
        await graph.call_method(
            path="/security/alerts_v2", method="GET", auth_mode=auth_mode
        )
    assert requests[0].headers.get_list("authorization") == [f"Bearer {expected}"]


@pytest.mark.anyio
async def test_auto_auth_mode_falls_back_to_the_user_token(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
) -> None:
    requests = install_graph_transport(ok_handler)
    with graph_secrets(**{USER_TOKEN_NAME: USER_TOKEN_VALUE}):
        await graph.call_method(
            path="/security/alerts_v2", method="GET", auth_mode="auto"
        )
    assert requests[0].headers.get_list("authorization") == [
        f"Bearer {USER_TOKEN_VALUE}"
    ]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("auth_mode", "present_token_name", "present_token_value"),
    [
        ("application", USER_TOKEN_NAME, USER_TOKEN_VALUE),
        ("delegated", SERVICE_TOKEN_NAME, SERVICE_TOKEN_VALUE),
    ],
)
async def test_explicit_auth_mode_never_borrows_the_other_token(
    graph_secrets: GraphSecrets,
    auth_mode: MicrosoftGraphAuthMode,
    present_token_name: str,
    present_token_value: str,
) -> None:
    with graph_secrets(**{present_token_name: present_token_value}):
        with pytest.raises(SecretNotFoundError) as exc_info:
            await graph.call_method(
                path="/security/alerts_v2", method="GET", auth_mode=auth_mode
            )
    assert present_token_value not in str(exc_info.value)


@pytest.mark.anyio
async def test_auth_fails_when_no_graph_token_is_configured(
    graph_secrets: GraphSecrets,
) -> None:
    with graph_secrets():
        with pytest.raises(SecretNotFoundError) as exc_info:
            await graph.call_method(
                path="/security/alerts_v2", method="GET", auth_mode="auto"
            )
    message = str(exc_info.value)
    assert SERVICE_TOKEN_NAME in message
    assert USER_TOKEN_NAME in message


@pytest.mark.anyio
@pytest.mark.parametrize("auth_mode", ["application", "delegated", "auto"])
async def test_generic_graph_never_consumes_a_product_token(
    graph_secrets: GraphSecrets, auth_mode: MicrosoftGraphAuthMode
) -> None:
    """A generic Graph connection must not borrow a product-scoped token."""
    product_tokens = {name: f"{name.lower()}-value" for name in PRODUCT_TOKEN_NAMES}
    with graph_secrets(**product_tokens):
        with pytest.raises(SecretNotFoundError) as exc_info:
            await graph.call_method(
                path="/security/alerts_v2", method="GET", auth_mode=auth_mode
            )
    message = str(exc_info.value)
    for name in PRODUCT_TOKEN_NAMES:
        assert name not in message
    assert not any(value in message for value in product_tokens.values())


# --- Header ownership -----------------------------------------------------


@pytest.mark.anyio
@pytest.mark.parametrize(
    "header_name",
    ["Authorization", "authorization", "AUTHORIZATION", " Authorization ", "Host"],
)
async def test_caller_cannot_supply_sdk_owned_headers(
    graph_secrets: GraphSecrets, header_name: str
) -> None:
    with graph_secrets(**{SERVICE_TOKEN_NAME: SERVICE_TOKEN_VALUE}):
        with pytest.raises(ValueError, match="cannot be supplied by the caller"):
            await graph.call_method(
                path="/security/alerts_v2",
                method="GET",
                headers={header_name: "Bearer attacker-token"},
            )


@pytest.mark.anyio
async def test_caller_headers_do_not_duplicate_the_bearer_header(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
) -> None:
    requests = install_graph_transport(ok_handler)
    with graph_secrets(**{SERVICE_TOKEN_NAME: SERVICE_TOKEN_VALUE}):
        await graph.call_method(
            path="/security/alerts_v2",
            method="GET",
            headers={"Prefer": "include-unknown-enum-members", "Accept": None},
        )
    request = requests[0]
    assert request.headers.get_list("authorization") == [
        f"Bearer {SERVICE_TOKEN_VALUE}"
    ]
    assert request.headers["prefer"] == "include-unknown-enum-members"
    assert request.url.host == "graph.microsoft.com"


# --- Approved API roots ---------------------------------------------------


@pytest.mark.parametrize(
    ("base_url", "expected"),
    [
        (None, "https://graph.microsoft.com/v1.0"),
        ("https://graph.microsoft.com/v1.0", "https://graph.microsoft.com/v1.0"),
        ("https://graph.microsoft.us/v1.0/", "https://graph.microsoft.us/v1.0"),
        (
            "https://dod-graph.microsoft.us/v1.0",
            "https://dod-graph.microsoft.us/v1.0",
        ),
        (
            "https://microsoftgraph.chinacloudapi.cn/v1.0",
            "https://microsoftgraph.chinacloudapi.cn/v1.0",
        ),
    ],
)
def test_approved_api_roots_are_normalized(base_url: str | None, expected: str) -> None:
    assert transport.normalize_base_url(base_url) == expected


@pytest.mark.parametrize(
    "base_url",
    [
        "http://graph.microsoft.com/v1.0",
        "https://graph.microsoft.com/beta",
        "https://graph.microsoft.com/v1.0/security",
        "https://graph.microsoft.com:443/v1.0",
        "https://graph.microsoft.com:notaport/v1.0",
        "https://user:secret@graph.microsoft.com/v1.0",
        "https://graph.microsoft.com.attacker.example/v1.0",
        "https://attacker.example/graph.microsoft.com/v1.0",
        "https://graph.microsoft.com/v1.0?tenant=1",
        "https://graph.microsoft.com/v1.0#fragment",
        "https://graph.microsoft.de/v1.0",
        "https://graph.microsoft.com//v1.0",
    ],
)
def test_unapproved_api_roots_are_rejected(base_url: str) -> None:
    with pytest.raises(ValueError, match="approved Microsoft Graph v1.0 API root"):
        transport.normalize_base_url(base_url)


# --- Relative request paths -----------------------------------------------


@pytest.mark.parametrize(
    "path",
    ["/security/alerts_v2", "security/alerts_v2"],
)
def test_relative_paths_resolve_under_the_selected_root(path: str) -> None:
    assert (
        transport.build_request_url(transport.DEFAULT_BASE_URL, path, None)
        == "https://graph.microsoft.com/v1.0/security/alerts_v2"
    )


@pytest.mark.parametrize(
    "path",
    [
        "https://attacker.example/v1.0/security",
        "//attacker.example/security",
        "\\\\attacker.example\\security",
        "../beta/security",
        "security/../../beta/security",
        "%2e%2e/beta/security",
        "%2E%2E/beta/security",
        "%252e%252e/beta/security",
        "security/%2e%2E",
        "..%2Fbeta/security",
        "..%252Fbeta/security",
        "security/a%2Fb",
        "security/a%252Fb",
        "security\\alerts_v2",
        "security/alerts_v2?$top=1",
        "security/alerts_v2#fragment",
        "security//alerts_v2",
        "security/alerts_v2/",
        "   ",
    ],
)
def test_paths_that_could_escape_the_root_are_rejected(path: str) -> None:
    with pytest.raises(ValueError, match="Microsoft Graph"):
        transport.build_request_url(transport.DEFAULT_BASE_URL, path, None)


@pytest.mark.anyio
async def test_unsupported_http_methods_are_rejected(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
) -> None:
    install_graph_transport(ok_handler)
    with graph_secrets(**{SERVICE_TOKEN_NAME: SERVICE_TOKEN_VALUE}):
        with pytest.raises(ValueError, match="Unsupported Microsoft Graph HTTP method"):
            await graph.call_method(path="/security/alerts_v2", method="TRACE")


# --- Query parameter encoding ---------------------------------------------


@pytest.mark.anyio
async def test_only_none_query_parameters_are_dropped(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
) -> None:
    requests = install_graph_transport(ok_handler)
    with graph_secrets(**{SERVICE_TOKEN_NAME: SERVICE_TOKEN_VALUE}):
        await graph.call_method(
            path="/security/alerts_v2",
            method="GET",
            params={
                "$top": 0,
                "$count": False,
                "$filter": "",
                "$expand": [],
                "$skip": None,
            },
        )
    params = requests[0].url.params
    assert params.get_list("$top") == ["0"]
    assert params.get_list("$count") == ["false"]
    assert params.get_list("$filter") == [""]
    assert "$skip" not in params
    assert "$expand" not in params


@pytest.mark.anyio
async def test_list_query_parameters_repeat_the_key(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
) -> None:
    requests = install_graph_transport(ok_handler)
    with graph_secrets(**{SERVICE_TOKEN_NAME: SERVICE_TOKEN_VALUE}):
        await graph.call_method(
            path="/security/alerts_v2",
            method="GET",
            params={"ids": ["a", "b"], "$filter": "status eq 'new'"},
        )
    params = requests[0].url.params
    assert params.get_list("ids") == ["a", "b"]
    assert params.get_list("$filter") == ["status eq 'new'"]


# --- JSON serialization and parsing ---------------------------------------


@pytest.mark.anyio
async def test_payload_preserves_explicit_nulls(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
) -> None:
    requests = install_graph_transport(ok_handler)
    payload = {"assignedTo": None, "customDetails": {"reviewer": None}, "tags": []}
    with graph_secrets(**{SERVICE_TOKEN_NAME: SERVICE_TOKEN_VALUE}):
        await graph.call_method(
            path="/security/alerts_v2/alert-1", method="PATCH", payload=payload
        )
    request = requests[0]
    assert json.loads(request.content) == payload
    assert request.headers["content-type"] == "application/json"


@pytest.mark.anyio
async def test_omit_none_payload_fields_only_drops_top_level_nulls(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
) -> None:
    requests = install_graph_transport(ok_handler)
    with graph_secrets(**{SERVICE_TOKEN_NAME: SERVICE_TOKEN_VALUE}):
        await graph.call_method(
            path="/security/incidents/mergeIncidents",
            method="POST",
            payload={
                "incidentComment": None,
                "incidentIds": ["1"],
                "nested": {"keepMe": None},
            },
            omit_none_payload_fields=True,
        )
    assert json.loads(requests[0].content) == {
        "incidentIds": ["1"],
        "nested": {"keepMe": None},
    }


@pytest.mark.anyio
async def test_json_response_is_returned_untouched(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
) -> None:
    body = {
        "@odata.context": "https://graph.microsoft.com/v1.0/$metadata#security/alerts_v2",
        "value": [
            {
                "id": "da637551227677560813_-961444813",
                "createdDateTime": "2021-04-27T12:19:27.7211305Z",
                "tenantId": "b3c1b5fc-828c-45fa-a1e1-10d74f6d6e9c",
                "assignedTo": None,
                "mitreTechniques": ["T1564.001"],
            }
        ],
    }
    install_graph_transport(lambda request: json_response(body))
    with graph_secrets(**{SERVICE_TOKEN_NAME: SERVICE_TOKEN_VALUE}):
        result = await graph.call_method(path="/security/alerts_v2", method="GET")
    assert result == body
    assert isinstance(result["value"][0]["createdDateTime"], str)
    assert isinstance(result["value"][0]["tenantId"], str)


@pytest.mark.anyio
@pytest.mark.parametrize("status_code", [204, 304])
async def test_no_content_responses_return_none(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
    status_code: int,
) -> None:
    install_graph_transport(lambda request: httpx.Response(status_code=status_code))
    with graph_secrets(**{SERVICE_TOKEN_NAME: SERVICE_TOKEN_VALUE}):
        result = await graph.call_method(
            path="/security/alerts_v2/alert-1", method="DELETE"
        )
    assert result is None


@pytest.mark.anyio
async def test_graph_errors_surface_the_provider_body(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
) -> None:
    install_graph_transport(
        lambda request: json_response(
            {"error": {"code": "Forbidden", "message": "Insufficient privileges."}},
            status_code=403,
        )
    )
    with graph_secrets(**{SERVICE_TOKEN_NAME: SERVICE_TOKEN_VALUE}):
        with pytest.raises(Exception, match="Insufficient privileges") as exc_info:
            await graph.call_method(path="/security/alerts_v2", method="GET")
    assert SERVICE_TOKEN_VALUE not in str(exc_info.value)


# --- Pagination -----------------------------------------------------------


def paged_handler(pages: list[dict[str, Any]]) -> GraphHandler:
    """Serve `pages` in order, one per request."""
    remaining = list(pages)

    def _handler(request: httpx.Request) -> httpx.Response:
        return json_response(remaining.pop(0))

    return _handler


@pytest.mark.anyio
async def test_pagination_flattens_pages_in_order(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
) -> None:
    next_link = "https://graph.microsoft.com/v1.0/security/alerts_v2?$skiptoken=page2"
    requests = install_graph_transport(
        paged_handler(
            [
                {"value": [{"id": "1"}, {"id": "2"}], "@odata.nextLink": next_link},
                {"value": [{"id": "3"}]},
            ]
        )
    )
    with graph_secrets(**{SERVICE_TOKEN_NAME: SERVICE_TOKEN_VALUE}):
        result = await graph.call_paginated_method(path="/security/alerts_v2")
    assert result == [{"id": "1"}, {"id": "2"}, {"id": "3"}]
    assert len(requests) == 2
    assert requests[1].url.params.get("$skiptoken") == "page2"


@pytest.mark.anyio
async def test_pagination_flattens_only_the_value_collection(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
) -> None:
    install_graph_transport(
        paged_handler([{"value": [{"id": "1"}], "comments": [{"id": "ignored"}]}])
    )
    with graph_secrets(**{SERVICE_TOKEN_NAME: SERVICE_TOKEN_VALUE}):
        result = await graph.call_paginated_method(path="/security/alerts_v2")
    assert result == [{"id": "1"}]


@pytest.mark.anyio
async def test_pagination_stops_once_the_limit_is_reached(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
) -> None:
    next_link = "https://graph.microsoft.com/v1.0/security/alerts_v2?$skiptoken=page2"
    requests = install_graph_transport(
        paged_handler(
            [
                {"value": [{"id": "1"}, {"id": "2"}], "@odata.nextLink": next_link},
                {"value": [{"id": "3"}]},
            ]
        )
    )
    with graph_secrets(**{SERVICE_TOKEN_NAME: SERVICE_TOKEN_VALUE}):
        result = await graph.call_paginated_method(path="/security/alerts_v2", limit=1)
    assert result == [{"id": "1"}]
    assert len(requests) == 1


@pytest.mark.anyio
async def test_pagination_does_not_over_fetch_on_an_exact_limit(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
) -> None:
    next_link = "https://graph.microsoft.com/v1.0/security/alerts_v2?$skiptoken=page2"
    requests = install_graph_transport(
        paged_handler(
            [
                {"value": [{"id": "1"}, {"id": "2"}], "@odata.nextLink": next_link},
                {"value": [{"id": "3"}]},
            ]
        )
    )
    with graph_secrets(**{SERVICE_TOKEN_NAME: SERVICE_TOKEN_VALUE}):
        result = await graph.call_paginated_method(path="/security/alerts_v2", limit=2)
    assert result == [{"id": "1"}, {"id": "2"}]
    assert len(requests) == 1


@pytest.mark.anyio
async def test_pagination_without_a_limit_follows_every_next_link(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
) -> None:
    next_link = "https://graph.microsoft.com/v1.0/security/alerts_v2?$skiptoken=page2"
    requests = install_graph_transport(
        paged_handler(
            [
                {"value": [{"id": "1"}], "@odata.nextLink": next_link},
                {"value": [{"id": "2"}]},
            ]
        )
    )
    with graph_secrets(**{SERVICE_TOKEN_NAME: SERVICE_TOKEN_VALUE}):
        result = await graph.call_paginated_method(
            path="/security/alerts_v2", limit=None
        )
    assert result == [{"id": "1"}, {"id": "2"}]
    assert len(requests) == 2


@pytest.mark.anyio
@pytest.mark.parametrize("limit", [0, -1])
async def test_pagination_rejects_a_non_positive_limit(
    graph_secrets: GraphSecrets, limit: int
) -> None:
    with graph_secrets(**{SERVICE_TOKEN_NAME: SERVICE_TOKEN_VALUE}):
        with pytest.raises(ValueError, match="must be a positive integer"):
            await graph.call_paginated_method(path="/security/alerts_v2", limit=limit)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "next_link",
    [
        "https://graph.microsoft.us/v1.0/security/alerts_v2?$skiptoken=2",
        "https://graph.microsoft.com/beta/security/alerts_v2?$skiptoken=2",
    ],
)
async def test_pagination_refuses_hostile_next_links(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
    next_link: str,
) -> None:
    requests = install_graph_transport(
        paged_handler([{"value": [{"id": "1"}], "@odata.nextLink": next_link}])
    )
    with graph_secrets(**{SERVICE_TOKEN_NAME: SERVICE_TOKEN_VALUE}):
        with pytest.raises(ValueError, match="must stay on"):
            await graph.call_paginated_method(path="/security/alerts_v2")
    assert len(requests) == 1


@pytest.mark.parametrize(
    "next_link",
    [
        "https://attacker.example/v1.0/security/alerts_v2",
        "http://graph.microsoft.com/v1.0/security/alerts_v2",
        "https://graph.microsoft.com:8443/v1.0/security/alerts_v2",
        "https://user:secret@graph.microsoft.com/v1.0/security/alerts_v2",
        "https://graph.microsoft.com/beta/security/alerts_v2",
        "https://graph.microsoft.com/v2.0/security/alerts_v2",
        "https://graph.microsoft.com/v1.0/../beta/security/alerts_v2",
        "https://graph.microsoft.com/v1.0/security/%2e%2e%2fbeta/alerts_v2",
        "https://graph.microsoft.com/v1.0/security/%252e%252e/alerts_v2",
        "https://graph.microsoft.com/v1.0/security/a%2Fb",
        "https://graph.microsoft.com/v1.0/security/a%252Fb",
        "https://graph.microsoft.com/v1.0/security\\alerts_v2",
        "https://graph.microsoft.com/v1.0/security//alerts_v2",
        "https://graph.microsoft.com/v1.0/security/alerts_v2/",
        "https://graph.microsoft.com/v1.0",
        "",
    ],
)
def test_next_link_validation_rejects_root_changes(next_link: str) -> None:
    with pytest.raises(ValueError):
        transport.validate_continuation_url(next_link, transport.DEFAULT_BASE_URL)


def test_next_link_validation_accepts_the_selected_root() -> None:
    next_link = "https://graph.microsoft.com/v1.0/security/alerts_v2?$skiptoken=abc"
    assert (
        transport.validate_continuation_url(next_link, transport.DEFAULT_BASE_URL)
        == next_link
    )


# --- Opaque continuation URLs --------------------------------------------


@pytest.mark.anyio
async def test_continuation_method_preserves_the_complete_url_and_raw_page(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
) -> None:
    continuation_url = (
        "https://graph.microsoft.com/v1.0/users/user-1/mailFolders/inbox/"
        "messages/delta?$deltatoken=opaque%2Btoken%3D%3D&$select=id,subject"
    )
    page = {
        "value": [{"id": "message-1"}],
        "@odata.deltaLink": continuation_url,
    }
    requests = install_graph_transport(lambda request: json_response(page))

    with graph_secrets(**{SERVICE_TOKEN_NAME: SERVICE_TOKEN_VALUE}):
        result = await graph.call_continuation_method(continuation_url=continuation_url)

    assert result == page
    assert str(requests[0].url) == continuation_url


@pytest.mark.anyio
@pytest.mark.parametrize(
    "continuation_url",
    [
        "https://attacker.example/v1.0/users?$skiptoken=2",
        "https://graph.microsoft.com/beta/users?$skiptoken=2",
    ],
)
async def test_continuation_method_rejects_root_changes_before_sending(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
    continuation_url: str,
) -> None:
    requests = install_graph_transport(ok_handler)

    with graph_secrets(**{SERVICE_TOKEN_NAME: SERVICE_TOKEN_VALUE}):
        with pytest.raises(ValueError, match="must stay on"):
            await graph.call_continuation_method(continuation_url=continuation_url)

    assert requests == []


# --- Retry safety ---------------------------------------------------------


@pytest.mark.anyio
async def test_mutating_requests_are_not_retried(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
) -> None:
    requests = install_graph_transport(
        lambda request: json_response(
            {"error": {"code": "ServiceUnavailable", "message": "Try later."}},
            status_code=503,
        )
    )

    with graph_secrets(**{SERVICE_TOKEN_NAME: SERVICE_TOKEN_VALUE}):
        with pytest.raises(Exception, match="Try later"):
            await graph.call_method(
                path="/security/incidents/incident-1",
                method="PATCH",
                payload={"status": "active"},
            )

    assert len(requests) == 1


@pytest.mark.anyio
async def test_safe_requests_retain_sdk_retry_behavior(
    install_graph_transport: InstallGraphTransport,
    graph_secrets: GraphSecrets,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    responses = [
        json_response(
            {"error": {"code": "ServiceUnavailable", "message": "Try later."}},
            status_code=503,
        ),
        json_response({"value": [{"id": "alert-1"}]}),
    ]

    async def no_sleep(delay: float) -> None:
        assert delay >= 0

    monkeypatch.setattr("kiota_http.middleware.retry_handler.asyncio.sleep", no_sleep)
    requests = install_graph_transport(lambda request: responses.pop(0))

    with graph_secrets(**{SERVICE_TOKEN_NAME: SERVICE_TOKEN_VALUE}):
        result = await graph.call_method(path="/security/alerts_v2", method="GET")

    assert result == {"value": [{"id": "alert-1"}]}
    assert len(requests) == 2
