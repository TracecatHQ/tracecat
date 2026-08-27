"""Microsoft Outlook Mail interface over the shared Graph v1.0 transport.

Identical request semantics to `tools.microsoft_graph_sdk`, but the token is
resolved from the `microsoft_outlook` OAuth provider first and only then from the
generic `microsoft_graph` provider. Microsoft Graph Security tokens are never
considered.
"""

from typing import Annotated, Any

from pydantic import Field

from tracecat_registry import RegistryOAuthSecret, RegistrySecretType, registry
from tracecat_registry.integrations._microsoft_graph_products import (
    microsoft_graph_service_oauth_secret,
    microsoft_graph_user_oauth_secret,
)
from tracecat_registry.integrations._microsoft_graph_transport import (
    GRAPH_PAGING_DOC_URL,
    GRAPH_USE_THE_API_DOC_URL,
    BaseUrlParam,
    ContinuationUrlParam,
    GraphProduct,
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

microsoft_outlook_service_oauth_secret = RegistryOAuthSecret(
    provider_id="microsoft_outlook",
    grant_type="client_credentials",
    optional=True,
)
"""Microsoft Outlook Mail application (service account) OAuth credentials.

- name: `microsoft_outlook_oauth`
- provider_id: `microsoft_outlook`
- token_name: `MICROSOFT_OUTLOOK_SERVICE_TOKEN`
"""

microsoft_outlook_user_oauth_secret = RegistryOAuthSecret(
    provider_id="microsoft_outlook",
    grant_type="authorization_code",
    optional=True,
)
"""Microsoft Outlook Mail delegated (user) OAuth credentials.

- name: `microsoft_outlook_oauth`
- provider_id: `microsoft_outlook`
- token_name: `MICROSOFT_OUTLOOK_USER_TOKEN`
"""

MICROSOFT_OUTLOOK_SDK_SECRETS: list[RegistrySecretType] = [
    microsoft_outlook_service_oauth_secret,
    microsoft_outlook_user_oauth_secret,
    microsoft_graph_service_oauth_secret,
    microsoft_graph_user_oauth_secret,
]

MICROSOFT_OUTLOOK = GraphProduct(
    label="Microsoft Outlook Mail",
    namespace="tools.microsoft_outlook_sdk",
    service_token_names=(
        microsoft_outlook_service_oauth_secret.token_name,
        microsoft_graph_service_oauth_secret.token_name,
    ),
    user_token_names=(
        microsoft_outlook_user_oauth_secret.token_name,
        microsoft_graph_user_oauth_secret.token_name,
    ),
)

AuthModeParam = Annotated[
    MicrosoftGraphAuthMode,
    Field(..., description=auth_mode_description(MICROSOFT_OUTLOOK)),
]


@registry.register(
    default_title="Call method",
    description=(
        "Call a Microsoft Graph v1.0 endpoint with Microsoft Outlook Mail "
        "credentials and return its JSON response body unchanged, or `null` for "
        "no-content responses."
    ),
    display_group="Microsoft Outlook Mail SDK",
    doc_url=GRAPH_USE_THE_API_DOC_URL,
    namespace="tools.microsoft_outlook_sdk",
    secrets=MICROSOFT_OUTLOOK_SDK_SECRETS,
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
        product=MICROSOFT_OUTLOOK,
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
        "`@odata.deltaLink` with Outlook credentials after validating that it "
        "stays on the selected v1.0 national-cloud root."
    ),
    display_group="Microsoft Outlook Mail SDK",
    doc_url=GRAPH_PAGING_DOC_URL,
    namespace="tools.microsoft_outlook_sdk",
    secrets=MICROSOFT_OUTLOOK_SDK_SECRETS,
)
async def call_continuation_method(
    continuation_url: ContinuationUrlParam,
    headers: HeadersParam = None,
    base_url: BaseUrlParam = None,
    auth_mode: AuthModeParam = "application",
) -> Any:
    """Follow one validated Graph next or delta link."""
    return await _call_continuation_method(
        product=MICROSOFT_OUTLOOK,
        continuation_url=continuation_url,
        headers=headers,
        base_url=base_url,
        auth_mode=auth_mode,
    )


@registry.register(
    default_title="Call paginated method",
    description=(
        "Call a Microsoft Graph v1.0 collection endpoint with Microsoft Outlook "
        "Mail credentials, follow `@odata.nextLink` and return one flattened list "
        "of items in Graph's order. Do not use this for `/delta` endpoints: "
        "following the continuation links discards the `@odata.deltaLink`."
    ),
    display_group="Microsoft Outlook Mail SDK",
    doc_url=GRAPH_PAGING_DOC_URL,
    namespace="tools.microsoft_outlook_sdk",
    secrets=MICROSOFT_OUTLOOK_SDK_SECRETS,
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
        product=MICROSOFT_OUTLOOK,
        path=path,
        params=params,
        headers=headers,
        base_url=base_url,
        auth_mode=auth_mode,
        limit=limit,
    )
