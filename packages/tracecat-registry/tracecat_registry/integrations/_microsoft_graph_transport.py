"""Shared Microsoft Graph v1.0 transport for the unified Graph SDK.

This module is internal: it registers no actions. It holds the single hardened
request pipeline used by the unified `microsoft_graph_sdk`, so the URL, header,
national-cloud, traversal, null and pagination-origin protections exist in
exactly one place.

Requests are built with Kiota primitives and sent through the `msgraph-core`
request adapter so that the Graph middleware pipeline (retry, redirect header
scrubbing, telemetry) always applies. JSON bodies are written with
`microsoft-kiota-serialization-json` and responses are handed back exactly as
Microsoft Graph returned them.

Each SDK call supplies a `GraphProduct`, which names the ordered OAuth tokens
that product may use. A product-specific token always wins over the generic
Microsoft Graph token, and products never see each other's tokens.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Annotated, Any, Literal, cast
from urllib.parse import quote, unquote, urlencode, urlsplit

import httpx
from kiota_abstractions.api_error import APIError
from kiota_abstractions.authentication import (
    AccessTokenProvider,
    AllowedHostsValidator,
    BaseBearerTokenAuthenticationProvider,
)
from kiota_abstractions.method import Method
from kiota_abstractions.native_response_handler import NativeResponseHandler
from kiota_abstractions.request_information import RequestInformation
from kiota_http.middleware.options.response_handler_option import ResponseHandlerOption
from kiota_http.middleware.options.retry_handler_option import RetryHandlerOption
from kiota_serialization_json.json_parse_node_factory import JsonParseNodeFactory
from kiota_serialization_json.json_serialization_writer import JsonSerializationWriter
from kiota_serialization_json.json_serialization_writer_factory import (
    JsonSerializationWriterFactory,
)
from msgraph_core import (
    APIVersion,
    BaseGraphRequestAdapter,
    GraphClientFactory,
    NationalClouds,
)
from pydantic import Field

from tracecat_registry import SecretNotFoundError, secrets

GRAPH_API_VERSION = "v1.0"
GRAPH_USE_THE_API_DOC_URL = "https://learn.microsoft.com/en-us/graph/use-the-api"
GRAPH_PAGING_DOC_URL = "https://learn.microsoft.com/en-us/graph/paging"

NATIONAL_CLOUDS: dict[str, NationalClouds] = {
    "graph.microsoft.com": NationalClouds.Global,
    "graph.microsoft.us": NationalClouds.US_GOV,
    "dod-graph.microsoft.us": NationalClouds.US_DoD,
    "microsoftgraph.chinacloudapi.cn": NationalClouds.China,
}
"""Graph hosts Tracecat will talk to, keyed by host name.

Deliberately narrower than `msgraph_core.NationalClouds`, which still carries the
retired `graph.microsoft.de` endpoint.
"""

ALLOWED_BASE_URLS: frozenset[str] = frozenset(
    f"https://{host}/{GRAPH_API_VERSION}" for host in NATIONAL_CLOUDS
)
DEFAULT_BASE_URL = f"https://graph.microsoft.com/{GRAPH_API_VERSION}"

SUPPORTED_METHODS: dict[str, Method] = {
    "GET": Method.GET,
    "POST": Method.POST,
    "PUT": Method.PUT,
    "PATCH": Method.PATCH,
    "DELETE": Method.DELETE,
    "HEAD": Method.HEAD,
    "OPTIONS": Method.OPTIONS,
}
NO_CONTENT_STATUS_CODES = frozenset({204, 304})
SDK_OWNED_HEADERS = frozenset({"authorization", "host"})
RETRYABLE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "PUT"})
JSON_CONTENT_TYPE = "application/json"

_SCHEME_PREFIX = re.compile(r"^[A-Za-z][A-Za-z0-9+.\-]*:")
_TRAVERSAL_SEGMENTS = frozenset({".", ".."})

type MicrosoftGraphAuthMode = Literal["application", "delegated", "auto"]


@dataclass(frozen=True, slots=True)
class GraphProduct:
    """One product-scoped view of Microsoft Graph and the tokens it may use.

    `service_token_names` and `user_token_names` are ordered by precedence: the
    product's own OAuth token first, then the generic Microsoft Graph token as a
    fallback. `auto` tries every service token before any user token.
    """

    label: str
    namespace: str
    service_token_names: tuple[str, ...]
    user_token_names: tuple[str, ...]

    def token_names(self, auth_mode: MicrosoftGraphAuthMode) -> tuple[str, ...]:
        """Return the token names to try, in precedence order."""
        match auth_mode:
            case "application":
                return self.service_token_names
            case "delegated":
                return self.user_token_names
            case "auto":
                return self.service_token_names + self.user_token_names
            case _:
                raise ValueError(
                    f"Unsupported Microsoft Graph auth mode {auth_mode!r}. "
                    "Expected `application`, `delegated` or `auto`."
                )


PathParam = Annotated[
    str,
    Field(
        ...,
        description=(
            "Resource path relative to the Microsoft Graph v1.0 root, e.g. "
            "`/security/alerts_v2`. Absolute URLs, dot-segments and encoded "
            "separators are rejected."
        ),
    ),
]
MethodParam = Annotated[
    str,
    Field(
        ...,
        description=(
            "HTTP method. One of GET, POST, PUT, PATCH, DELETE, HEAD, OPTIONS."
        ),
    ),
]
ParamsParam = Annotated[
    dict[str, Any] | None,
    Field(
        ...,
        description=(
            'Query parameters, e.g. `{"$top": 50}`. Only `None` values are '
            'dropped: `false`, `0` and `""` are sent. Booleans are serialized '
            "as OData `true`/`false` and lists as repeated keys."
        ),
    ),
]
PayloadParam = Annotated[
    dict[str, Any] | None,
    Field(
        ...,
        description=(
            "JSON request body. Explicit `null` values are sent as JSON `null` "
            "unless `omit_none_payload_fields` is enabled."
        ),
    ),
]
HeadersParam = Annotated[
    dict[str, str | None] | None,
    Field(
        ...,
        description=(
            "Additional request headers. `null` values are dropped. `Authorization` "
            "and `Host` are owned by the SDK and are rejected (case-insensitive)."
        ),
    ),
]
BaseUrlParam = Annotated[
    str | None,
    Field(
        ...,
        description=(
            "Microsoft Graph v1.0 API root. Defaults to "
            "`https://graph.microsoft.com/v1.0`. Also accepts "
            "`https://graph.microsoft.us/v1.0`, "
            "`https://dod-graph.microsoft.us/v1.0` and "
            "`https://microsoftgraph.chinacloudapi.cn/v1.0`."
        ),
    ),
]
OmitNonePayloadFieldsParam = Annotated[
    bool,
    Field(
        ...,
        description=(
            "Drop top-level payload fields whose value is `null` instead of "
            "sending JSON `null`. Nested `null` values are always preserved."
        ),
    ),
]
LimitParam = Annotated[
    int | None,
    Field(
        ...,
        ge=1,
        description=(
            "Maximum number of items to return across all pages. Paging stops as "
            "soon as the bound is reached. Set to `null` to follow every "
            "`@odata.nextLink`."
        ),
    ),
]
ContinuationUrlParam = Annotated[
    str,
    Field(
        ...,
        description=(
            "Complete `@odata.nextLink` or `@odata.deltaLink` returned by Microsoft "
            "Graph. The URL must remain on the selected national-cloud v1.0 root."
        ),
    ),
]


def _token_phrase(product: GraphProduct, auth_mode: MicrosoftGraphAuthMode) -> str:
    names = product.token_names(auth_mode)
    quoted = [f"`{name}`" for name in names]
    if len(quoted) == 1:
        return quoted[0]
    return f"{', '.join(quoted[:-1])} or {quoted[-1]}"


def resolve_access_token(
    product: GraphProduct, auth_mode: MicrosoftGraphAuthMode
) -> str:
    """Resolve the first configured token for `auth_mode`, never logging its value."""
    names = product.token_names(auth_mode)
    for token_name in names:
        if token := secrets.get_or_default(token_name):
            return token
    raise SecretNotFoundError(
        f"{product.label} `{auth_mode}` auth requires "
        f"{_token_phrase(product, auth_mode)}. Connect the matching Microsoft OAuth "
        "integration or change `auth_mode`."
    )


def normalize_base_url(base_url: str | None) -> str:
    """Normalize `base_url` to one of the approved Graph v1.0 API roots."""
    candidate = (base_url or DEFAULT_BASE_URL).strip()
    normalized = candidate.rstrip("/")
    parts = urlsplit(normalized)
    host = (parts.hostname or "").lower()
    if (
        parts.scheme != "https"
        or parts.username
        or parts.password
        or parts.query
        or parts.fragment
        or host not in NATIONAL_CLOUDS
        or parts.path != f"/{GRAPH_API_VERSION}"
    ):
        raise ValueError(
            f"{candidate!r} is not an approved Microsoft Graph v1.0 API root. "
            f"Expected one of {sorted(ALLOWED_BASE_URLS)}."
        )
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(
            f"{candidate!r} is not an approved Microsoft Graph v1.0 API root."
        ) from exc
    if port is not None:
        raise ValueError(
            f"{candidate!r} is not an approved Microsoft Graph v1.0 API root. "
            "Explicit ports are not allowed."
        )
    return f"https://{host}/{GRAPH_API_VERSION}"


def _path_segments(path: str) -> list[str]:
    """Split a caller-supplied path into segments, rejecting root escapes."""
    if not isinstance(path, str) or not path.strip():
        raise ValueError(
            "Microsoft Graph `path` is required, e.g. `/security/alerts_v2`."
        )
    candidate = path.strip()
    reason: str | None = None
    if _SCHEME_PREFIX.match(candidate) or candidate.startswith("//"):
        reason = "it must be relative to the Microsoft Graph v1.0 root"
    elif "\\" in candidate:
        reason = "backslashes are not path separators in Microsoft Graph"
    elif "?" in candidate or "#" in candidate:
        reason = "use `params` for query parameters; fragments are not supported"
    elif any(character.isspace() or ord(character) < 0x20 for character in candidate):
        reason = "it contains whitespace or control characters"
    if reason:
        raise ValueError(f"Invalid Microsoft Graph path {path!r}: {reason}.")

    relative = candidate[1:] if candidate.startswith("/") else candidate
    segments = relative.split("/")
    for segment in segments:
        if not segment:
            raise ValueError(
                f"Invalid Microsoft Graph path {path!r}: empty path segments are not allowed."
            )
        if _segment_is_unsafe(segment):
            raise ValueError(
                f"Invalid Microsoft Graph path {path!r}: path traversal and encoded "
                "separators are not allowed."
            )
    return segments


def _segment_is_unsafe(segment: str) -> bool:
    """Reject traversal or separators revealed by any percent-decoding layer."""
    decoded = segment
    while True:
        if decoded in _TRAVERSAL_SEGMENTS or "/" in decoded or "\\" in decoded:
            return True
        next_decoded = unquote(decoded)
        if next_decoded == decoded:
            return False
        decoded = next_decoded


def _query_value(key: str, value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return value
    if isinstance(value, int | float):
        return str(value)
    raise TypeError(
        f"Query parameter {key!r} must be a string, number, boolean or a list of those, "
        f"got {type(value).__name__}."
    )


def _encode_params(params: Mapping[str, Any] | None) -> str:
    """Encode query parameters, dropping only `None` values."""
    if not params:
        return ""
    pairs: list[tuple[str, str]] = []
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, list | tuple):
            pairs.extend(
                (key, _query_value(key, item)) for item in value if item is not None
            )
        else:
            pairs.append((key, _query_value(key, value)))
    # `$` stays literal so OData keys reach Graph as `$top` rather than `%24top`.
    return urlencode(pairs, safe="$", quote_via=quote)


def build_request_url(
    base_url: str, path: str, params: Mapping[str, Any] | None
) -> str:
    """Join an approved root, a validated relative path and encoded query parameters."""
    url = f"{base_url}/{'/'.join(_path_segments(path))}"
    if query := _encode_params(params):
        return f"{url}?{query}"
    return url


def sanitize_headers(headers: Mapping[str, Any] | None) -> dict[str, str]:
    """Drop unset headers and refuse the ones the SDK owns."""
    if not headers:
        return {}
    sanitized: dict[str, str] = {}
    for name, value in headers.items():
        if name.strip().lower() in SDK_OWNED_HEADERS:
            raise ValueError(
                f"Header {name!r} is set by the Microsoft Graph SDK and cannot be "
                "supplied by the caller."
            )
        if value is None:
            continue
        if not isinstance(value, str):
            raise TypeError(
                f"Header {name!r} must be a string, got {type(value).__name__}."
            )
        sanitized[name] = value
    return sanitized


def serialize_payload(
    payload: Mapping[str, Any], omit_none_payload_fields: bool
) -> bytes:
    """Serialize a JSON body with Kiota, preserving explicit nulls by default."""
    body = (
        {key: value for key, value in payload.items() if value is not None}
        if omit_none_payload_fields
        else dict(payload)
    )
    writer = JsonSerializationWriter()
    writer.write_additional_data_value(body)
    return writer.get_serialized_content()


def validate_continuation_url(continuation_url: Any, base_url: str) -> str:
    """Accept a Graph continuation URL only on the selected v1.0 root."""
    if not isinstance(continuation_url, str) or not continuation_url.strip():
        raise ValueError("Microsoft Graph returned an unusable continuation URL.")
    candidate = continuation_url.strip()
    parts = urlsplit(candidate)
    try:
        port = parts.port
    except ValueError as exc:
        raise ValueError(
            "Refusing to follow Microsoft Graph continuation URL: it must stay on "
            f"{base_url}."
        ) from exc
    host = (parts.hostname or "").lower()
    origin = f"https://{host}"
    resource_segments = parts.path.split("/")[2:]
    if (
        parts.scheme != "https"
        or parts.username
        or parts.password
        or parts.fragment
        or port is not None
        or f"{origin}/{GRAPH_API_VERSION}" != base_url
        or not parts.path.startswith(f"/{GRAPH_API_VERSION}/")
        or any(character.isspace() or ord(character) < 0x20 for character in candidate)
        or not resource_segments
        or any(
            not segment or _segment_is_unsafe(segment) for segment in resource_segments
        )
    ):
        raise ValueError(
            "Refusing to follow Microsoft Graph continuation URL: it must stay on "
            f"{base_url}."
        )
    return candidate


class _StaticAccessTokenProvider(AccessTokenProvider):
    """Hands the resolved Graph token to Kiota for one approved host only."""

    def __init__(self, token: str, allowed_host: str) -> None:
        self._token = token
        self._allowed_hosts_validator = AllowedHostsValidator([allowed_host])

    async def get_authorization_token(
        self,
        uri: str,
        additional_authentication_context: dict[str, Any] | None = None,
    ) -> str:
        if not self._allowed_hosts_validator.is_url_host_valid(uri):
            raise ValueError(
                "Refusing to send Microsoft Graph credentials to a host outside "
                f"{self._allowed_hosts_validator.get_allowed_hosts()}."
            )
        return self._token

    def get_allowed_hosts_validator(self) -> AllowedHostsValidator:
        return self._allowed_hosts_validator


def build_http_client(base_url: str) -> httpx.AsyncClient:
    """Build the Graph middleware pipeline pinned to the selected national cloud."""
    host = urlsplit(base_url).hostname or ""
    return GraphClientFactory.create_with_default_middleware(
        api_version=APIVersion.v1, host=NATIONAL_CLOUDS[host]
    )


def _build_request_adapter(
    base_url: str, token: str, http_client: httpx.AsyncClient
) -> BaseGraphRequestAdapter:
    host = urlsplit(base_url).hostname or ""
    adapter = BaseGraphRequestAdapter(
        authentication_provider=BaseBearerTokenAuthenticationProvider(
            _StaticAccessTokenProvider(token=token, allowed_host=host)
        ),
        parse_node_factory=JsonParseNodeFactory(),
        serialization_writer_factory=JsonSerializationWriterFactory(),
        http_client=http_client,
    )
    adapter.base_url = base_url
    return adapter


def _build_request_information(
    *,
    method: str,
    url: str,
    headers: Mapping[str, str],
    content: bytes | None,
) -> RequestInformation:
    normalized_method = method.strip().upper() if isinstance(method, str) else ""
    if normalized_method not in SUPPORTED_METHODS:
        raise ValueError(
            f"Unsupported Microsoft Graph HTTP method {method!r}. "
            f"Expected one of {sorted(SUPPORTED_METHODS)}."
        )
    request_info = RequestInformation()
    request_info.url = url
    request_info.http_method = SUPPORTED_METHODS[normalized_method]
    for name, value in headers.items():
        request_info.headers.add(name, value)
    if content is not None:
        request_info.set_stream_content(content, JSON_CONTENT_TYPE)
    request_info.add_request_options(
        [
            ResponseHandlerOption(NativeResponseHandler()),
            RetryHandlerOption(should_retry=normalized_method in RETRYABLE_METHODS),
        ]
    )
    return request_info


def _read_response(response: httpx.Response) -> Any:
    if response.status_code >= 400:
        raise APIError(
            message=(
                f"Microsoft Graph returned {response.status_code}: {response.text}"
            ),
            response_status_code=response.status_code,
            response_headers=dict(response.headers),
        )
    if response.status_code in NO_CONTENT_STATUS_CODES or not response.content:
        return None
    # Parse the native response directly. Kiota's generic parse-node accessor
    # coerces ISO-8601 strings, UUIDs and durations, while workflow authors need
    # the JSON document exactly as Graph represented it.
    return response.json()


async def _send(
    *,
    adapter: BaseGraphRequestAdapter,
    method: str,
    url: str,
    headers: Mapping[str, str],
    content: bytes | None,
) -> Any:
    request_info = _build_request_information(
        method=method, url=url, headers=headers, content=content
    )
    response = cast(
        httpx.Response,
        await adapter.send_primitive_async(request_info, "bytes", None),
    )
    return _read_response(response)


def _page_items(page: Any, product: GraphProduct) -> list[Any]:
    if page is None:
        return []
    if not isinstance(page, dict):
        raise TypeError(
            "Microsoft Graph paginated responses must be JSON objects with a `value` "
            "collection."
        )
    value = page.get("value")
    if value is None:
        raise ValueError(
            "Microsoft Graph response has no `value` collection. Use "
            f"`{product.namespace}.call_method` for single-resource endpoints."
        )
    if not isinstance(value, list):
        raise TypeError("Microsoft Graph `value` must be a JSON array.")
    return value


async def call_method(
    *,
    product: GraphProduct,
    path: str,
    method: str,
    params: dict[str, Any] | None,
    payload: dict[str, Any] | None,
    headers: dict[str, str | None] | None,
    base_url: str | None,
    auth_mode: MicrosoftGraphAuthMode,
    omit_none_payload_fields: bool,
) -> Any:
    """Send one Microsoft Graph v1.0 request and return its untouched JSON body."""
    resolved_base_url = normalize_base_url(base_url)
    request_headers = sanitize_headers(headers)
    url = build_request_url(resolved_base_url, path, params)
    content = (
        serialize_payload(payload, omit_none_payload_fields)
        if payload is not None
        else None
    )
    token = resolve_access_token(product, auth_mode)

    http_client = build_http_client(resolved_base_url)
    try:
        adapter = _build_request_adapter(resolved_base_url, token, http_client)
        return await _send(
            adapter=adapter,
            method=method,
            url=url,
            headers=request_headers,
            content=content,
        )
    finally:
        await http_client.aclose()


async def call_continuation_method(
    *,
    product: GraphProduct,
    continuation_url: str,
    headers: dict[str, str | None] | None,
    base_url: str | None,
    auth_mode: MicrosoftGraphAuthMode,
) -> Any:
    """Follow one Graph next or delta link and return the untouched JSON page."""
    resolved_base_url = normalize_base_url(base_url)
    request_headers = sanitize_headers(headers)
    url = validate_continuation_url(continuation_url, resolved_base_url)
    token = resolve_access_token(product, auth_mode)

    http_client = build_http_client(resolved_base_url)
    try:
        adapter = _build_request_adapter(resolved_base_url, token, http_client)
        return await _send(
            adapter=adapter,
            method="GET",
            url=url,
            headers=request_headers,
            content=None,
        )
    finally:
        await http_client.aclose()


async def call_paginated_method(
    *,
    product: GraphProduct,
    path: str,
    params: dict[str, Any] | None,
    headers: dict[str, str | None] | None,
    base_url: str | None,
    auth_mode: MicrosoftGraphAuthMode,
    limit: int | None,
) -> list[Any]:
    """Fetch every page of a Microsoft Graph collection, bounded by `limit`.

    Each `@odata.nextLink` is re-validated against the selected API root before it
    is followed. `msgraph_core.PageIterator` is intentionally not used: it accepts
    any `http`-prefixed next link and deserializes items into Kiota models, so it
    can neither keep the bearer token on the selected origin nor return Graph's
    JSON untouched.
    """
    if limit is not None and limit < 1:
        raise ValueError(
            "`limit` must be a positive integer, or `null` to follow every page."
        )
    resolved_base_url = normalize_base_url(base_url)
    request_headers = sanitize_headers(headers)
    url: str | None = build_request_url(resolved_base_url, path, params)
    token = resolve_access_token(product, auth_mode)

    items: list[Any] = []
    http_client = build_http_client(resolved_base_url)
    try:
        adapter = _build_request_adapter(resolved_base_url, token, http_client)
        while url:
            page = await _send(
                adapter=adapter,
                method="GET",
                url=url,
                headers=request_headers,
                content=None,
            )
            items.extend(_page_items(page, product))
            if limit is not None and len(items) >= limit:
                return items[:limit]
            next_link = page.get("@odata.nextLink") if isinstance(page, dict) else None
            url = (
                validate_continuation_url(next_link, resolved_base_url)
                if next_link
                else None
            )
    finally:
        await http_client.aclose()
    return items
