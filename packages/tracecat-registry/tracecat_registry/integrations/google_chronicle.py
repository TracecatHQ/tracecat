"""Generic Google Chronicle (Google Security Operations) REST client."""

from typing import Annotated, Any

import httpx
from pydantic import Field

from tracecat_registry import RegistryOAuthSecret, registry, secrets


google_chronicle_user_oauth_secret = RegistryOAuthSecret(
    provider_id="google_chronicle",
    grant_type="authorization_code",
    optional=True,
)
"""Google Chronicle user OAuth credentials.

- name: `google_chronicle_oauth`
- provider_id: `google_chronicle`
- token_name: `GOOGLE_CHRONICLE_USER_TOKEN`
"""

google_chronicle_service_oauth_secret = RegistryOAuthSecret(
    provider_id="google_chronicle",
    grant_type="client_credentials",
    optional=True,
)
"""Google Chronicle service account OAuth credentials.

- name: `google_chronicle_oauth`
- provider_id: `google_chronicle`
- token_name: `GOOGLE_CHRONICLE_SERVICE_TOKEN`
"""


def get_access_token() -> str:
    """Return the configured Chronicle user or service-account token."""
    if token := secrets.get_or_default(google_chronicle_user_oauth_secret.token_name):
        return token
    return secrets.get(google_chronicle_service_oauth_secret.token_name)


@registry.register(
    default_title="Call API",
    description="Call a Google Chronicle REST API endpoint.",
    display_group="Google Chronicle",
    doc_url="https://docs.cloud.google.com/chronicle/docs/reference/rest",
    namespace="tools.google_chronicle",
    secrets=[
        google_chronicle_user_oauth_secret,
        google_chronicle_service_oauth_secret,
    ],
)
async def call_api(
    url: Annotated[
        str,
        Field(
            ...,
            description=(
                "Full Google Chronicle REST API URL, including the API version "
                "and resource path."
            ),
        ),
    ],
    method: Annotated[
        str,
        Field(..., description="HTTP method for the Chronicle REST API request."),
    ],
    params: Annotated[
        dict[str, Any] | None,
        Field(..., description="Query parameters for the Chronicle API method."),
    ] = None,
    payload: Annotated[
        dict[str, Any] | None,
        Field(..., description="JSON request body for the Chronicle API method."),
    ] = None,
) -> Any:
    """Call a Chronicle REST API endpoint and return its response body."""
    token = get_access_token()
    request_params = (
        {key: value for key, value in params.items() if value is not None}
        if params
        else None
    )
    async with httpx.AsyncClient() as client:
        response = await client.request(
            method=method,
            url=url,
            headers={"Authorization": f"Bearer {token}"},
            params=request_params,
            json=payload,
        )
    response.raise_for_status()
    return response.json() if response.content else None
