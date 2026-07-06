"""Freshservice REST API integrations."""

from collections.abc import Mapping
from typing import Annotated, Any, Literal, TypedDict, cast
from urllib.parse import parse_qs, urlparse, urlunparse

import httpx
from pydantic import Field

from tracecat_registry import RegistrySecret, SecretNotFoundError, registry, secrets

FRESHSERVICE_API_DOC_URL = "https://api.freshservice.com/"
DEFAULT_TIMEOUT_SECONDS = 30.0

type FreshserviceMethod = Literal["GET", "POST", "PUT", "PATCH", "DELETE"]

BaseUrlParam = Annotated[
    str | None,
    Field(
        ...,
        description=(
            "Freshservice tenant URL or API URL. Defaults to "
            "`FRESHSERVICE_BASE_URL`, e.g. `https://example.freshservice.com`."
        ),
    ),
]
QueryParamsParam = Annotated[
    dict[str, Any] | None,
    Field(..., description="Freshservice query parameters."),
]
RequestBodyParam = Annotated[
    dict[str, Any] | None,
    Field(..., description="Freshservice JSON request body."),
]
PerPageParam = Annotated[
    int,
    Field(..., ge=1, le=100, description="Freshservice `per_page` value."),
]
MaxPagesParam = Annotated[
    int | None,
    Field(..., ge=1, description="Maximum pages to fetch."),
]


class FreshservicePaginatedResult(TypedDict):
    items: list[Any]
    pages: int
    next_page: int | None


freshservice_secret = RegistrySecret(
    name="freshservice",
    keys=["FRESHSERVICE_API_KEY"],
    optional_keys=["FRESHSERVICE_BASE_URL"],
)
"""Freshservice API credentials.

- name: `freshservice`
- keys:
    - `FRESHSERVICE_API_KEY`
- optional keys:
    - `FRESHSERVICE_BASE_URL`
"""


def _drop_none(params: Mapping[str, Any]) -> dict[str, Any]:
    return {key: value for key, value in params.items() if value is not None}


def _resolve_base_url(base_url: str | None) -> str:
    resolved = (
        base_url or secrets.get_or_default("FRESHSERVICE_BASE_URL") or ""
    ).strip()
    if not resolved:
        raise SecretNotFoundError(
            "Freshservice calls require `base_url` or `FRESHSERVICE_BASE_URL`, "
            "e.g. `https://example.freshservice.com`."
        )

    if "://" not in resolved:
        resolved = f"https://{resolved}"

    parsed = urlparse(resolved.rstrip("/"))
    path = parsed.path.rstrip("/")
    if path == "/api/v2":
        return urlunparse(parsed._replace(path=path))
    if not path:
        return f"{urlunparse(parsed._replace(path=''))}/api/v2"
    return f"{urlunparse(parsed._replace(path=path))}/api/v2"


def _normalize_path(path: str) -> str:
    stripped = path.strip()
    if not stripped:
        raise ValueError("Freshservice API path cannot be empty.")
    parsed = urlparse(stripped)
    if parsed.scheme or parsed.netloc:
        raise ValueError(
            "Freshservice API path must be relative, e.g. `/tickets`, not a URL."
        )
    if stripped == "/api/v2":
        return ""
    if stripped.startswith("/api/v2/"):
        stripped = stripped.removeprefix("/api/v2")
    if not stripped.startswith("/"):
        stripped = f"/{stripped}"
    return stripped


def _decode_response(response: httpx.Response) -> dict[str, Any] | list[Any]:
    response.raise_for_status()
    if not response.content:
        return {"status": "success", "status_code": response.status_code}
    try:
        return cast(dict[str, Any] | list[Any], response.json())
    except ValueError:
        return {"status_code": response.status_code, "body": response.text}


async def _request_freshservice_api(
    *,
    method: FreshserviceMethod,
    path: str,
    base_url: str | None = None,
    query_params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any] | list[Any]:
    response, _ = await _request_freshservice_http(
        method=method,
        path=path,
        base_url=base_url,
        query_params=query_params,
        json_body=json_body,
        headers=headers,
        timeout=timeout,
    )
    return response


async def _request_freshservice_http(
    *,
    method: FreshserviceMethod,
    path: str,
    base_url: str | None = None,
    query_params: dict[str, Any] | None = None,
    json_body: dict[str, Any] | None = None,
    headers: dict[str, str] | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> tuple[dict[str, Any] | list[Any], httpx.Headers]:
    resolved_base_url = _resolve_base_url(base_url)
    url = f"{resolved_base_url}{_normalize_path(path)}"
    request_headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
    }
    if headers:
        request_headers.update(headers)

    async with httpx.AsyncClient(
        auth=httpx.BasicAuth(secrets.get("FRESHSERVICE_API_KEY"), "X"),
        timeout=timeout,
    ) as client:
        response = await client.request(
            method=method,
            url=url,
            params=query_params,
            json=json_body,
            headers=request_headers,
        )
    return _decode_response(response), response.headers


def _extract_page_items(
    response: dict[str, Any] | list[Any],
    items_key: str | None,
) -> list[Any]:
    if isinstance(response, list):
        return response

    if items_key is not None:
        if items_key not in response:
            raise ValueError(f"Freshservice response field `{items_key}` is missing.")
        value = response[items_key]
        if not isinstance(value, list):
            raise ValueError(
                f"Freshservice response field `{items_key}` is not a list."
            )
        return value

    list_fields = [key for key, value in response.items() if isinstance(value, list)]
    if len(list_fields) == 1:
        return cast(list[Any], response[list_fields[0]])
    if not list_fields:
        raise ValueError(
            "Freshservice paginated response did not contain a list field; "
            "pass `items_key` explicitly."
        )
    fields = ", ".join(sorted(list_fields))
    raise ValueError(
        "Freshservice paginated response contained multiple list fields "
        f"({fields}); pass `items_key` explicitly."
    )


def _extract_next_page(headers: Mapping[str, str], fallback_page: int) -> int | None:
    link_header = headers.get("link") or headers.get("Link")
    if not link_header:
        return None

    for part in link_header.split(","):
        if 'rel="next"' not in part and "rel=next" not in part:
            continue
        start = part.find("<")
        end = part.find(">", start + 1)
        if start == -1 or end == -1:
            return fallback_page
        query = parse_qs(urlparse(part[start + 1 : end]).query)
        page_values = query.get("page")
        if not page_values:
            return fallback_page
        try:
            return int(page_values[0])
        except ValueError:
            return fallback_page
    return None


async def _call_paginated_endpoint(
    *,
    path: str,
    query_params: dict[str, Any] | None = None,
    items_key: str | None = None,
    page: int = 1,
    per_page: int = 30,
    max_pages: int | None = None,
    base_url: str | None = None,
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
) -> FreshservicePaginatedResult:
    items: list[Any] = []
    pages = 0
    current_page = page

    while True:
        params = dict(query_params or {})
        params["page"] = current_page
        params["per_page"] = per_page
        response, headers = await _request_freshservice_http(
            method="GET",
            path=path,
            base_url=base_url,
            query_params=params,
            timeout=timeout,
        )
        page_items = _extract_page_items(response, items_key)
        items.extend(page_items)
        pages += 1

        next_page = _extract_next_page(headers, current_page + 1)
        if next_page is None or (max_pages is not None and pages >= max_pages):
            return {"items": items, "pages": pages, "next_page": next_page}
        current_page = next_page


@registry.register(
    default_title="Call endpoint",
    description="Call a Freshservice REST API endpoint with API-key Basic auth.",
    display_group="Freshservice",
    doc_url=FRESHSERVICE_API_DOC_URL,
    namespace="tools.freshservice",
    secrets=[freshservice_secret],
)
async def call_endpoint(
    method: Annotated[
        FreshserviceMethod,
        Field(..., description="HTTP method for the Freshservice API request."),
    ],
    path: Annotated[
        str,
        Field(..., description="Freshservice API path, e.g. `/tickets`."),
    ],
    query_params: QueryParamsParam = None,
    json_body: RequestBodyParam = None,
    headers: Annotated[
        dict[str, str] | None,
        Field(..., description="Additional HTTP headers to send."),
    ] = None,
    base_url: BaseUrlParam = None,
    timeout: Annotated[
        float,
        Field(..., ge=1, le=300, description="Request timeout in seconds."),
    ] = DEFAULT_TIMEOUT_SECONDS,
) -> dict[str, Any] | list[Any]:
    return await _request_freshservice_api(
        method=method,
        path=path,
        base_url=base_url,
        query_params=query_params,
        json_body=json_body,
        headers=headers,
        timeout=timeout,
    )


@registry.register(
    default_title="Call paginated endpoint",
    description="Call a Freshservice list endpoint and follow page/per_page pagination.",
    display_group="Freshservice",
    doc_url=FRESHSERVICE_API_DOC_URL,
    namespace="tools.freshservice",
    secrets=[freshservice_secret],
)
async def call_paginated_endpoint(
    path: Annotated[
        str,
        Field(..., description="Freshservice list API path, e.g. `/tickets`."),
    ],
    query_params: QueryParamsParam = None,
    items_key: Annotated[
        str | None,
        Field(
            ...,
            description=(
                "Response field containing the page items, e.g. `tickets`. "
                "If omitted, Tracecat infers the only list-valued response field."
            ),
        ),
    ] = None,
    page: Annotated[
        int,
        Field(..., ge=1, description="Freshservice page number to start from."),
    ] = 1,
    per_page: PerPageParam = 30,
    max_pages: MaxPagesParam = None,
    base_url: BaseUrlParam = None,
    timeout: Annotated[
        float,
        Field(..., ge=1, le=300, description="Request timeout in seconds."),
    ] = DEFAULT_TIMEOUT_SECONDS,
) -> FreshservicePaginatedResult:
    return await _call_paginated_endpoint(
        path=path,
        query_params=query_params,
        items_key=items_key,
        page=page,
        per_page=per_page,
        max_pages=max_pages,
        base_url=base_url,
        timeout=timeout,
    )


@registry.register(
    default_title="List tickets",
    description="List Freshservice tickets with optional filters.",
    display_group="Freshservice Tickets",
    doc_url=FRESHSERVICE_API_DOC_URL,
    namespace="tools.freshservice",
    secrets=[freshservice_secret],
)
async def list_tickets(
    query_params: QueryParamsParam = None,
    page: Annotated[
        int,
        Field(..., ge=1, description="Freshservice page number to start from."),
    ] = 1,
    per_page: PerPageParam = 30,
    max_pages: MaxPagesParam = None,
    base_url: BaseUrlParam = None,
) -> FreshservicePaginatedResult:
    return await _call_paginated_endpoint(
        path="/tickets",
        query_params=query_params,
        items_key="tickets",
        page=page,
        per_page=per_page,
        max_pages=max_pages,
        base_url=base_url,
    )


@registry.register(
    default_title="Get ticket",
    description="Get a Freshservice ticket by ID.",
    display_group="Freshservice Tickets",
    doc_url=FRESHSERVICE_API_DOC_URL,
    namespace="tools.freshservice",
    secrets=[freshservice_secret],
)
async def get_ticket(
    ticket_id: Annotated[int, Field(..., description="Freshservice ticket ID.")],
    include: Annotated[
        str | None,
        Field(..., description="Optional Freshservice include parameter."),
    ] = None,
    base_url: BaseUrlParam = None,
) -> dict[str, Any] | list[Any]:
    return await _request_freshservice_api(
        method="GET",
        path=f"/tickets/{ticket_id}",
        query_params=_drop_none({"include": include}) or None,
        base_url=base_url,
    )


@registry.register(
    default_title="Create ticket",
    description="Create a Freshservice ticket.",
    display_group="Freshservice Tickets",
    doc_url=FRESHSERVICE_API_DOC_URL,
    namespace="tools.freshservice",
    secrets=[freshservice_secret],
)
async def create_ticket(
    ticket: Annotated[
        dict[str, Any],
        Field(
            ...,
            description=(
                "Freshservice ticket payload, e.g. subject, description, "
                "email/requester_id, priority, and status."
            ),
        ),
    ],
    base_url: BaseUrlParam = None,
) -> dict[str, Any] | list[Any]:
    return await _request_freshservice_api(
        method="POST",
        path="/tickets",
        json_body=ticket,
        base_url=base_url,
    )


@registry.register(
    default_title="Update ticket",
    description="Update a Freshservice ticket.",
    display_group="Freshservice Tickets",
    doc_url=FRESHSERVICE_API_DOC_URL,
    namespace="tools.freshservice",
    secrets=[freshservice_secret],
)
async def update_ticket(
    ticket_id: Annotated[int, Field(..., description="Freshservice ticket ID.")],
    ticket: Annotated[
        dict[str, Any],
        Field(..., description="Freshservice ticket fields to update."),
    ],
    base_url: BaseUrlParam = None,
) -> dict[str, Any] | list[Any]:
    return await _request_freshservice_api(
        method="PUT",
        path=f"/tickets/{ticket_id}",
        json_body=ticket,
        base_url=base_url,
    )


@registry.register(
    default_title="Delete ticket",
    description="Delete a Freshservice ticket.",
    display_group="Freshservice Tickets",
    doc_url=FRESHSERVICE_API_DOC_URL,
    namespace="tools.freshservice",
    secrets=[freshservice_secret],
)
async def delete_ticket(
    ticket_id: Annotated[int, Field(..., description="Freshservice ticket ID.")],
    base_url: BaseUrlParam = None,
) -> dict[str, Any] | list[Any]:
    return await _request_freshservice_api(
        method="DELETE",
        path=f"/tickets/{ticket_id}",
        base_url=base_url,
    )


@registry.register(
    default_title="Create ticket note",
    description="Add a note to a Freshservice ticket.",
    display_group="Freshservice Ticket Conversations",
    doc_url=FRESHSERVICE_API_DOC_URL,
    namespace="tools.freshservice",
    secrets=[freshservice_secret],
)
async def create_ticket_note(
    ticket_id: Annotated[int, Field(..., description="Freshservice ticket ID.")],
    note: Annotated[
        dict[str, Any],
        Field(..., description="Freshservice note payload."),
    ],
    base_url: BaseUrlParam = None,
) -> dict[str, Any] | list[Any]:
    return await _request_freshservice_api(
        method="POST",
        path=f"/tickets/{ticket_id}/notes",
        json_body=note,
        base_url=base_url,
    )


@registry.register(
    default_title="Reply to ticket",
    description="Add a reply to a Freshservice ticket.",
    display_group="Freshservice Ticket Conversations",
    doc_url=FRESHSERVICE_API_DOC_URL,
    namespace="tools.freshservice",
    secrets=[freshservice_secret],
)
async def reply_to_ticket(
    ticket_id: Annotated[int, Field(..., description="Freshservice ticket ID.")],
    reply: Annotated[
        dict[str, Any],
        Field(..., description="Freshservice ticket reply payload."),
    ],
    base_url: BaseUrlParam = None,
) -> dict[str, Any] | list[Any]:
    return await _request_freshservice_api(
        method="POST",
        path=f"/tickets/{ticket_id}/reply",
        json_body=reply,
        base_url=base_url,
    )


@registry.register(
    default_title="List requesters",
    description="List Freshservice requesters with optional filters.",
    display_group="Freshservice Requesters",
    doc_url=FRESHSERVICE_API_DOC_URL,
    namespace="tools.freshservice",
    secrets=[freshservice_secret],
)
async def list_requesters(
    query_params: QueryParamsParam = None,
    page: Annotated[
        int,
        Field(..., ge=1, description="Freshservice page number to start from."),
    ] = 1,
    per_page: PerPageParam = 30,
    max_pages: MaxPagesParam = None,
    base_url: BaseUrlParam = None,
) -> FreshservicePaginatedResult:
    return await _call_paginated_endpoint(
        path="/requesters",
        query_params=query_params,
        items_key="requesters",
        page=page,
        per_page=per_page,
        max_pages=max_pages,
        base_url=base_url,
    )


@registry.register(
    default_title="Get requester",
    description="Get a Freshservice requester by ID.",
    display_group="Freshservice Requesters",
    doc_url=FRESHSERVICE_API_DOC_URL,
    namespace="tools.freshservice",
    secrets=[freshservice_secret],
)
async def get_requester(
    requester_id: Annotated[int, Field(..., description="Freshservice requester ID.")],
    base_url: BaseUrlParam = None,
) -> dict[str, Any] | list[Any]:
    return await _request_freshservice_api(
        method="GET",
        path=f"/requesters/{requester_id}",
        base_url=base_url,
    )


@registry.register(
    default_title="Create requester",
    description="Create a Freshservice requester.",
    display_group="Freshservice Requesters",
    doc_url=FRESHSERVICE_API_DOC_URL,
    namespace="tools.freshservice",
    secrets=[freshservice_secret],
)
async def create_requester(
    requester: Annotated[
        dict[str, Any],
        Field(..., description="Freshservice requester payload."),
    ],
    base_url: BaseUrlParam = None,
) -> dict[str, Any] | list[Any]:
    return await _request_freshservice_api(
        method="POST",
        path="/requesters",
        json_body=requester,
        base_url=base_url,
    )


@registry.register(
    default_title="Update requester",
    description="Update a Freshservice requester.",
    display_group="Freshservice Requesters",
    doc_url=FRESHSERVICE_API_DOC_URL,
    namespace="tools.freshservice",
    secrets=[freshservice_secret],
)
async def update_requester(
    requester_id: Annotated[int, Field(..., description="Freshservice requester ID.")],
    requester: Annotated[
        dict[str, Any],
        Field(..., description="Freshservice requester fields to update."),
    ],
    base_url: BaseUrlParam = None,
) -> dict[str, Any] | list[Any]:
    return await _request_freshservice_api(
        method="PUT",
        path=f"/requesters/{requester_id}",
        json_body=requester,
        base_url=base_url,
    )


@registry.register(
    default_title="List agents",
    description="List Freshservice agents with optional filters.",
    display_group="Freshservice Agents",
    doc_url=FRESHSERVICE_API_DOC_URL,
    namespace="tools.freshservice",
    secrets=[freshservice_secret],
)
async def list_agents(
    query_params: QueryParamsParam = None,
    page: Annotated[
        int,
        Field(..., ge=1, description="Freshservice page number to start from."),
    ] = 1,
    per_page: PerPageParam = 30,
    max_pages: MaxPagesParam = None,
    base_url: BaseUrlParam = None,
) -> FreshservicePaginatedResult:
    return await _call_paginated_endpoint(
        path="/agents",
        query_params=query_params,
        items_key="agents",
        page=page,
        per_page=per_page,
        max_pages=max_pages,
        base_url=base_url,
    )


@registry.register(
    default_title="Get agent",
    description="Get a Freshservice agent by ID.",
    display_group="Freshservice Agents",
    doc_url=FRESHSERVICE_API_DOC_URL,
    namespace="tools.freshservice",
    secrets=[freshservice_secret],
)
async def get_agent(
    agent_id: Annotated[int, Field(..., description="Freshservice agent ID.")],
    base_url: BaseUrlParam = None,
) -> dict[str, Any] | list[Any]:
    return await _request_freshservice_api(
        method="GET",
        path=f"/agents/{agent_id}",
        base_url=base_url,
    )


@registry.register(
    default_title="List groups",
    description="List Freshservice groups with optional filters.",
    display_group="Freshservice Groups",
    doc_url=FRESHSERVICE_API_DOC_URL,
    namespace="tools.freshservice",
    secrets=[freshservice_secret],
)
async def list_groups(
    query_params: QueryParamsParam = None,
    page: Annotated[
        int,
        Field(..., ge=1, description="Freshservice page number to start from."),
    ] = 1,
    per_page: PerPageParam = 30,
    max_pages: MaxPagesParam = None,
    base_url: BaseUrlParam = None,
) -> FreshservicePaginatedResult:
    return await _call_paginated_endpoint(
        path="/groups",
        query_params=query_params,
        items_key="groups",
        page=page,
        per_page=per_page,
        max_pages=max_pages,
        base_url=base_url,
    )


@registry.register(
    default_title="Get group",
    description="Get a Freshservice group by ID.",
    display_group="Freshservice Groups",
    doc_url=FRESHSERVICE_API_DOC_URL,
    namespace="tools.freshservice",
    secrets=[freshservice_secret],
)
async def get_group(
    group_id: Annotated[int, Field(..., description="Freshservice group ID.")],
    base_url: BaseUrlParam = None,
) -> dict[str, Any] | list[Any]:
    return await _request_freshservice_api(
        method="GET",
        path=f"/groups/{group_id}",
        base_url=base_url,
    )


@registry.register(
    default_title="List changes",
    description="List Freshservice changes with optional filters.",
    display_group="Freshservice Changes",
    doc_url=FRESHSERVICE_API_DOC_URL,
    namespace="tools.freshservice",
    secrets=[freshservice_secret],
)
async def list_changes(
    query_params: QueryParamsParam = None,
    page: Annotated[
        int,
        Field(..., ge=1, description="Freshservice page number to start from."),
    ] = 1,
    per_page: PerPageParam = 30,
    max_pages: MaxPagesParam = None,
    base_url: BaseUrlParam = None,
) -> FreshservicePaginatedResult:
    return await _call_paginated_endpoint(
        path="/changes",
        query_params=query_params,
        items_key="changes",
        page=page,
        per_page=per_page,
        max_pages=max_pages,
        base_url=base_url,
    )


@registry.register(
    default_title="Get change",
    description="Get a Freshservice change by ID.",
    display_group="Freshservice Changes",
    doc_url=FRESHSERVICE_API_DOC_URL,
    namespace="tools.freshservice",
    secrets=[freshservice_secret],
)
async def get_change(
    change_id: Annotated[int, Field(..., description="Freshservice change ID.")],
    base_url: BaseUrlParam = None,
) -> dict[str, Any] | list[Any]:
    return await _request_freshservice_api(
        method="GET",
        path=f"/changes/{change_id}",
        base_url=base_url,
    )


@registry.register(
    default_title="Create change",
    description="Create a Freshservice change.",
    display_group="Freshservice Changes",
    doc_url=FRESHSERVICE_API_DOC_URL,
    namespace="tools.freshservice",
    secrets=[freshservice_secret],
)
async def create_change(
    change: Annotated[
        dict[str, Any],
        Field(..., description="Freshservice change payload."),
    ],
    base_url: BaseUrlParam = None,
) -> dict[str, Any] | list[Any]:
    return await _request_freshservice_api(
        method="POST",
        path="/changes",
        json_body=change,
        base_url=base_url,
    )


@registry.register(
    default_title="Update change",
    description="Update a Freshservice change.",
    display_group="Freshservice Changes",
    doc_url=FRESHSERVICE_API_DOC_URL,
    namespace="tools.freshservice",
    secrets=[freshservice_secret],
)
async def update_change(
    change_id: Annotated[int, Field(..., description="Freshservice change ID.")],
    change: Annotated[
        dict[str, Any],
        Field(..., description="Freshservice change fields to update."),
    ],
    base_url: BaseUrlParam = None,
) -> dict[str, Any] | list[Any]:
    return await _request_freshservice_api(
        method="PUT",
        path=f"/changes/{change_id}",
        json_body=change,
        base_url=base_url,
    )
