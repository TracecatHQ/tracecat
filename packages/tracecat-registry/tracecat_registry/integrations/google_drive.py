"""Google Drive integration UDFs for Tracecat.

This module provides Google Drive API integration for security automation workflows,
including file management, permission auditing, and data loss prevention.

Uses Tracecat's built-in OAuth system for seamless authentication.
Configure the 'google_drive' OAuth integration in Tracecat UI, then users
can click "Connect with OAuth" to authorize Drive access.
"""

from typing import Annotated, Any

import httpx
from pydantic import Field

from tracecat_registry import RegistryOAuthSecret, registry, secrets


# Use Tracecat's built-in OAuth system
drive_oauth_secret = RegistryOAuthSecret(
    provider_id="google_drive",
    grant_type="authorization_code",
)
"""Google Drive OAuth credentials.

Configure the 'google_drive' OAuth integration in Tracecat with:
- OAuth 2.0 client credentials from Google Cloud Console
- Required scopes: https://www.googleapis.com/auth/drive

See: https://developers.google.com/drive/api/guides/about-auth
"""


def _get_drive_token() -> str:
    """Get access token from Tracecat's OAuth system."""
    return secrets.get("GOOGLE_DRIVE_USER_TOKEN")


@registry.register(
    default_title="List Drive files",
    display_group="Google Drive",
    description="List and search files in Google Drive",
    namespace="tools.google_drive",
    secrets=[drive_oauth_secret],
)
async def list_files(
    query: Annotated[
        str, Field(default="", description="Search query (e.g., 'name contains \"report\"')")
    ] = "",
    max_results: Annotated[
        int, Field(default=10, description="Maximum number of results")
    ] = 10,
    order_by: Annotated[
        str, Field(default="modifiedTime desc", description="Sort order")
    ] = "modifiedTime desc",
    fields: Annotated[
        str,
        Field(
            default="*",
            description="Fields to return (e.g., 'files(id,name,mimeType)' or '*' for all)",
        ),
    ] = "*",
) -> dict[str, Any]:
    """
    List and search files in Google Drive.

    Query examples:
    - name contains 'report' - Files with 'report' in name
    - mimeType = 'application/pdf' - PDF files only
    - sharedWithMe - Files shared with you
    - 'me' in owners - Files you own
    - modifiedTime > '2024-01-01' - Recently modified
    - trashed = false - Not in trash

    Returns the full API response including files array and nextPageToken.
    """
    access_token = _get_drive_token()

    async with httpx.AsyncClient() as client:
        params: dict[str, Any] = {
            "pageSize": max_results,
            "orderBy": order_by,
            "fields": fields,
        }
        if query:
            params["q"] = query

        response = await client.get(
            "https://www.googleapis.com/drive/v3/files",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get Drive file",
    display_group="Google Drive",
    description="Get metadata for a specific file",
    namespace="tools.google_drive",
    secrets=[drive_oauth_secret],
)
async def get_file(
    file_id: Annotated[str, Field(description="The file ID")],
    fields: Annotated[
        str, Field(default="*", description="Fields to return ('*' for all)")
    ] = "*",
) -> dict[str, Any]:
    """
    Get detailed metadata for a specific file.

    Returns complete file information including permissions, sharing status,
    owners, and other metadata.
    """
    access_token = _get_drive_token()

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": fields},
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Upload Drive file",
    display_group="Google Drive",
    description="Upload a file to Google Drive",
    namespace="tools.google_drive",
    secrets=[drive_oauth_secret],
)
async def upload_file(
    file_name: Annotated[str, Field(description="Name for the uploaded file")],
    file_content: Annotated[str, Field(description="Base64-encoded file content")],
    mime_type: Annotated[
        str, Field(default="application/octet-stream", description="MIME type of file")
    ] = "application/octet-stream",
    parent_folder_id: Annotated[
        str, Field(default="", description="Parent folder ID (empty = root)")
    ] = "",
    fields: Annotated[
        str, Field(default="*", description="Fields to return ('*' for all)")
    ] = "*",
) -> dict[str, Any]:
    """
    Upload a file to Google Drive.

    Returns uploaded file metadata including id and webViewLink.
    """
    access_token = _get_drive_token()

    import base64
    import json

    content_bytes = base64.b64decode(file_content)

    metadata: dict[str, Any] = {"name": file_name, "mimeType": mime_type}
    if parent_folder_id:
        metadata["parents"] = [parent_folder_id]

    # Construct multipart/related request with metadata and file content
    boundary = "tracecat_boundary"
    multipart_body = (
        f"--{boundary}\r\n"
        f"Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{json.dumps(metadata)}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: {mime_type}\r\n\r\n"
    ).encode("utf-8") + content_bytes + f"\r\n--{boundary}--".encode("utf-8")

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://www.googleapis.com/upload/drive/v3/files",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Content-Type": f"multipart/related; boundary={boundary}",
            },
            params={"uploadType": "multipart", "fields": fields},
            content=multipart_body,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Create Drive folder",
    display_group="Google Drive",
    description="Create a new folder in Google Drive",
    namespace="tools.google_drive",
    secrets=[drive_oauth_secret],
)
async def create_folder(
    folder_name: Annotated[str, Field(description="Name for the new folder")],
    parent_folder_id: Annotated[
        str, Field(default="", description="Parent folder ID (empty = root)")
    ] = "",
    fields: Annotated[
        str, Field(default="*", description="Fields to return ('*' for all)")
    ] = "*",
) -> dict[str, Any]:
    """
    Create a new folder in Google Drive.

    Returns created folder metadata including id and webViewLink.
    Useful for organizing incident investigations or security reports.
    """
    access_token = _get_drive_token()

    metadata: dict[str, Any] = {
        "name": folder_name,
        "mimeType": "application/vnd.google-apps.folder",
    }
    if parent_folder_id:
        metadata["parents"] = [parent_folder_id]

    async with httpx.AsyncClient() as client:
        response = await client.post(
            "https://www.googleapis.com/drive/v3/files",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": fields},
            json=metadata,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Delete Drive file",
    display_group="Google Drive",
    description="Move a file or folder to trash",
    namespace="tools.google_drive",
    secrets=[drive_oauth_secret],
)
async def delete_file(
    file_id: Annotated[str, Field(description="The file or folder ID to delete")],
    fields: Annotated[
        str, Field(default="*", description="Fields to return ('*' for all)")
    ] = "*",
) -> dict[str, Any]:
    """
    Move a file or folder to trash.

    The file can be restored from trash. For permanent deletion,
    use permanently_delete_file.
    """
    access_token = _get_drive_token()

    async with httpx.AsyncClient() as client:
        response = await client.patch(
            f"https://www.googleapis.com/drive/v3/files/{file_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": fields},
            json={"trashed": True},
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Permanently delete Drive file",
    display_group="Google Drive",
    description="Permanently delete a file (bypass trash)",
    namespace="tools.google_drive",
    secrets=[drive_oauth_secret],
)
async def permanently_delete_file(
    file_id: Annotated[
        str, Field(description="The file or folder ID to permanently delete")
    ],
) -> dict[str, Any]:
    """
    Permanently delete a file or folder (bypass trash).

    WARNING: This action cannot be undone. The file is deleted immediately
    and cannot be recovered.
    """
    access_token = _get_drive_token()

    async with httpx.AsyncClient() as client:
        response = await client.delete(
            f"https://www.googleapis.com/drive/v3/files/{file_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        # Return empty dict if no content (DELETE typically returns 204)
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()


@registry.register(
    default_title="Get Drive file permissions",
    display_group="Google Drive",
    description="List all permissions for a file",
    namespace="tools.google_drive",
    secrets=[drive_oauth_secret],
)
async def get_file_permissions(
    file_id: Annotated[str, Field(description="The file ID")],
    fields: Annotated[
        str, Field(default="*", description="Fields to return ('*' for all)")
    ] = "*",
) -> dict[str, Any]:
    """
    List all permissions for a file or folder.

    Returns the full API response with permissions array showing who has access,
    their role, and permission details. Useful for access auditing and compliance.
    """
    access_token = _get_drive_token()

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": fields},
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Add Drive permission",
    display_group="Google Drive",
    description="Grant access to a file or folder",
    namespace="tools.google_drive",
    secrets=[drive_oauth_secret],
)
async def add_permission(
    file_id: Annotated[str, Field(description="The file or folder ID")],
    email: Annotated[str, Field(description="Email address to grant access to")],
    role: Annotated[
        str, Field(description="Role: reader, writer, commenter, owner")
    ] = "reader",
    send_notification: Annotated[
        bool, Field(default=False, description="Send email notification")
    ] = False,
    fields: Annotated[
        str, Field(default="*", description="Fields to return ('*' for all)")
    ] = "*",
) -> dict[str, Any]:
    """
    Grant access to a file or folder.

    Roles:
    - reader: View only
    - writer: Edit access
    - commenter: Can comment
    - owner: Full control

    Returns the created permission.
    """
    access_token = _get_drive_token()

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions",
            headers={"Authorization": f"Bearer {access_token}"},
            params={
                "sendNotificationEmail": str(send_notification).lower(),
                "fields": fields,
            },
            json={
                "type": "user",
                "role": role,
                "emailAddress": email,
            },
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Update Drive permission",
    display_group="Google Drive",
    description="Modify an existing permission",
    namespace="tools.google_drive",
    secrets=[drive_oauth_secret],
)
async def update_permission(
    file_id: Annotated[str, Field(description="The file or folder ID")],
    permission_id: Annotated[str, Field(description="The permission ID to update")],
    role: Annotated[str, Field(description="New role: reader, writer, commenter")],
    fields: Annotated[
        str, Field(default="*", description="Fields to return ('*' for all)")
    ] = "*",
) -> dict[str, Any]:
    """
    Update an existing permission (change role).

    Useful for downgrading access (e.g., writer → reader) or
    upgrading access as needed.
    """
    access_token = _get_drive_token()

    async with httpx.AsyncClient() as client:
        response = await client.patch(
            f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions/{permission_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"fields": fields},
            json={"role": role},
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Revoke Drive permission",
    display_group="Google Drive",
    description="Remove access to a file or folder",
    namespace="tools.google_drive",
    secrets=[drive_oauth_secret],
)
async def revoke_permission(
    file_id: Annotated[str, Field(description="The file or folder ID")],
    permission_id: Annotated[str, Field(description="The permission ID to revoke")],
) -> dict[str, Any]:
    """
    Remove a permission (revoke access).

    Useful for:
    - Removing external shares
    - Revoking access after incidents
    - Automated access cleanup
    """
    access_token = _get_drive_token()

    async with httpx.AsyncClient() as client:
        response = await client.delete(
            f"https://www.googleapis.com/drive/v3/files/{file_id}/permissions/{permission_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        # Return empty dict if no content (DELETE typically returns 204)
        if response.status_code == 204 or not response.content:
            return {}
        return response.json()
