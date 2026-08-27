"""Generic interface for the Microsoft Graph v1.0 API over `msgraph-core`.

Credentials come from the generic `microsoft_graph` OAuth provider only. Product
namespaces such as `tools.microsoft_graph_security_sdk` and
`tools.microsoft_outlook_sdk` layer their own OAuth provider on top of this one;
this module never reads their tokens.

The request pipeline lives in `_microsoft_graph_transport`.
"""

from typing import Annotated, Any

from pydantic import Field

from tracecat_registry import registry
from tracecat_registry.integrations._microsoft_graph_products import (
    MICROSOFT_GRAPH,
    MICROSOFT_GRAPH_SDK_SECRETS,
)
from tracecat_registry.integrations._microsoft_graph_transport import (
    GRAPH_PAGING_DOC_URL,
    GRAPH_USE_THE_API_DOC_URL,
    BaseUrlParam,
    ContinuationUrlParam,
    HeadersParam,
    LimitParam,
    MethodParam,
    MicrosoftGraphAuthMode,
    OmitNonePayloadFieldsParam,
    ParamsParam,
    PathParam,
    PayloadParam,
    auth_mode_description,
    call_continuation_method as _call_continuation_method,
    call_method as _call_method,
    call_paginated_method as _call_paginated_method,
)

AuthModeParam = Annotated[
    MicrosoftGraphAuthMode,
    Field(..., description=auth_mode_description(MICROSOFT_GRAPH)),
]


@registry.register(
    default_title="Call method",
    description=(
        "Call a Microsoft Graph v1.0 endpoint and return its JSON response body "
        "unchanged, or `null` for no-content responses."
    ),
    display_group="Microsoft Graph SDK",
    doc_url=GRAPH_USE_THE_API_DOC_URL,
    namespace="tools.microsoft_graph_sdk",
    secrets=MICROSOFT_GRAPH_SDK_SECRETS,
)
async def call_method(
    path: PathParam,
    method: MethodParam,
    params: ParamsParam = None,
    payload: PayloadParam = None,
    headers: HeadersParam = None,
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "application",
    omit_none_payload_fields: OmitNonePayloadFieldsParam = False,
) -> Any:
    """Send one Microsoft Graph v1.0 request and return its untouched JSON body."""
    return await _call_method(
        product=MICROSOFT_GRAPH,
        path=path,
        method=method,
        params=params,
        payload=payload,
        headers=headers,
        base_url=base_url,
        auth_mode=auth_mode,
        omit_none_payload_fields=omit_none_payload_fields,
    )


@registry.register(
    default_title="Call continuation method",
    description=(
        "Follow one complete Microsoft Graph `@odata.nextLink` or "
        "`@odata.deltaLink` after validating that it stays on the selected v1.0 "
        "national-cloud root, and return the JSON page unchanged."
    ),
    display_group="Microsoft Graph SDK",
    doc_url=GRAPH_PAGING_DOC_URL,
    namespace="tools.microsoft_graph_sdk",
    secrets=MICROSOFT_GRAPH_SDK_SECRETS,
)
async def call_continuation_method(
    continuation_url: ContinuationUrlParam,
    headers: HeadersParam = None,
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "application",
) -> Any:
    """Follow one validated Graph next or delta link."""
    return await _call_continuation_method(
        product=MICROSOFT_GRAPH,
        continuation_url=continuation_url,
        headers=headers,
        base_url=base_url,
        auth_mode=auth_mode,
    )


@registry.register(
    default_title="Call paginated method",
    description=(
        "Call a Microsoft Graph v1.0 collection endpoint, follow `@odata.nextLink` "
        "and return one flattened list of items in Graph's order."
    ),
    display_group="Microsoft Graph SDK",
    doc_url=GRAPH_PAGING_DOC_URL,
    namespace="tools.microsoft_graph_sdk",
    secrets=MICROSOFT_GRAPH_SDK_SECRETS,
)
async def call_paginated_method(
    path: PathParam,
    params: ParamsParam = None,
    headers: HeadersParam = None,
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "application",
    limit: LimitParam = 1000,
) -> list[Any]:
    """Fetch every page of a Microsoft Graph collection, bounded by `limit`."""
    return await _call_paginated_method(
        product=MICROSOFT_GRAPH,
        path=path,
        params=params,
        headers=headers,
        base_url=base_url,
        auth_mode=auth_mode,
        limit=limit,
    )
