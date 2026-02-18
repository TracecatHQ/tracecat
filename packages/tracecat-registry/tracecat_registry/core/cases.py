import base64
from datetime import datetime
import posixpath
from typing import Annotated, Any, Literal
from urllib.parse import unquote, urlsplit
from uuid import UUID

import httpx
from typing_extensions import Doc

from tracecat_registry import config, registry, types
from tracecat_registry.context import get_context
from tracecat_registry.sdk.exceptions import (
    TracecatValidationError,
)


PriorityType = Literal[
    "unknown",
    "low",
    "medium",
    "high",
    "critical",
    "other",
]

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
    "unknown",
    "new",
    "in_progress",
    "on_hold",
    "resolved",
    "closed",
    "other",
]


def _as_list_filter[T](value: T | list[T]) -> list[T]:
    return value if isinstance(value, list) else [value]


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
) -> types.Case:
    params: dict[str, Any] = {}
    if summary is not None:
        params["summary"] = summary
    if description is not None:
        params["description"] = description
    if priority is not None:
        params["priority"] = priority
    if severity is not None:
        params["severity"] = severity
    if status is not None:
        params["status"] = status
    if fields is not None:
        params["fields"] = fields
    if payload is not None:
        params["payload"] = payload
    if tags is not None:
        params["tags"] = tags
    return await get_context().cases.create_case_simple(**params)


@registry.register(
    default_title="Update case",
    display_group="Cases",
    description="Update an existing case.",
    namespace="core.cases",
)
async def update_case(
    case_id: Annotated[
        str,
        Doc("The ID of the case to update."),
    ],
    summary: Annotated[
        str | None,
        Doc("The updated summary of the case."),
    ] = None,
    description: Annotated[
        str | None,
        Doc("The updated description of the case."),
    ] = None,
    priority: Annotated[
        PriorityType | None,
        Doc("The updated priority of the case."),
    ] = None,
    severity: Annotated[
        SeverityType | None,
        Doc("The updated severity of the case."),
    ] = None,
    status: Annotated[
        StatusType | None,
        Doc("The updated status of the case."),
    ] = None,
    fields: Annotated[
        dict[str, Any] | None,
        Doc("Updated custom fields for the case."),
    ] = None,
    payload: Annotated[
        dict[str, Any] | None,
        Doc("Updated payload for the case."),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Doc(
            "List of tag identifiers (IDs or refs) to set on the case. This will replace all existing tags."
        ),
    ] = None,
    append: Annotated[
        bool,
        Doc(
            "If true, append the provided description to the existing description when it is not empty."
        ),
    ] = False,
) -> types.Case:
    client_params: dict[str, Any] = {}
    if summary is not None:
        client_params["summary"] = summary
    if description is not None:
        client_params["description"] = description
    if priority is not None:
        client_params["priority"] = priority
    if severity is not None:
        client_params["severity"] = severity
    if status is not None:
        client_params["status"] = status
    if fields is not None:
        # Empty dict or None means fields are not updated
        # You must explicitly set fields to None to remove their values
        # If we don't pass fields, the service will not try to update the fields
        client_params["fields"] = fields
    if payload is not None:
        client_params["payload"] = payload
    if tags is not None:
        client_params["tags"] = tags
    if append and description is not None:
        client_params["append_description"] = True
    return await get_context().cases.update_case_simple(case_id, **client_params)


@registry.register(
    default_title="Create case comment",
    display_group="Cases",
    description="Add a comment to an existing case.",
    namespace="core.cases",
)
async def create_comment(
    case_id: Annotated[
        str,
        Doc("The ID of the case to comment on."),
    ],
    content: Annotated[
        str,
        Doc("The comment content."),
    ],
    parent_id: Annotated[
        str | None,
        Doc("The ID of the parent comment if this is a reply."),
    ] = None,
) -> types.CaseComment:
    params: dict[str, Any] = {"content": content}
    if parent_id is not None:
        params["parent_id"] = parent_id
    return await get_context().cases.create_comment_simple(case_id, **params)


@registry.register(
    default_title="Update case comment",
    display_group="Cases",
    description="Update an existing case comment.",
    namespace="core.cases",
)
async def update_comment(
    comment_id: Annotated[
        str,
        Doc("The ID of the comment to update."),
    ],
    content: Annotated[
        str | None,
        Doc("The updated comment content."),
    ] = None,
    parent_id: Annotated[
        str | None,
        Doc("The updated parent comment ID."),
    ] = None,
) -> types.CaseComment:
    client_params: dict[str, Any] = {}
    if content is not None:
        client_params["content"] = content
    if parent_id is not None:
        client_params["parent_id"] = parent_id
    return await get_context().cases.update_comment_simple(comment_id, **client_params)


@registry.register(
    default_title="Get case",
    display_group="Cases",
    description="Get details of a specific case by ID.",
    namespace="core.cases",
)
async def get_case(
    case_id: Annotated[
        str,
        Doc("The ID of the case to retrieve."),
    ],
) -> types.CaseRead:
    return await get_context().cases.get_case(case_id)


@registry.register(
    default_title="List cases",
    display_group="Cases",
    description="List all cases.",
    namespace="core.cases",
)
async def list_cases(
    limit: Annotated[
        int,
        Doc("Maximum number of cases to return."),
    ] = 100,
    cursor: Annotated[
        str | None,
        Doc(
            "Pagination cursor used to fetch a specific page. Response metadata is returned when paginate=true."
        ),
    ] = None,
    reverse: Annotated[
        bool,
        Doc(
            "Reverse pagination direction. Response metadata is returned when paginate=true."
        ),
    ] = False,
    order_by: Annotated[
        Literal["created_at", "updated_at", "priority", "severity", "status", "tasks"]
        | None,
        Doc("The field to order the cases by."),
    ] = None,
    sort: Annotated[
        Literal["asc", "desc"] | None,
        Doc("The direction to order the cases by."),
    ] = None,
    paginate: Annotated[
        bool,
        Doc("If true, return cursor pagination metadata along with items."),
    ] = False,
) -> list[types.CaseReadMinimal] | types.CaseListResponse:
    if limit > config.TRACECAT__LIMIT_CURSOR_MAX:
        raise TracecatValidationError(
            detail=f"Limit cannot be greater than {config.TRACECAT__LIMIT_CURSOR_MAX}"
        )

    params: dict[str, Any] = {"limit": limit}
    if cursor is not None:
        params["cursor"] = cursor
    if reverse:
        params["reverse"] = reverse
    if order_by is not None:
        params["order_by"] = order_by
    if sort is not None:
        params["sort"] = sort
    response = await get_context().cases.list_cases(**params)
    if paginate:
        return response
    return response["items"]


@registry.register(
    default_title="Search cases",
    display_group="Cases",
    description="Search cases based on various criteria.",
    namespace="core.cases",
)
async def search_cases(
    search_term: Annotated[
        str | None,
        Doc("Text to search for in case summary and description."),
    ] = None,
    status: Annotated[
        StatusType | list[StatusType] | None,
        Doc("Filter by case status."),
    ] = None,
    priority: Annotated[
        PriorityType | list[PriorityType] | None,
        Doc("Filter by case priority."),
    ] = None,
    severity: Annotated[
        SeverityType | list[SeverityType] | None,
        Doc("Filter by case severity."),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Doc("Filter by tag IDs or refs (AND logic)."),
    ] = None,
    assignee_id: Annotated[
        str | list[str] | None,
        Doc("Filter by assignee ID or 'unassigned'."),
    ] = None,
    dropdown: Annotated[
        list[str] | None,
        Doc("Filter by dropdown values in definition_ref:option_ref format."),
    ] = None,
    start_time: Annotated[
        datetime | str | None,
        Doc("Filter cases created after this time."),
    ] = None,
    end_time: Annotated[
        datetime | str | None,
        Doc("Filter cases created before this time."),
    ] = None,
    updated_before: Annotated[
        datetime | str | None,
        Doc("Filter cases updated before this time."),
    ] = None,
    updated_after: Annotated[
        datetime | str | None,
        Doc("Filter cases updated after this time."),
    ] = None,
    limit: Annotated[
        int,
        Doc("Maximum number of cases to return."),
    ] = 100,
    cursor: Annotated[
        str | None,
        Doc(
            "Pagination cursor used to fetch a specific page. Response metadata is returned when paginate=true."
        ),
    ] = None,
    reverse: Annotated[
        bool,
        Doc(
            "Reverse pagination direction. Response metadata is returned when paginate=true."
        ),
    ] = False,
    order_by: Annotated[
        Literal["created_at", "updated_at", "priority", "severity", "status", "tasks"]
        | None,
        Doc("The field to order the cases by."),
    ] = None,
    sort: Annotated[
        Literal["asc", "desc"] | None,
        Doc("The direction to order the cases by."),
    ] = None,
    paginate: Annotated[
        bool,
        Doc("If true, return cursor pagination metadata along with items."),
    ] = False,
) -> list[types.CaseReadMinimal] | types.CaseListResponse:
    """Search cases based on various criteria."""
    if limit > config.TRACECAT__LIMIT_CURSOR_MAX:
        raise TracecatValidationError(
            detail=f"Limit cannot be greater than {config.TRACECAT__LIMIT_CURSOR_MAX}"
        )

    params: dict[str, Any] = {"limit": limit}
    if cursor is not None:
        params["cursor"] = cursor
    if reverse:
        params["reverse"] = reverse
    if search_term is not None:
        params["search_term"] = search_term
    if status is not None:
        params["status"] = _as_list_filter(status)
    if priority is not None:
        params["priority"] = _as_list_filter(priority)
    if severity is not None:
        params["severity"] = _as_list_filter(severity)
    if tags is not None:
        params["tags"] = tags
    if assignee_id is not None:
        params["assignee_id"] = _as_list_filter(assignee_id)
    if dropdown is not None:
        params["dropdown"] = dropdown
    if start_time is not None:
        params["start_time"] = start_time
    if end_time is not None:
        params["end_time"] = end_time
    if updated_before is not None:
        params["updated_before"] = updated_before
    if updated_after is not None:
        params["updated_after"] = updated_after
    if order_by is not None:
        params["order_by"] = order_by
    if sort is not None:
        params["sort"] = sort
    response = await get_context().cases.search_cases(**params)
    if paginate:
        return response
    return response["items"]


@registry.register(
    default_title="Delete case",
    display_group="Cases",
    description="Delete a case.",
    namespace="core.cases",
)
async def delete_case(
    case_id: Annotated[
        str,
        Doc("The ID of the case to delete."),
    ],
) -> None:
    await get_context().cases.delete_case(case_id)


@registry.register(
    default_title="List case events",
    display_group="Cases",
    description="List all events for a case in chronological order.",
    namespace="core.cases",
)
async def list_case_events(
    case_id: Annotated[
        str,
        Doc("The ID of the case to get events for."),
    ],
) -> types.CaseEventsWithUsers:
    return await get_context().cases.list_events(case_id)


@registry.register(
    default_title="List case comments",
    display_group="Cases",
    description="List all comments for a case.",
    namespace="core.cases",
)
async def list_comments(
    case_id: Annotated[
        str,
        Doc("The ID of the case to get comments for."),
    ],
) -> list[types.CaseCommentRead]:
    return await get_context().cases.list_comments(case_id)


@registry.register(
    default_title="Assign user to case",
    display_group="Cases",
    description="Assign a user to an existing case.",
    namespace="core.cases",
)
async def assign_user(
    case_id: Annotated[
        str,
        Doc("The ID of the case to assign a user to."),
    ],
    assignee_id: Annotated[
        str,
        Doc("The ID of the user to assign to the case."),
    ],
) -> types.Case:
    return await get_context().cases.assign_user_simple(
        case_id,
        assignee_id=assignee_id,
    )


@registry.register(
    default_title="Assign user by email to case",
    display_group="Cases",
    description="Assign a user to an existing case by email.",
    namespace="core.cases",
)
async def assign_user_by_email(
    case_id: Annotated[
        str,
        Doc("The ID of the case to assign a user to."),
    ],
    assignee_email: Annotated[
        str,
        Doc("The email of the user to assign to the case."),
    ],
) -> types.Case:
    return await get_context().cases.assign_user_by_email(
        case_id,
        email=assignee_email,
    )


@registry.register(
    default_title="Add tag to case",
    display_group="Cases",
    description="Add a tag to a case by tag ID or ref.",
    namespace="core.cases",
)
async def add_case_tag(
    case_id: Annotated[
        str,
        Doc("The ID of the case to add a tag to."),
    ],
    tag: Annotated[
        str,
        Doc("The tag identifier (ID or ref) to add to the case."),
    ],
    create_if_missing: Annotated[
        bool,
        Doc("If true, create the tag if it does not exist."),
    ] = False,
) -> types.TagRead:
    return await get_context().cases.add_tag(
        case_id,
        tag_id=tag,
        create_if_missing=create_if_missing,
    )


@registry.register(
    default_title="Remove tag from case",
    display_group="Cases",
    description="Remove a tag from a case by tag ID or ref.",
    namespace="core.cases",
)
async def remove_case_tag(
    case_id: Annotated[
        str,
        Doc("The ID of the case to remove a tag from."),
    ],
    tag: Annotated[
        str,
        Doc("The tag identifier (ID or ref) to remove from the case."),
    ],
) -> None:
    await get_context().cases.remove_tag(case_id, tag_id=tag)


async def _upload_attachment(
    case_id: str,
    file_name: str,
    content: bytes,
    content_type: str,
) -> types.CaseAttachmentRead:
    """Upload an attachment to a case."""
    try:
        case_uuid = UUID(case_id)
    except ValueError as e:
        raise TracecatValidationError(
            detail=f"Invalid case ID format: {case_id}"
        ) from e

    content_base64 = base64.b64encode(content).decode("utf-8")
    return await get_context().cases.create_attachment(
        str(case_uuid),
        filename=file_name,
        content_base64=content_base64,
        content_type=content_type,
    )


@registry.register(
    default_title="Upload attachment",
    display_group="Cases",
    description="Upload a file attachment to a case. File size and type restrictions apply for security.",
    namespace="core.cases",
)
async def upload_attachment(
    case_id: Annotated[
        str,
        Doc("The ID of the case to attach the file to."),
    ],
    file_name: Annotated[
        str,
        Doc("The original filename."),
    ],
    content_base64: Annotated[
        str,
        Doc("The file content encoded in base64."),
    ],
    content_type: Annotated[
        str,
        Doc("The MIME type of the file (e.g., 'application/pdf')."),
    ],
) -> types.CaseAttachmentRead:
    """Upload a file attachment to a case."""
    # Decode base64 content
    try:
        content = base64.b64decode(content_base64, validate=True)
    except Exception as e:
        raise TracecatValidationError(
            detail=f"Invalid base64 encoding: {str(e)}"
        ) from e

    return await _upload_attachment(case_id, file_name, content, content_type)


def _infer_filename_from_url(url: str) -> str:
    """Infer a safe filename from a URL path with conservative fallbacks."""
    parsed = urlsplit(url.strip())
    path = parsed.path.rstrip("/")

    if path:
        filename = unquote(posixpath.basename(path))
        if filename:
            return filename

    raise TracecatValidationError(detail=f"Unable to infer filename from URL: {url}")


@registry.register(
    default_title="Upload attachment from URL",
    display_group="Cases",
    description="Upload a file attachment to a case from a URL.",
    namespace="core.cases",
)
async def upload_attachment_from_url(
    case_id: Annotated[
        str,
        Doc("The ID of the case to attach the file to."),
    ],
    url: Annotated[
        str,
        Doc("The URL of the file to upload."),
    ],
    headers: Annotated[
        dict[str, str] | None,
        Doc("The headers to use when downloading the file."),
    ] = None,
    file_name: Annotated[
        str | None,
        Doc(
            "Filename of the file to upload. If not provided, the filename will be inferred from the URL."
        ),
    ] = None,
) -> types.CaseAttachmentRead:
    """Upload a file attachment to a case from a URL."""
    async with httpx.AsyncClient() as client:
        response = await client.get(url, headers=headers)
        response.raise_for_status()
        content = response.content
        content_type = response.headers.get("Content-Type")

    if not content:
        raise TracecatValidationError(
            detail=f"No content found in response from URL: {url}"
        )

    if not content_type:
        raise TracecatValidationError(
            detail=f"No content type found in response from URL: {url}"
        )

    file_name = file_name or _infer_filename_from_url(url)

    return await _upload_attachment(case_id, file_name, content, content_type)


@registry.register(
    default_title="List attachments",
    display_group="Cases",
    description="List all attachments for a case.",
    namespace="core.cases",
)
async def list_attachments(
    case_id: Annotated[
        str,
        Doc("The ID of the case to list attachments for."),
    ],
) -> list[types.CaseAttachmentRead]:
    """List all attachments for a case."""
    # Validate case_id format
    try:
        case_uuid = UUID(case_id)
    except ValueError as e:
        raise TracecatValidationError(
            detail=f"Invalid case ID format: {case_id}"
        ) from e

    return await get_context().cases.list_attachments(str(case_uuid))


@registry.register(
    default_title="Download attachment",
    display_group="Cases",
    description="Download an attachment's content. File integrity is verified via SHA256.",
    namespace="core.cases",
)
async def download_attachment(
    case_id: Annotated[
        str,
        Doc("The ID of the case containing the attachment."),
    ],
    attachment_id: Annotated[
        str,
        Doc("The ID of the attachment to download."),
    ],
) -> types.CaseAttachmentDownloadData:
    """Download an attachment's content.

    Returns the file content as base64 encoded string along with metadata.
    File integrity is automatically verified via SHA256 hash.
    """
    # Validate UUID formats
    try:
        case_uuid = UUID(case_id)
        attachment_uuid = UUID(attachment_id)
    except ValueError as e:
        raise TracecatValidationError(detail=f"Invalid ID format: {str(e)}") from e

    return await get_context().cases.download_attachment(
        case_uuid,
        attachment_uuid,
    )


@registry.register(
    default_title="Get attachment",
    display_group="Cases",
    description="Get attachment metadata without downloading the content.",
    namespace="core.cases",
)
async def get_attachment(
    case_id: Annotated[
        str,
        Doc("The ID of the case containing the attachment."),
    ],
    attachment_id: Annotated[
        str,
        Doc("The ID of the attachment to get."),
    ],
) -> types.CaseAttachmentRead:
    """Get attachment metadata without downloading the content."""
    # Validate UUID formats
    try:
        case_uuid = UUID(case_id)
        attachment_uuid = UUID(attachment_id)
    except ValueError as e:
        raise TracecatValidationError(detail=f"Invalid ID format: {str(e)}") from e

    return await get_context().cases.get_attachment_metadata(
        case_uuid,
        attachment_uuid,
    )


@registry.register(
    default_title="Delete attachment",
    display_group="Cases",
    description="Delete an attachment from a case. Only the creator or admins can delete attachments.",
    namespace="core.cases",
)
async def delete_attachment(
    case_id: Annotated[
        str,
        Doc("The ID of the case containing the attachment."),
    ],
    attachment_id: Annotated[
        str,
        Doc("The ID of the attachment to delete."),
    ],
) -> None:
    """Delete an attachment from a case.

    This performs a soft delete, preserving the audit trail while removing
    the file from storage. Only the attachment creator or admins can delete.
    """
    try:
        case_uuid = UUID(case_id)
        attachment_uuid = UUID(attachment_id)
    except ValueError as e:
        raise TracecatValidationError(detail=f"Invalid ID format: {str(e)}") from e

    await get_context().cases.delete_attachment(
        case_uuid,
        attachment_uuid,
    )


@registry.register(
    default_title="Get attachment download URL",
    display_group="Cases",
    description="Get a presigned S3 URL for downloading an attachment.",
    namespace="core.cases",
)
async def get_attachment_download_url(
    case_id: Annotated[
        str,
        Doc("The ID of the case containing the attachment."),
    ],
    attachment_id: Annotated[
        str,
        Doc("The ID of the attachment."),
    ],
    expiry: Annotated[
        int | None,
        Doc(
            "URL expiry time in seconds. If not provided, uses the default from configuration."
        ),
    ] = None,
) -> str:
    """Get a presigned S3 URL for downloading an attachment."""
    # Validate UUID formats
    try:
        case_uuid = UUID(case_id)
        attachment_uuid = UUID(attachment_id)
    except ValueError as e:
        raise TracecatValidationError(detail=f"Invalid ID format: {str(e)}") from e

    # Validate expiry if provided
    if expiry is not None:
        if expiry <= 0:
            raise TracecatValidationError(
                detail="Expiry must be a positive number of seconds"
            )
        if expiry > 86400:  # 24 hours
            raise TracecatValidationError(
                detail="Expiry cannot exceed 24 hours (86400 seconds)"
            )

    return await get_context().cases.get_attachment_presigned_url(
        case_uuid,
        attachment_uuid,
        expiry=expiry,
    )
