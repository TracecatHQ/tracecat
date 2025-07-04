from typing import Annotated, Any
from typing_extensions import Doc

import httpx

from tracecat_registry import RegistrySecret, registry, secrets

microsoft_entra_oauth_secret = RegistrySecret.oauth("microsoft_entra")
"""Microsoft Entra OAuth2.0 credentials (Authorization Code grant).

- name: `microsoft_entra`
- provider_id: `microsoft_entra`
usage:
MICROSOFT_ENTRA_ACCESS_TOKEN
"""


@registry.register(
    default_title="Get user ID by email",
    description="Get a user's ID by searching for their email address in mail or userPrincipalName Docs.",
    display_group="Microsoft Entra ID",
    doc_url="https://learn.microsoft.com/en-us/graph/api/user-list?view=graph-rest-beta&tabs=http",
    namespace="tools.microsoft_entra",
    secrets=[microsoft_entra_oauth_secret],
)
async def get_user_id_by_email(
    email: Annotated[str, Doc("The email address to search for.")],
) -> dict[str, Any]:
    """Get a user's ID by email address.

    Note: This API requires User.ReadBasic.All, User.Read.All, or Directory.Read.All permissions.
    """
    token = secrets.get("MICROSOFT_ENTRA_ACCESS_TOKEN")

    headers = {"Authorization": f"Bearer {token}"}

    url = "https://graph.microsoft.com/beta/users"

    filter_query = f"mail eq '{email}'"

    params = {"$filter": filter_query, "$select": "id", "$top": "1"}

    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers, params=params)
        response.raise_for_status()
        return response.json()
