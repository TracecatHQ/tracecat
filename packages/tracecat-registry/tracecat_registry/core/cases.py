import base64
from datetime import datetime
from typing import Annotated, Any, Literal
from uuid import UUID

from sqlalchemy.exc import NoResultFound, ProgrammingError
from sqlmodel import col, select
from typing_extensions import Doc

from tracecat.auth.models import UserRead
from tracecat.config import TRACECAT__MAX_ROWS_CLIENT_POSTGRES
from tracecat.cases.attachments import (
    CaseAttachmentCreate,
    CaseAttachmentDownloadData,
    CaseAttachmentRead,
)
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.models import (
    CaseCommentCreate,
    CaseCommentRead,
    CaseCommentUpdate,
    CaseCreate,
    CaseCustomFieldRead,
    CaseEventRead,
    CaseEventsWithUsers,
    CaseFieldRead,
    CaseRead,
    CaseReadMinimal,
    CaseUpdate,
)
from tracecat.cases.service import CasesService, CaseCommentsService
from tracecat.db.engine import get_async_session_context_manager
from tracecat.auth.users import lookup_user_by_email
from tracecat.tags.models import TagRead
from tracecat_registry import registry

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
    async with CasesService.with_session() as service:
        case = await service.create_case(
            CaseCreate(
                summary=summary,
                description=description,
                priority=CasePriority(priority),
                severity=CaseSeverity(severity),
                status=CaseStatus(status),
                fields=fields,
                payload=payload,
            )
        )

        # Add tags if provided
        if tags:
            for tag in tags:
                await service.tags.add_case_tag(case.id, tag)

            # Refresh case to include tags
            await service.session.refresh(case)

    return case.model_dump(mode="json")


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
) -> dict[str, Any]:
    async with CasesService.with_session() as service:
        case = await service.get_case(UUID(case_id))
        if not case:
            raise ValueError(f"Case with ID {case_id} not found")

        params: dict[str, Any] = {}
        if summary is not None:
            params["summary"] = summary
        if description is not None:
            params["description"] = description
        if priority is not None:
            params["priority"] = CasePriority(priority)
        if severity is not None:
            params["severity"] = CaseSeverity(severity)
        if status is not None:
            params["status"] = CaseStatus(status)
        if fields is not None:
            # Empty dict or None means fields are not updated
            # You must explicitly set fields to None to remove their values
            # If we don't pass fields, the service will not try to update the fields
            params["fields"] = fields
        if payload is not None:
            params["payload"] = payload
        updated_case = await service.update_case(case, CaseUpdate(**params))

        # Update tags if provided (replace all existing tags)
        if tags is not None:
            # Get current tags
            existing_tags = await service.tags.list_tags_for_case(case.id)

            # Remove all existing tags
            for existing_tag in existing_tags:
                await service.tags.remove_case_tag(case.id, existing_tag.ref)

            # Add new tags
            for tag in tags:
                await service.tags.add_case_tag(case.id, tag)

            # Refresh case to include updated tags
            await service.session.refresh(updated_case)

    return updated_case.model_dump(mode="json")


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
) -> dict[str, Any]:
    async with CasesService.with_session() as case_service:
        case = await case_service.get_case(UUID(case_id))
        if not case:
            raise ValueError(f"Case with ID {case_id} not found")

        async with CaseCommentsService.with_session() as comment_service:
            comment = await comment_service.create_comment(
                case,
                CaseCommentCreate(
                    content=content,
                    parent_id=UUID(parent_id) if parent_id else None,
                ),
            )
    return comment.model_dump(mode="json")


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
) -> dict[str, Any]:
    async with CaseCommentsService.with_session() as service:
        comment = await service.get_comment(UUID(comment_id))
        if not comment:
            raise ValueError(f"Comment with ID {comment_id} not found")

        params: dict[str, Any] = {}
        if content is not None:
            params["content"] = content
        if parent_id is not None:
            params["parent_id"] = UUID(parent_id)
        updated_comment = await service.update_comment(
            comment, CaseCommentUpdate(**params)
        )
    return updated_comment.model_dump(mode="json")


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
) -> dict[str, Any]:
    async with CasesService.with_session() as service:
        case = await service.get_case(UUID(case_id))
        if not case:
            raise ValueError(f"Case with ID {case_id} not found")

        fields = await service.fields.get_fields(case) or {}
        field_definitions = await service.fields.list_fields()

    final_fields = []
    for defn in field_definitions:
        f = CaseFieldRead.from_sa(defn)
        final_fields.append(
            CaseCustomFieldRead(
                id=f.id,
                type=f.type,
                description=f.description,
                nullable=f.nullable,
                default=f.default,
                reserved=f.reserved,
                value=fields.get(f.id),
            )
        )

    # Convert any UUID to string before serializing
    case_read = CaseRead(
        id=case.id,  # Use UUID directly
        short_id=f"CASE-{case.case_number:04d}",
        created_at=case.created_at,
        updated_at=case.updated_at,
        summary=case.summary,
        status=case.status,
        priority=case.priority,
        severity=case.severity,
        description=case.description,
        fields=final_fields,
        payload=case.payload,
    )

    # Use model_dump(mode="json") to ensure UUIDs are converted to strings
    return case_read.model_dump(mode="json")


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
    order_by: Annotated[
        Literal["created_at", "updated_at", "priority", "severity", "status"] | None,
        Doc("The field to order the cases by."),
    ] = None,
    sort: Annotated[
        Literal["asc", "desc"] | None,
        Doc("The direction to order the cases by."),
    ] = None,
) -> list[dict[str, Any]]:
    if limit > TRACECAT__MAX_ROWS_CLIENT_POSTGRES:
        raise ValueError(
            f"Limit cannot be greater than {TRACECAT__MAX_ROWS_CLIENT_POSTGRES}"
        )

    async with CasesService.with_session() as service:
        cases = await service.list_cases(limit=limit, order_by=order_by, sort=sort)
    return [
        CaseReadMinimal(
            id=case.id,
            created_at=case.created_at,
            updated_at=case.updated_at,
            short_id=f"CASE-{case.case_number:04d}",
            summary=case.summary,
            status=case.status,
            priority=case.priority,
            severity=case.severity,
        ).model_dump(mode="json")
        for case in cases
    ]


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
        StatusType | None,
        Doc("Filter by case status."),
    ] = None,
    priority: Annotated[
        PriorityType | None,
        Doc("Filter by case priority."),
    ] = None,
    severity: Annotated[
        SeverityType | None,
        Doc("Filter by case severity."),
    ] = None,
    start_time: Annotated[
        datetime | None,
        Doc("Filter cases created after this time."),
    ] = None,
    end_time: Annotated[
        datetime | None,
        Doc("Filter cases created before this time."),
    ] = None,
    updated_before: Annotated[
        datetime | None,
        Doc("Filter cases updated before this time."),
    ] = None,
    updated_after: Annotated[
        datetime | None,
        Doc("Filter cases updated after this time."),
    ] = None,
    order_by: Annotated[
        Literal["created_at", "updated_at", "priority", "severity", "status"] | None,
        Doc("The field to order the cases by."),
    ] = None,
    sort: Annotated[
        Literal["asc", "desc"] | None,
        Doc("The direction to order the cases by."),
    ] = None,
    tags: Annotated[
        list[str] | None,
        Doc("Filter by tag IDs or refs (AND logic)."),
    ] = None,
    limit: Annotated[
        int,
        Doc("Maximum number of cases to return."),
    ] = 100,
) -> list[dict[str, Any]]:
    if limit > TRACECAT__MAX_ROWS_CLIENT_POSTGRES:
        raise ValueError(
            f"Limit cannot be greater than {TRACECAT__MAX_ROWS_CLIENT_POSTGRES}"
        )

    async with CasesService.with_session() as service:
        tag_ids: list[UUID] = []
        if tags:
            for tag_identifier in tags:
                try:
                    tag = await service.tags.get_tag_by_ref_or_id(tag_identifier)
                except NoResultFound:
                    continue
                tag_ids.append(tag.id)

        try:
            cases = await service.search_cases(
                search_term=search_term,
                status=CaseStatus(status) if status else None,
                priority=CasePriority(priority) if priority else None,
                severity=CaseSeverity(severity) if severity else None,
                tag_ids=tag_ids or None,
                limit=limit,
                order_by=order_by,
                sort=sort,
                start_time=start_time,
                end_time=end_time,
                updated_before=updated_before,
                updated_after=updated_after,
            )
        except ProgrammingError as exc:
            raise ValueError(
                "Invalid filter parameters supplied for case search"
            ) from exc
    return [
        CaseReadMinimal(
            id=case.id,
            created_at=case.created_at,
            updated_at=case.updated_at,
            short_id=f"CASE-{case.case_number:04d}",
            summary=case.summary,
            status=case.status,
            priority=case.priority,
            severity=case.severity,
        ).model_dump(mode="json")
        for case in cases
    ]


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
    async with CasesService.with_session() as service:
        case = await service.get_case(UUID(case_id))
        if not case:
            raise ValueError(f"Case with ID {case_id} not found")
        await service.delete_case(case)


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
) -> dict[str, Any]:
    # Validate case_id format
    try:
        case_uuid = UUID(case_id)
    except ValueError:
        raise ValueError(f"Invalid case ID format: {case_id}")

    async with CasesService.with_session() as service:
        case = await service.get_case(case_uuid)
        if not case:
            raise ValueError(f"Case with ID {case_id} not found")

        events = await service.events.list_events(case)

    # Convert events to read models
    # Collect unique user IDs
    user_ids = {event.user_id for event in events if event.user_id}

    # Fetch users if needed
    users = []
    if user_ids:
        async with get_async_session_context_manager() as session:
            from tracecat.db.schemas import User

            stmt = select(User).where(col(User.id).in_(user_ids))
            result = await session.exec(stmt)
            users = [
                UserRead.model_validate(user, from_attributes=True)
                for user in result.all()
            ]

    return CaseEventsWithUsers(
        events=[
            CaseEventRead.model_validate(event, from_attributes=True)
            for event in events
        ],
        users=users,
    ).model_dump(mode="json")


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
) -> list[dict[str, Any]]:
    async with get_async_session_context_manager() as session:
        case_service = CasesService(session)
        case = await case_service.get_case(UUID(case_id))
        if not case:
            raise ValueError(f"Case with ID {case_id} not found")

        comments_service = CaseCommentsService(session)
        comment_user_pairs = await comments_service.list_comments(case)

    return [
        CaseCommentRead(
            id=comment.id,
            created_at=comment.created_at,
            updated_at=comment.updated_at,
            content=comment.content,
            parent_id=comment.parent_id,
            user=UserRead.model_validate(user, from_attributes=True) if user else None,
            last_edited_at=comment.last_edited_at,
        ).model_dump(mode="json")
        for comment, user in comment_user_pairs
    ]


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
) -> dict[str, Any]:
    async with CasesService.with_session() as service:
        case = await service.get_case(UUID(case_id))
        if not case:
            raise ValueError(f"Case with ID {case_id} not found")

        updated_case = await service.update_case(
            case, CaseUpdate(assignee_id=UUID(assignee_id))
        )
    return updated_case.model_dump(mode="json")


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
) -> dict[str, Any]:
    async with CasesService.with_session() as service:
        case = await service.get_case(UUID(case_id))
        if not case:
            raise ValueError(f"Case with ID {case_id} not found")

        # Look up user by email
        user = await lookup_user_by_email(session=service.session, email=assignee_email)
        if not user:
            raise ValueError(f"User with email {assignee_email} not found")

        # Update the case with the user's ID
        updated_case = await service.update_case(case, CaseUpdate(assignee_id=user.id))
    return updated_case.model_dump(mode="json")


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
) -> dict[str, Any]:
    async with CasesService.with_session() as service:
        case = await service.get_case(UUID(case_id))
        if not case:
            raise ValueError(f"Case with ID {case_id} not found")

        tag_obj = await service.tags.add_case_tag(case.id, tag)

    return TagRead.model_validate(tag_obj, from_attributes=True).model_dump(mode="json")


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
    async with CasesService.with_session() as service:
        case = await service.get_case(UUID(case_id))
        if not case:
            raise ValueError(f"Case with ID {case_id} not found")

        await service.tags.remove_case_tag(case.id, tag)


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
        str | None,
        Doc(
            "The MIME type of the file (e.g., 'application/pdf'). If not provided, defaults to 'application/octet-stream'."
        ),
    ] = None,
) -> dict[str, Any]:
    """Upload a file attachment to a case."""
    # Validate case_id format
    try:
        case_uuid = UUID(case_id)
    except ValueError:
        raise ValueError(f"Invalid case ID format: {case_id}")

    # Decode base64 content
    try:
        content = base64.b64decode(content_base64, validate=True)
    except Exception as e:
        raise ValueError(f"Invalid base64 encoding: {str(e)}")

    # Default content type if not provided
    if not content_type:
        content_type = "application/octet-stream"

    async with CasesService.with_session() as service:
        case = await service.get_case(case_uuid)
        if not case:
            raise ValueError(f"Case with ID {case_id} not found")

        attachment = await service.attachments.create_attachment(
            case=case,
            params=CaseAttachmentCreate(
                file_name=file_name,
                content_type=content_type,
                size=len(content),
                content=content,
            ),
        )
    return CaseAttachmentRead(
        id=attachment.id,
        case_id=attachment.case_id,
        file_id=attachment.file_id,
        file_name=attachment.file.name,
        content_type=attachment.file.content_type,
        size=attachment.file.size,
        sha256=attachment.file.sha256,
        created_at=attachment.created_at,
        updated_at=attachment.updated_at,
    ).model_dump(mode="json")


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
) -> list[dict[str, Any]]:
    """List all attachments for a case."""
    # Validate case_id format
    try:
        case_uuid = UUID(case_id)
    except ValueError:
        raise ValueError(f"Invalid case ID format: {case_id}")

    async with CasesService.with_session() as service:
        case = await service.get_case(case_uuid)
        if not case:
            raise ValueError(f"Case with ID {case_id} not found")
        attachments = await service.attachments.list_attachments(case)

    return [
        CaseAttachmentRead(
            id=attachment.id,
            case_id=attachment.case_id,
            file_id=attachment.file_id,
            file_name=attachment.file.name,
            content_type=attachment.file.content_type,
            size=attachment.file.size,
            sha256=attachment.file.sha256,
            created_at=attachment.created_at,
            updated_at=attachment.updated_at,
        ).model_dump(mode="json")
        for attachment in attachments
    ]


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
) -> dict[str, Any]:
    """Download an attachment's content.

    Returns the file content as base64 encoded string along with metadata.
    File integrity is automatically verified via SHA256 hash.
    """
    # Validate UUID formats
    try:
        case_uuid = UUID(case_id)
        attachment_uuid = UUID(attachment_id)
    except ValueError as e:
        raise ValueError(f"Invalid ID format: {str(e)}")

    async with CasesService.with_session() as service:
        case = await service.get_case(case_uuid)
        if not case:
            raise ValueError(f"Case with ID {case_id} not found")
        (
            content,
            file_name,
            content_type,
        ) = await service.attachments.download_attachment(
            case=case,
            attachment_id=attachment_uuid,
        )
    content_base64 = base64.b64encode(content).decode("utf-8")
    return CaseAttachmentDownloadData(
        content_base64=content_base64,
        file_name=file_name,
        content_type=content_type,
    ).model_dump(mode="json")


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
) -> dict[str, Any]:
    """Get attachment metadata without downloading the content."""
    # Validate UUID formats
    try:
        case_uuid = UUID(case_id)
        attachment_uuid = UUID(attachment_id)
    except ValueError as e:
        raise ValueError(f"Invalid ID format: {str(e)}")

    async with CasesService.with_session() as service:
        case = await service.get_case(case_uuid)
        if not case:
            raise ValueError(f"Case with ID {case_id} not found")

        attachment = await service.attachments.get_attachment(case, attachment_uuid)
        if not attachment:
            raise ValueError(f"Attachment {attachment_id} not found")

    return CaseAttachmentRead(
        id=attachment.id,
        case_id=attachment.case_id,
        file_id=attachment.file_id,
        file_name=attachment.file.name,
        content_type=attachment.file.content_type,
        size=attachment.file.size,
        sha256=attachment.file.sha256,
        created_at=attachment.created_at,
        updated_at=attachment.updated_at,
    ).model_dump(mode="json")


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
        raise ValueError(f"Invalid ID format: {str(e)}")

    async with CasesService.with_session() as service:
        case = await service.get_case(case_uuid)
        if not case:
            raise ValueError(f"Case with ID {case_id} not found")
        await service.attachments.delete_attachment(case, attachment_uuid)


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
        raise ValueError(f"Invalid ID format: {str(e)}")

    # Validate expiry if provided
    if expiry is not None:
        if expiry <= 0:
            raise ValueError("Expiry must be a positive number of seconds")
        if expiry > 86400:  # 24 hours
            raise ValueError("Expiry cannot exceed 24 hours (86400 seconds)")

    async with CasesService.with_session() as service:
        case = await service.get_case(case_uuid)
        if not case:
            raise ValueError(f"Case with ID {case_id} not found")

        download_url, _, _ = await service.attachments.get_attachment_download_url(
            case=case,
            attachment_id=attachment_uuid,
            expiry=expiry,
        )
    return download_url


@registry.register(
    default_title="List case fields",
    display_group="Cases",
    description="List all available case fields and their definitions.",
    namespace="core.cases",
)
async def list_case_fields() -> list[dict[str, Any]]:
    """List all case field definitions.

    Returns field metadata including name, type, description, and whether it's a reserved field.
    """
    async with CasesService.with_session() as service:
        field_definitions = await service.fields.list_fields()

    return [
        CaseFieldRead.from_sa(field_def).model_dump(mode="json")
        for field_def in field_definitions
    ]
