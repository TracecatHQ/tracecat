"""Google API authentication via Google Auth Python library."""

from typing import Annotated

import orjson
from google.oauth2 import service_account
from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets

google_api_secret = RegistrySecret(
    name="google_api",
    keys=["GOOGLE_API_CREDENTIALS"],
)
"""Google API Secret.

- name: `google_api`
- keys:
    - `GOOGLE_API_CREDENTIALS` (JSON string)

Note: `GOOGLE_API_CREDENTIALS` should be a JSON string of the service account credentials.
"""


@registry.register(
    default_title="Get auth token",
    description="Given service account credentials as a JSON string, retrieves a JWT token for Google API calls.",
    display_group="Google API",
    doc_url="https://googleapis.dev/python/google-auth/latest/reference/google.oauth2.service_account.html#google.oauth2.service_account.Credentials.from_service_account_info",
    namespace="integrations.google_api",
    secrets=[google_api_secret],
)
def get_auth_token(
    scopes: Annotated[list[str], Field(..., description="Google API scopes.")],
    subject: Annotated[str, Field(..., description="Google API subject.")] = None,
) -> str:
    creds_json = secrets.get("GOOGLE_API_CREDENTIALS")
    try:
        creds = orjson.loads(creds_json)
    except orjson.JSONDecodeError as e:
        raise ValueError("`GOOGLE_API_CREDENTIALS` is not a valid JSON string.") from e
    credentials = service_account.Credentials.from_service_account_info(
        creds,
        scopes=scopes,
    )
    if subject:
        credentials = credentials.with_subject(subject)
    return credentials.token
