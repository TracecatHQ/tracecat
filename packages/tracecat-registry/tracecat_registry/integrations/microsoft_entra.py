from typing import Annotated, Any
from typing_extensions import Doc

import httpx

from tracecat_registry import registry, secrets
from tracecat_registry._internal.models import RegistryOAuthSecret

microsoft_entra_oauth_secret = RegistryOAuthSecret(
    provider_id="microsoft_entra",
    grant_type="authorization_code",
)
"""Microsoft Entra OAuth2.0 credentials (Authorization Code grant).

- name: `microsoft_entra`
- provider_id: `microsoft_entra`
- token_name: `MICROSOFT_ENTRA_USER_TOKEN`
"""


@registry.register(
    default_title="Get user ID by email",
    description="Get a user's ID by searching for their email address in mail or userPrincipalName.",
    display_group="Microsoft Entra ID",
    doc_url="https://learn.microsoft.com/en-us/graph/api/user-list?view=graph-rest-1.0",
    namespace="tools.microsoft_entra",
    secrets=[microsoft_entra_oauth_secret],
)
async def get_user_id_by_email(
    email: Annotated[str, Doc("The email address to search for.")],
) -> dict[str, Any]:
    """Get a user's ID by email address.

    Note: This API requires User.ReadBasic.All, User.Read.All, or Directory.Read.All permissions.
    """
    token = secrets.get(microsoft_entra_oauth_secret.token_name)

    headers = {"Authorization": f"Bearer {token}"}

    url = "https://graph.microsoft.com/v1.0/users"

    filter_query = f"mail eq '{email.replace("'", "''")}' or userPrincipalName eq '{email.replace("'", "''")}'"

    params = {"$filter": filter_query, "$select": "id", "$top": "1"}

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
