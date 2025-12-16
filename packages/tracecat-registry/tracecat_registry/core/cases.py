"""Core Cases UDFs (HTTP-only, standalone registry).

These actions call Tracecat's executor/internal APIs via `tracecat_registry.sdk`.
"""

import base64
import posixpath
from datetime import datetime
from typing import Annotated, Any, Literal
from urllib.parse import unquote, urlsplit

import httpx
from tracecat_registry.context import get_context
from tracecat_registry.sdk.types import UNSET
from typing_extensions import Doc

from tracecat_registry import registry
from tracecat_registry.sdk.client import TracecatClient

PriorityType = Literal["unknown", "low", "medium", "high", "critical", "other"]
SeverityType = Literal[
    "unknown",
    "informational",
    "low",
    "medium",
    "high",
    "critical",
    "fatal",
    "other",
]
StatusType = Literal[
    "unknown", "new", "in_progress", "on_hold", "resolved", "closed", "other"
]


def _infer_filename_from_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    path = parsed.path.rstrip("/")
    if path:
        filename = unquote(posixpath.basename(path))
        if filename:
            return filename
    raise ValueError(f"Unable to infer filename from URL: {url}")


@registry.register(
    default_title="Create case",
    display_group="Cases",
    description="Create a new case.",
    namespace="core.cases",
)
async def create_case(
    summary: Annotated[
        str,
        Doc("The summary of the case."),
    ],
    description: Annotated[
        str,
        Doc("The description of the case."),
    ],
    priority: Annotated[
        PriorityType,
        Doc("The priority of the case."),
    ] = "unknown",
    severity: Annotated[
        SeverityType,
        Doc("The severity of the case."),
    ] = "unknown",
    status: Annotated[
        StatusType,
        Doc("The status of the case."),
    ] = "unknown",
    fields: Annotated[
        dict[str, Any] | None,
        Doc("Custom fields for the case."),
    ] = None,
    payload: Annotated[
        dict[str, Any] | None,
        Doc("Payload for the case."),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Doc("List of tag identifiers (IDs or refs) to add to the case."),
    ] = None,
) -> dict[str, Any]:
    ctx = get_context()
    case = await ctx.cases.create_case(
        summary=summary,
        description=description,
        priority=priority,
        severity=severity,
        status=status,
        fields=fields,
        payload=payload,
        tags=tags,
    )
    return case


@registry.register(
    default_title="Update case",
    display_group="Cases",
    description="Update an existing case.",
    namespace="core.cases",
)
async def update_case(
    case_id: Annotated[str, Doc("The ID of the case to update.")],
    summary: Annotated[str | None, Doc("Updated summary.")] = None,
    description: Annotated[str | None, Doc("Updated description.")] = None,
    priority: Annotated[PriorityType | None, Doc("Updated priority.")] = None,
    severity: Annotated[SeverityType | None, Doc("Updated severity.")] = None,
    status: Annotated[StatusType | None, Doc("Updated status.")] = None,
    fields: Annotated[dict[str, Any] | None, Doc("Updated custom fields.")] = None,
    payload: Annotated[dict[str, Any] | None, Doc("Updated payload.")] = None,
    tags: Annotated[
        list[str] | None, Doc("Replace all tags with these identifiers.")
    ] = None,
    append: Annotated[bool, Doc("Append description if true.")] = False,
) -> dict[str, Any]:
    ctx = get_context()
    if description is not None and append:
        existing = await ctx.cases.get_case(case_id)
        existing_desc = existing.get("description") or ""
        description = (
            f"{existing_desc}\n{description}" if existing_desc else description
        )

    if tags is not None:
        existing_tags = await ctx.cases.list_tags(case_id)
        for tag in existing_tags:
            await ctx.cases.remove_tag(case_id=case_id, tag_id=tag["id"])
        for tag_identifier in tags:
            await ctx.cases.add_tag(case_id=case_id, tag_id=tag_identifier)

    return await ctx.cases.update_case(
        case_id=case_id,
        summary=summary if summary is not None else UNSET,
        description=description if description is not None else UNSET,
        priority=priority if priority is not None else UNSET,
        severity=severity if severity is not None else UNSET,
        status=status if status is not None else UNSET,
        fields=fields if fields is not None else UNSET,
        payload=payload if payload is not None else UNSET,
        tags=tags if tags is not None else UNSET,
    )


@registry.register(
    default_title="Create case comment",
    display_group="Cases",
    description="Add a comment to an existing case.",
    namespace="core.cases",
)
async def create_comment(
    case_id: Annotated[str, Doc("Case ID.")],
    content: Annotated[str, Doc("Comment content.")],
    parent_id: Annotated[str | None, Doc("Optional parent comment ID.")] = None,
) -> dict[str, Any]:
    ctx = get_context()
    return await ctx.cases.create_comment(
        case_id=case_id,
        content=content,
        parent_id=parent_id if parent_id is not None else UNSET,
    )


@registry.register(
    default_title="Update case comment",
    display_group="Cases",
    description="Update an existing case comment.",
    namespace="core.cases",
)
async def update_comment(
    case_id: Annotated[str, Doc("The case ID containing the comment.")],
    comment_id: Annotated[str, Doc("The comment ID to update.")],
    content: Annotated[str, Doc("The updated comment content.")],
) -> dict[str, Any]:
    ctx = get_context()
    await ctx.cases.update_comment(
        case_id=case_id, comment_id=comment_id, content=content
    )
    return {"ok": True}


@registry.register(
    default_title="Get case",
    display_group="Cases",
    description="Get details of a specific case by ID.",
    namespace="core.cases",
)
async def get_case(case_id: Annotated[str, Doc("Case ID.")]) -> dict[str, Any]:
    ctx = get_context()
    return await ctx.cases.get_case(case_id)


@registry.register(
    default_title="List cases",
    display_group="Cases",
    description="List cases (returns current page items only).",
    namespace="core.cases",
)
async def list_cases(
    limit: Annotated[int, Doc("Maximum number of cases to return.")] = 100,
    order_by: Annotated[
        Literal["created_at", "updated_at", "priority", "severity", "status"] | None,
        Doc("Order by field."),
    ] = None,
    sort: Annotated[Literal["asc", "desc"] | None, Doc("Sort direction.")] = None,
) -> list[dict[str, Any]]:
    ctx = get_context()

    kwargs: dict[str, Any] = {
        "limit": limit,
    }
    if order_by is not None:
        kwargs["order_by"] = order_by
    if sort is not None:
        kwargs["sort"] = sort
    page = await ctx.cases.list_cases(**kwargs)
    return page.get("items", [])


@registry.register(
    default_title="Search cases",
    display_group="Cases",
    description="Search cases based on criteria.",
    namespace="core.cases",
)
async def search_cases(
    search_term: Annotated[str | None, Doc("Search term.")] = None,
    status: Annotated[StatusType | None, Doc("Filter by status.")] = None,
    priority: Annotated[PriorityType | None, Doc("Filter by priority.")] = None,
    severity: Annotated[SeverityType | None, Doc("Filter by severity.")] = None,
    start_time: Annotated[datetime | str | None, Doc("Created after.")] = None,
    end_time: Annotated[datetime | str | None, Doc("Created before.")] = None,
    updated_before: Annotated[datetime | str | None, Doc("Updated before.")] = None,
    updated_after: Annotated[datetime | str | None, Doc("Updated after.")] = None,
    order_by: Annotated[
        Literal["created_at", "updated_at", "priority", "severity", "status"] | None,
        Doc("Order by field."),
    ] = None,
    sort: Annotated[Literal["asc", "desc"] | None, Doc("Sort direction.")] = None,
    tags: Annotated[list[str] | None, Doc("Filter by tags (AND).")] = None,
    limit: Annotated[int | None, Doc("Max results.")] = None,
) -> list[dict[str, Any]]:
    params: dict[str, Any] = {}
    if search_term is not None:
        params["search_term"] = search_term
    if status is not None:
        params["status"] = [status]
    if priority is not None:
        params["priority"] = [priority]
    if severity is not None:
        params["severity"] = [severity]
    if tags is not None:
        params["tags"] = tags
    if limit is not None:
        params["limit"] = limit
    if order_by is not None:
        params["order_by"] = order_by
    if sort is not None:
        params["sort"] = sort
    if start_time is not None:
        params["start_time"] = start_time
    if end_time is not None:
        params["end_time"] = end_time
    if updated_after is not None:
        params["updated_after"] = updated_after
    if updated_before is not None:
        params["updated_before"] = updated_before
    ctx = get_context()
    return await ctx.cases.search_cases(**params)


@registry.register(
    default_title="Delete case",
    display_group="Cases",
    description="Delete a case.",
    namespace="core.cases",
)
async def delete_case(case_id: Annotated[str, Doc("Case ID.")]) -> None:
    ctx = get_context()
    await ctx.cases.delete_case(case_id)


@registry.register(
    default_title="List case events",
    display_group="Cases",
    description="List events for a case.",
    namespace="core.cases",
)
async def list_case_events(case_id: Annotated[str, Doc("Case ID.")]) -> dict[str, Any]:
    ctx = get_context()
    return await ctx.cases.list_events(case_id)


@registry.register(
    default_title="List case comments",
    display_group="Cases",
    description="List comments for a case.",
    namespace="core.cases",
)
async def list_comments(
    case_id: Annotated[str, Doc("Case ID.")],
) -> list[dict[str, Any]]:
    ctx = get_context()
    return await ctx.cases.list_comments(case_id)


@registry.register(
    default_title="Assign user to case",
    display_group="Cases",
    description="Assign a user to a case by user ID.",
    namespace="core.cases",
)
async def assign_user(
    case_id: Annotated[str, Doc("Case ID.")],
    assignee_id: Annotated[str, Doc("User ID.")],
) -> dict[str, Any]:
    ctx = get_context()
    return await ctx.cases.update_case(case_id=case_id, assignee_id=assignee_id)


@registry.register(
    default_title="Assign user by email to case",
    display_group="Cases",
    description="Assign a user to a case by email.",
    namespace="core.cases",
)
async def assign_user_by_email(
    case_id: Annotated[str, Doc("Case ID.")],
    assignee_email: Annotated[str, Doc("User email.")],
) -> dict[str, Any]:
    client = TracecatClient()
    user = await client.get("/users/search", params={"email": assignee_email})
    await client.patch(f"/cases/{case_id}", json={"assignee_id": user["id"]})
    return await client.get(f"/cases/{case_id}")


@registry.register(
    default_title="Add tag to case",
    display_group="Cases",
    description="Add a tag to a case by tag ID or ref.",
    namespace="core.cases",
)
async def add_case_tag(
    case_id: Annotated[str, Doc("Case ID.")],
    tag: Annotated[str, Doc("Tag identifier (ID or ref).")],
    create_if_missing: Annotated[bool, Doc("Create tag if missing.")] = False,
) -> dict[str, Any]:
    ctx = get_context()
    try:
        await ctx.cases.add_tag(case_id=case_id, tag_id=tag)
    except Exception:
        if not create_if_missing:
            raise
        created = await ctx.cases.create_tag(name=tag)
        await ctx.cases.add_tag(case_id=case_id, tag_id=created["id"])
    return {"ok": True}


@registry.register(
    default_title="Remove tag from case",
    display_group="Cases",
    description="Remove a tag from a case by tag ID or ref.",
    namespace="core.cases",
)
async def remove_case_tag(
    case_id: Annotated[str, Doc("Case ID.")],
    tag: Annotated[str, Doc("Tag identifier (ID or ref).")],
) -> None:
    ctx = get_context()
    await ctx.cases.remove_tag(case_id=case_id, tag_id=tag)


@registry.register(
    default_title="Upload attachment",
    display_group="Cases",
    description="Upload a file attachment to a case.",
    namespace="core.cases",
)
async def upload_attachment(
    case_id: Annotated[str, Doc("Case ID.")],
    file_name: Annotated[str, Doc("Filename.")],
    content_base64: Annotated[str, Doc("Base64-encoded content.")],
    content_type: Annotated[str, Doc("MIME type.")],
) -> dict[str, Any]:
    ctx = get_context()
    return await ctx.cases.create_attachment(
        case_id=case_id,
        filename=file_name,
        content_base64=content_base64,
        content_type=content_type,
    )


@registry.register(
    default_title="Upload attachment from URL",
    display_group="Cases",
    description="Upload a file attachment to a case from a URL.",
    namespace="core.cases",
)
async def upload_attachment_from_url(
    case_id: Annotated[str, Doc("Case ID.")],
    url: Annotated[str, Doc("URL to download.")],
    headers: Annotated[dict[str, str] | None, Doc("Download headers.")] = None,
    file_name: Annotated[str | None, Doc("Optional filename override.")] = None,
) -> dict[str, Any]:
    async with httpx.AsyncClient() as http:
        resp = await http.get(url, headers=headers)
        resp.raise_for_status()
        content = resp.content
        content_type = resp.headers.get("Content-Type") or "application/octet-stream"
    if not content:
        raise ValueError(f"No content found in response from URL: {url}")
    name = file_name or _infer_filename_from_url(url)
    return await upload_attachment(
        case_id=case_id,
        file_name=name,
        content_base64=base64.b64encode(content).decode("utf-8"),
        content_type=content_type,
    )


@registry.register(
    default_title="List attachments",
    display_group="Cases",
    description="List all attachments for a case.",
    namespace="core.cases",
)
async def list_attachments(
    case_id: Annotated[str, Doc("Case ID.")],
) -> list[dict[str, Any]]:
    ctx = get_context()
    return await ctx.cases.list_attachments(case_id)


@registry.register(
    default_title="Download attachment",
    display_group="Cases",
    description="Download an attachment's content as base64.",
    namespace="core.cases",
)
async def download_attachment(
    case_id: Annotated[str, Doc("Case ID.")],
    attachment_id: Annotated[str, Doc("Attachment ID.")],
) -> dict[str, Any]:
    ctx = get_context()
    return await ctx.cases.download_attachment(
        case_id=case_id, attachment_id=attachment_id
    )


@registry.register(
    default_title="Get attachment",
    display_group="Cases",
    description="Get attachment download info (presigned URL).",
    namespace="core.cases",
)
async def get_attachment(
    case_id: Annotated[str, Doc("Case ID.")],
    attachment_id: Annotated[str, Doc("Attachment ID.")],
) -> dict[str, Any]:
    ctx = get_context()
    return await ctx.cases.get_attachment(case_id=case_id, attachment_id=attachment_id)


@registry.register(
    default_title="Get attachment download URL",
    display_group="Cases",
    description="Get a presigned URL for downloading an attachment.",
    namespace="core.cases",
)
async def get_attachment_download_url(
    case_id: Annotated[str, Doc("The ID of the case containing the attachment.")],
    attachment_id: Annotated[str, Doc("The ID of the attachment.")],
    expiry: Annotated[
        int | None,
        Doc(
            "URL expiry time in seconds. If not provided, uses the default from configuration."
        ),
    ] = None,
) -> str:
    if expiry is not None:
        if expiry <= 0:
            raise ValueError("Expiry must be a positive number of seconds")
        if expiry > 86400:
            raise ValueError("Expiry cannot exceed 24 hours (86400 seconds)")

    ctx = get_context()
    return await ctx.cases.get_attachment_download_url(
        case_id=case_id,
        attachment_id=attachment_id,
        expiry=expiry,
    )


@registry.register(
    default_title="Delete attachment",
    display_group="Cases",
    description="Delete an attachment from a case.",
    namespace="core.cases",
)
async def delete_attachment(
    case_id: Annotated[str, Doc("Case ID.")],
    attachment_id: Annotated[str, Doc("Attachment ID.")],
) -> None:
    ctx = get_context()
    await ctx.cases.delete_attachment(case_id=case_id, attachment_id=attachment_id)
