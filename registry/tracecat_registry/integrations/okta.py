"""Okta REST API integration for user and group management."""

from typing import Annotated, Any

import httpx
from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets

okta_secret = RegistrySecret(
    name="okta",
    keys=[
        "OKTA_API_TOKEN",
    ],
)
"""Okta API credentials.

- name: `okta`
- keys:
    - `OKTA_API_TOKEN`: Okta API token for authentication
"""


def _get_okta_headers() -> dict[str, str]:
    """Get standard headers for Okta API requests."""
    return {
        "Authorization": f"SSWS {secrets.get('OKTA_API_TOKEN')}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


@registry.register(
    default_title="Get user",
    description="Retrieve a specific user by ID, login, or email from your Okta organization.",
    display_group="Okta",
    doc_url="https://developer.okta.com/docs/reference/api/users/#get-user",
    namespace="tools.okta",
    secrets=[okta_secret],
)
async def get_user(
    base_url: Annotated[
        str,
        Field(
            ..., description="Okta domain base URL (e.g., 'https://dev-12345.okta.com')"
        ),
    ],
    user_id: Annotated[
        str,
        Field(..., description="User ID, login, or email of the user to retrieve"),
    ],
) -> dict[str, Any]:
    """Get a specific user from Okta."""
    headers = _get_okta_headers()

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/api/v1/users/{user_id}",
            headers=headers,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="List users",
    description="List all users in your Okta organization with optional filtering and search.",
    display_group="Okta",
    doc_url="https://developer.okta.com/docs/reference/api/users/#list-users",
    namespace="tools.okta",
    secrets=[okta_secret],
)
async def list_users(
    base_url: Annotated[
        str,
        Field(
            ..., description="Okta domain base URL (e.g., 'https://dev-12345.okta.com')"
        ),
    ],
    limit: Annotated[
        int,
        Field(200, description="Number of users to return (default: 200)"),
    ] = 200,
    filter: Annotated[
        str | None,
        Field(None, description="Filter expression for users"),
    ] = None,
    after: Annotated[
        int | None,
        Field(None, description="Result to start from"),
    ] = None,
) -> list[dict[str, Any]]:
    """List users in Okta organization."""
    headers = _get_okta_headers()

    params: dict[str, Any] = {"limit": limit}
    if filter:
        params["filter"] = filter
    if after:
        params["after"] = after

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/api/v1/users",
            headers=headers,
            params=params,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Search users",
    description="Search for users using a query string that matches login, email, firstName, or lastName.",
    display_group="Okta",
    doc_url="https://developer.okta.com/docs/reference/api/users/#list-users",
    namespace="tools.okta",
    secrets=[okta_secret],
)
async def search_users(
    base_url: Annotated[
        str,
        Field(
            ..., description="Okta domain base URL (e.g., 'https://dev-12345.okta.com')"
        ),
    ],
    query: Annotated[
        str,
        Field(..., description="Query string to search for users"),
    ],
    limit: Annotated[
        int,
        Field(10, description="Number of users to return (default: 10)"),
    ] = 10,
    after: Annotated[
        int | None,
        Field(None, description="Result to start from"),
    ] = None,
) -> list[dict[str, Any]]:
    """Search for users in Okta."""
    headers = _get_okta_headers()

    params = {"search": query, "limit": limit}
    if after:
        params["after"] = after

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/api/v1/users",
            headers=headers,
            params=params,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Create user",
    description="Create a new user in your Okta organization.",
    display_group="Okta",
    doc_url="https://developer.okta.com/docs/reference/api/users/#create-user",
    namespace="tools.okta",
    secrets=[okta_secret],
)
async def create_user(
    base_url: Annotated[
        str,
        Field(
            ..., description="Okta domain base URL (e.g., 'https://dev-12345.okta.com')"
        ),
    ],
    email: Annotated[
        str,
        Field(..., description="Email address of the new user"),
    ],
    first_name: Annotated[
        str,
        Field(..., description="First name of the new user"),
    ],
    last_name: Annotated[
        str,
        Field(..., description="Last name of the new user"),
    ],
    login: Annotated[
        str | None,
        Field(
            None, description="Login for the user (defaults to email if not provided)"
        ),
    ] = None,
    activate: Annotated[
        bool,
        Field(True, description="Whether to activate the user immediately"),
    ] = True,
    additional_attributes: Annotated[
        dict[str, Any] | None,
        Field(None, description="Additional user profile attributes"),
    ] = None,
) -> dict[str, Any]:
    """Create a new user in Okta."""
    headers = _get_okta_headers()

    profile = {
        "firstName": first_name,
        "lastName": last_name,
        "email": email,
        "login": login or email,
    }

    if additional_attributes:
        profile.update(additional_attributes)

    user_data = {"profile": profile}
    params = {"activate": str(activate).lower()}

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/api/v1/users",
            headers=headers,
            params=params,
            json=user_data,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Activate user",
    description="Activate a user account in Okta.",
    display_group="Okta",
    doc_url="https://developer.okta.com/docs/reference/api/users/#activate-user",
    namespace="tools.okta",
    secrets=[okta_secret],
)
async def activate_user(
    base_url: Annotated[
        str,
        Field(
            ..., description="Okta domain base URL (e.g., 'https://dev-12345.okta.com')"
        ),
    ],
    user_id: Annotated[
        str,
        Field(..., description="User ID, login, or email of the user to activate"),
    ],
    send_email: Annotated[
        bool,
        Field(True, description="Whether to send an activation email to the user"),
    ] = True,
) -> dict[str, Any]:
    """Activate a user in Okta."""
    headers = _get_okta_headers()

    params = {"sendEmail": str(send_email).lower()}

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/api/v1/users/{user_id}/lifecycle/activate",
            headers=headers,
            params=params,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Clear user sessions",
    description="Clear all active sessions for a user in Okta.",
    display_group="Okta",
    doc_url="https://developer.okta.com/docs/reference/api/users/#clear-user-sessions",
    namespace="tools.okta",
    secrets=[okta_secret],
)
async def clear_user_sessions(
    base_url: Annotated[
        str,
        Field(
            ..., description="Okta domain base URL (e.g., 'https://dev-12345.okta.com')"
        ),
    ],
    user_id: Annotated[
        str,
        Field(
            ...,
            description="User ID, login, or email of the user whose sessions to clear",
        ),
    ],
) -> dict[str, Any]:
    """Clear all active sessions for a user."""
    headers = _get_okta_headers()

    async with httpx.AsyncClient() as client:
        response = await client.delete(
            f"{base_url}/api/v1/users/{user_id}/sessions",
            headers=headers,
        )
        response.raise_for_status()
        return {"message": "User sessions cleared successfully"}


@registry.register(
    default_title="List groups in organization",
    description="List all groups in your Okta organization with optional filtering.",
    display_group="Okta",
    doc_url="https://developer.okta.com/docs/reference/api/groups/#list-groups",
    namespace="tools.okta",
    secrets=[okta_secret],
)
async def list_groups_in_org(
    base_url: Annotated[
        str,
        Field(
            ..., description="Okta domain base URL (e.g., 'https://dev-12345.okta.com')"
        ),
    ],
    limit: Annotated[
        int,
        Field(200, description="Number of groups to return (default: 200)"),
    ] = 200,
    search: Annotated[
        str | None,
        Field(None, description="Search expression for filtering groups"),
    ] = None,
    filter: Annotated[
        str | None,
        Field(None, description="Filter expression for groups"),
    ] = None,
    after: Annotated[
        int | None,
        Field(None, description="Result to start from"),
    ] = None,
) -> list[dict[str, Any]]:
    """List groups in Okta organization."""
    headers = _get_okta_headers()

    params: dict[str, Any] = {"limit": limit}
    if search:
        params["search"] = search
    if filter:
        params["filter"] = filter
    if after:
        params["after"] = after

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/api/v1/groups",
            headers=headers,
            params=params,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get group members",
    description="List all users that are members of a specific group.",
    display_group="Okta",
    doc_url="https://developer.okta.com/docs/reference/api/groups/#list-group-members",
    namespace="tools.okta",
    secrets=[okta_secret],
)
async def get_group_members(
    base_url: Annotated[
        str,
        Field(
            ..., description="Okta domain base URL (e.g., 'https://dev-12345.okta.com')"
        ),
    ],
    group_id: Annotated[
        str,
        Field(..., description="ID of the group to get members for"),
    ],
    limit: Annotated[
        int,
        Field(200, description="Number of members to return (default: 200)"),
    ] = 200,
    after: Annotated[
        int | None,
        Field(None, description="Result to start from"),
    ] = None,
) -> list[dict[str, Any]]:
    """Get all members of a specific group."""
    headers = _get_okta_headers()

    params = {"limit": limit}
    if after:
        params["after"] = after

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/api/v1/groups/{group_id}/users",
            headers=headers,
            params=params,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get groups assigned to user",
    description="List all groups that a user is a member of.",
    display_group="Okta",
    doc_url="https://developer.okta.com/docs/reference/api/users/#get-user-groups",
    namespace="tools.okta",
    secrets=[okta_secret],
)
async def get_groups_assigned_to_user(
    base_url: Annotated[
        str,
        Field(
            ..., description="Okta domain base URL (e.g., 'https://dev-12345.okta.com')"
        ),
    ],
    user_id: Annotated[
        str,
        Field(..., description="User ID, login, or email to get group memberships for"),
    ],
) -> list[dict[str, Any]]:
    """Get all groups that a user is assigned to."""
    headers = _get_okta_headers()

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/api/v1/users/{user_id}/groups",
            headers=headers,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Add user to group",
    description="Add a user to a specific group in Okta.",
    display_group="Okta",
    doc_url="https://developer.okta.com/docs/reference/api/groups/#add-user-to-group",
    namespace="tools.okta",
    secrets=[okta_secret],
)
async def add_to_group(
    base_url: Annotated[
        str,
        Field(
            ..., description="Okta domain base URL (e.g., 'https://dev-12345.okta.com')"
        ),
    ],
    user_id: Annotated[
        str,
        Field(..., description="User ID, login, or email to add to the group"),
    ],
    group_id: Annotated[
        str,
        Field(..., description="ID of the group to add the user to"),
    ],
) -> dict[str, Any]:
    """Add a user to a group."""
    headers = _get_okta_headers()

    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"{base_url}/api/v1/groups/{group_id}/users/{user_id}",
            headers=headers,
        )
        response.raise_for_status()
        return {"message": f"User {user_id} successfully added to group {group_id}"}


@registry.register(
    default_title="Remove user from group",
    description="Remove a user from a specific group in Okta.",
    display_group="Okta",
    doc_url="https://developer.okta.com/docs/reference/api/groups/#remove-user-from-group",
    namespace="tools.okta",
    secrets=[okta_secret],
)
async def remove_from_group(
    base_url: Annotated[
        str,
        Field(
            ..., description="Okta domain base URL (e.g., 'https://dev-12345.okta.com')"
        ),
    ],
    user_id: Annotated[
        str,
        Field(..., description="User ID, login, or email to remove from the group"),
    ],
    group_id: Annotated[
        str,
        Field(..., description="ID of the group to remove the user from"),
    ],
) -> dict[str, Any]:
    """Remove a user from a group."""
    headers = _get_okta_headers()

    async with httpx.AsyncClient() as client:
        response = await client.delete(
            f"{base_url}/api/v1/groups/{group_id}/users/{user_id}",
            headers=headers,
        )
        response.raise_for_status()
        return {"message": f"User {user_id} successfully removed from group {group_id}"}


@registry.register(
    default_title="Assign group to application",
    description="Assign a group to an application in Okta.",
    display_group="Okta",
    doc_url="https://developer.okta.com/docs/reference/api/apps/#assign-group-to-application",
    namespace="tools.okta",
    secrets=[okta_secret],
)
async def assign_group_to_app(
    base_url: Annotated[
        str,
        Field(
            ..., description="Okta domain base URL (e.g., 'https://dev-12345.okta.com')"
        ),
    ],
    app_id: Annotated[
        str,
        Field(..., description="Application ID to assign the group to"),
    ],
    group_id: Annotated[
        str,
        Field(..., description="Group ID to assign to the application"),
    ],
    priority: Annotated[
        int | None,
        Field(None, description="Priority of the group assignment (0-100)"),
    ] = None,
) -> dict[str, Any]:
    """Assign a group to an application."""
    headers = _get_okta_headers()

    assignment_data: dict[str, Any] = {"id": group_id}
    if priority is not None:
        assignment_data["priority"] = priority

    async with httpx.AsyncClient() as client:
        response = await client.put(
            f"{base_url}/api/v1/apps/{app_id}/groups/{group_id}",
            headers=headers,
            json=assignment_data,
        )
        response.raise_for_status()
        return response.json()
