"""Google API authentication via Google Auth Python library.

Docs: https://googleapis.dev/python/google-auth/latest/reference/google.oauth2.service_account.html
"""

from collections.abc import Mapping
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
    default_title="Get Google API service account token",
    description="Get a service account token for Google API calls.",
    display_group="Google API",
    namespace="integrations.google_api",
    secrets=[google_api_secret],
)
def get_auth_token(
    scopes: Annotated[list[str], Field(..., description="Google API scopes.")],
    subject: Annotated[str, Field(..., description="Google API subject.")] = None,
) -> str:
    """Retrieve an auth token for Google API calls for a service account."""
    creds_json_str = secrets.get("GOOGLE_API_CREDENTIALS")
    creds = orjson.loads(creds_json_str)
    if not isinstance(creds, Mapping):
        raise ValueError(
            "SECRETS.google_api.GOOGLE_API_CREDENTIALS is not a valid JSON string."
        )
    credentials = service_account.Credentials.from_service_account_info(
        creds,
        scopes=scopes,
    )
    if subject:
        credentials = credentials.with_subject(subject)
    return credentials.token
