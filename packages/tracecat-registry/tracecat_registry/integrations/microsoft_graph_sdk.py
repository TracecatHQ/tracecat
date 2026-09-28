"""Generic interface for the Microsoft Graph v1.0 API over `msgraph-core`.

One public SDK namespace serves generic Graph, Graph Security and Outlook Mail.
The caller selects a product-scoped OAuth provider; product tokens fall back to
the matching generic Graph grant and never cross into another product.
"""

from typing import Annotated, Any

from pydantic import Field

from tracecat_registry import registry
from tracecat_registry.integrations._microsoft_graph_products import (
    GRAPH_PRODUCTS,
    MICROSOFT_GRAPH_SDK_SECRETS,
    OAuthProviderParam,
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
    call_continuation_method as _call_continuation_method,
    call_method as _call_method,
    call_paginated_method as _call_paginated_method,
)

AuthModeParam = Annotated[
    MicrosoftGraphAuthMode,
    Field(
        ...,
        description=(
            "Credential type to use from the selected OAuth provider. "
            "`application` uses its service token, `delegated` uses its user token, "
            "and `auto` tries the service chain before the user chain. Product "
            "providers fall back to the matching generic Microsoft Graph token."
        ),
    ),
]


@registry.register(
    default_title="Call method",
    description=(
        "Call a Microsoft Graph v1.0 endpoint with the selected OAuth provider and "
        "return its JSON response body unchanged, or `null` for no-content responses."
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
    oauth_provider: OAuthProviderParam = "microsoft_graph",
) -> Any:
    """Send one Microsoft Graph v1.0 request and return its untouched JSON body."""
    return await _call_method(
        product=GRAPH_PRODUCTS[oauth_provider],
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
        "`@odata.deltaLink` with the selected OAuth provider after validating that "
        "it stays on the selected v1.0 national-cloud root, and return the JSON page "
        "unchanged."
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
    oauth_provider: OAuthProviderParam = "microsoft_graph",
) -> Any:
    """Follow one validated Graph next or delta link."""
    return await _call_continuation_method(
        product=GRAPH_PRODUCTS[oauth_provider],
        continuation_url=continuation_url,
        headers=headers,
        base_url=base_url,
        auth_mode=auth_mode,
    )


@registry.register(
    default_title="Call paginated method",
    description=(
        "Call a Microsoft Graph v1.0 collection endpoint with the selected OAuth "
        "provider, follow `@odata.nextLink` and return one flattened list of items "
        "in Graph's order."
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
    oauth_provider: OAuthProviderParam = "microsoft_graph",
) -> list[Any]:
    """Fetch every page of a Microsoft Graph collection, bounded by `limit`."""
    return await _call_paginated_method(
        product=GRAPH_PRODUCTS[oauth_provider],
        path=path,
        params=params,
        headers=headers,
        base_url=base_url,
        auth_mode=auth_mode,
        limit=limit,
    )
