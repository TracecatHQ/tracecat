"""Cases SDK client for Tracecat API."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal
from uuid import UUID

from tracecat_registry import types
from tracecat_registry.sdk.types import (
    UNSET,
    CasePriority,
    CaseSeverity,
    CaseStatus,
    Unset,
    is_set,
)

if TYPE_CHECKING:
    from tracecat_registry.sdk.client import TracecatClient
    from tracecat_ee.cases.types import CaseDurationMetric


class CasesClient:
    """Client for Cases API operations."""

    def __init__(self, client: TracecatClient) -> None:
        self._client = client

    # === Case CRUD === #

    async def create_case(
        self,
        *,
        summary: str,
        description: str,
        status: CaseStatus = "new",
        priority: CasePriority = "medium",
        severity: CaseSeverity = "medium",
        assignee_id: str | None | Unset = UNSET,
        payload: dict[str, Any] | None | Unset = UNSET,
        tags: list[str] | None | Unset = UNSET,
        fields: dict[str, Any] | None | Unset = UNSET,
    ) -> types.CaseRead:
        """Create a new case.

        Args:
            summary: Case summary/title.
            description: Case description.
            status: Case status.
            priority: Case priority.
            severity: Case severity.
            assignee_id: User ID to assign the case to.
            payload: Additional JSON payload.
            tags: List of tag names or IDs to attach.
            fields: Custom field values.

        Returns:
            Created case data.
        """
        data: dict[str, Any] = {
            "summary": summary,
            "description": description,
            "status": status,
            "priority": priority,
            "severity": severity,
        }
        if is_set(assignee_id):
            data["assignee_id"] = assignee_id
        if is_set(payload):
            data["payload"] = payload
        if is_set(tags):
            data["tags"] = tags
        if is_set(fields):
            data["fields"] = fields

        return await self._client.post("/cases", json=data)

    async def get_case(self, case_id: str) -> types.CaseRead:
        """Get a case by ID.

        Args:
            case_id: The case UUID.

        Returns:
            Case data.
        """
        return await self._client.get(f"/cases/{case_id}")

    async def update_case(
        self,
        case_id: str,
        *,
        summary: str | Unset = UNSET,
        description: str | None | Unset = UNSET,
        status: CaseStatus | Unset = UNSET,
        priority: CasePriority | Unset = UNSET,
        severity: CaseSeverity | Unset = UNSET,
        assignee_id: str | None | Unset = UNSET,
        payload: dict[str, Any] | None | Unset = UNSET,
        fields: dict[str, Any] | None | Unset = UNSET,
        tags: list[str] | None | Unset = UNSET,
    ) -> types.CaseRead:
        """Update a case.

        Args:
            case_id: The case UUID.
            summary: New summary.
            description: New description. Pass None to clear.
            status: New status.
            priority: New priority.
            severity: New severity.
            assignee_id: New assignee user ID. Pass None to unassign.
            payload: New payload (merged with existing). Pass None to clear.
            fields: Custom field values to update.
            tags: List of tag IDs or refs to set (replaces existing).

        Returns:
            Updated case data.
        """
        data: dict[str, Any] = {}
        if is_set(summary):
            data["summary"] = summary
        if is_set(description):
            data["description"] = description
        if is_set(status):
            data["status"] = status
        if is_set(priority):
            data["priority"] = priority
        if is_set(severity):
            data["severity"] = severity
        if is_set(assignee_id):
            data["assignee_id"] = assignee_id
        if is_set(payload):
            data["payload"] = payload
        if is_set(fields):
            data["fields"] = fields
        if is_set(tags):
            data["tags"] = tags

        return await self._client.patch(f"/cases/{case_id}", json=data)

    async def delete_case(self, case_id: str) -> None:
        """Delete a case.

        Args:
            case_id: The case UUID.
        """
        await self._client.delete(f"/cases/{case_id}")

    async def list_cases(
        self,
        *,
        limit: int = 20,
        order_by: str | Unset = UNSET,
        sort: Literal["asc", "desc"] | Unset = UNSET,
    ) -> types.CaseListResponse:
        """List cases using default server-side filtering.

        Args:
            limit: Maximum items per page.
            order_by: Column to order by.
            sort: Sort direction.

        Returns:
            Paginated list of cases with cursor metadata.
        """
        params: dict[str, Any] = {"limit": limit}
        if is_set(order_by):
            params["order_by"] = order_by
        if is_set(sort):
            params["sort"] = sort

        return await self._client.get("/cases", params=params)

    async def search_cases(
        self,
        *,
        limit: int = 20,
        cursor: str | Unset = UNSET,
        reverse: bool | Unset = UNSET,
        search_term: str | Unset = UNSET,
        status: list[CaseStatus] | Unset = UNSET,
        priority: list[CasePriority] | Unset = UNSET,
        severity: list[CaseSeverity] | Unset = UNSET,
        assignee_id: list[str] | Unset = UNSET,
        tags: list[str] | Unset = UNSET,
        dropdown: list[str] | Unset = UNSET,
        order_by: str | Unset = UNSET,
        sort: Literal["asc", "desc"] | Unset = UNSET,
        start_time: datetime | str | Unset = UNSET,
        end_time: datetime | str | Unset = UNSET,
        updated_after: datetime | str | Unset = UNSET,
        updated_before: datetime | str | Unset = UNSET,
    ) -> types.CaseListResponse:
        """Search cases with filtering and pagination."""
        params: dict[str, Any] = {"limit": limit}
        if is_set(cursor):
            params["cursor"] = cursor
        if is_set(reverse):
            params["reverse"] = reverse
        if is_set(search_term):
            params["search_term"] = search_term
        if is_set(status):
            params["status"] = status
        if is_set(priority):
            params["priority"] = priority
        if is_set(severity):
            params["severity"] = severity
        if is_set(assignee_id):
            params["assignee_id"] = assignee_id
        if is_set(tags):
            params["tags"] = tags
        if is_set(dropdown):
            params["dropdown"] = dropdown
        if is_set(order_by):
            params["order_by"] = order_by
        if is_set(sort):
            params["sort"] = sort
        if is_set(start_time):
            params["start_time"] = (
                start_time.isoformat()
                if isinstance(start_time, datetime)
                else start_time
            )
        if is_set(end_time):
            params["end_time"] = (
                end_time.isoformat() if isinstance(end_time, datetime) else end_time
            )
        if is_set(updated_after):
            params["updated_after"] = (
                updated_after.isoformat()
                if isinstance(updated_after, datetime)
                else updated_after
            )
        if is_set(updated_before):
            params["updated_before"] = (
                updated_before.isoformat()
                if isinstance(updated_before, datetime)
                else updated_before
            )

        return await self._client.get("/cases/search", params=params)

    # === Comments === #

    async def list_comments(self, case_id: str) -> list[types.CaseCommentRead]:
        """List comments on a case.

        Args:
            case_id: The case UUID.

        Returns:
            List of comments.
        """
        return await self._client.get(f"/cases/{case_id}/comments")

    async def create_comment(
        self,
        case_id: str,
        *,
        content: str,
        parent_id: str | Unset = UNSET,
    ) -> types.CaseCommentRead:
        """Create a comment on a case.

        Args:
            case_id: The case UUID.
            content: Comment content.
            parent_id: Parent comment ID for replies.

        Returns:
            Created comment data.
        """
        data: dict[str, Any] = {
            "content": content,
        }
        if is_set(parent_id):
            data["parent_id"] = parent_id
        return await self._client.post(
            f"/cases/{case_id}/comments",
            json=data,
        )

    async def update_comment(
        self,
        case_id: str,
        comment_id: str,
        *,
        content: str,
    ) -> types.CaseCommentRead:
        """Update a comment.

        Args:
            case_id: The case UUID.
            comment_id: The comment UUID.
            content: New content.

        Returns:
            Updated comment data.
        """
        return await self._client.patch(
            f"/cases/{case_id}/comments/{comment_id}",
            json={"content": content},
        )

    async def update_comment_by_id(
        self,
        comment_id: str,
        *,
        content: str | Unset = UNSET,
        parent_id: str | Unset = UNSET,
    ) -> types.CaseCommentRead:
        """Update a comment by ID without requiring case_id.

        Args:
            comment_id: The comment UUID.
            content: New content.
            parent_id: New parent comment ID.

        Returns:
            Updated comment data.
        """
        data: dict[str, Any] = {}
        if is_set(content):
            data["content"] = content
        if is_set(parent_id):
            data["parent_id"] = parent_id
        return await self._client.patch(
            f"/comments/{comment_id}",
            json=data,
        )

    async def delete_comment(self, case_id: str, comment_id: str) -> None:
        """Delete a comment.

        Args:
            case_id: The case UUID.
            comment_id: The comment UUID.
        """
        await self._client.delete(f"/cases/{case_id}/comments/{comment_id}")

    # === Tags === #

    async def list_tags(self, case_id: str) -> list[types.TagRead]:
        """List tags on a case.

        Args:
            case_id: The case UUID.

        Returns:
            List of tags.
        """
        return await self._client.get(f"/cases/{case_id}/tags")

    async def add_tag(
        self,
        case_id: str,
        *,
        tag_id: str,
        create_if_missing: bool = False,
    ) -> types.TagRead:
        """Add a tag to a case.

        Args:
            case_id: The case UUID.
            tag_id: The tag UUID or ref.
            create_if_missing: If True, create the tag if not found.

        Returns:
            The added tag data.
        """
        return await self._client.post(
            f"/cases/{case_id}/tags",
            json={"tag_id": tag_id, "create_if_missing": create_if_missing},
        )

    async def remove_tag(self, case_id: str, *, tag_id: str) -> None:
        """Remove a tag from a case.

        Args:
            case_id: The case UUID.
            tag_id: The tag UUID or ref.
        """
        await self._client.delete(f"/cases/{case_id}/tags/{tag_id}")

    # === Attachments === #

    async def list_attachments(self, case_id: str) -> list[types.CaseAttachmentRead]:
        """List attachments on a case.

        Args:
            case_id: The case UUID.

        Returns:
            List of attachments.
        """
        return await self._client.get(f"/cases/{case_id}/attachments")

    async def create_attachment(
        self,
        case_id: str,
        *,
        filename: str,
        content_base64: str,
        content_type: str = "application/octet-stream",
    ) -> types.CaseAttachmentRead:
        """Create an attachment on a case.

        Args:
            case_id: The case UUID.
            filename: Filename for the attachment.
            content_base64: Base64-encoded file content.
            content_type: MIME type of the file.

        Returns:
            Created attachment data.
        """
        return await self._client.post(
            f"/cases/{case_id}/attachments",
            json={
                "filename": filename,
                "content_base64": content_base64,
                "content_type": content_type,
            },
        )

    async def get_attachment(
        self,
        case_id: str,
        attachment_id: str,
        *,
        expiry: int | None = None,
    ) -> types.CaseAttachmentDownloadResponse:
        """Get attachment download info.

        Args:
            case_id: The case UUID.
            attachment_id: The attachment UUID.
            expiry: Optional URL expiry time in seconds.

        Returns:
            Attachment data with download URL.
        """
        params = {"expiry": expiry} if expiry is not None else None
        return await self._client.get(
            f"/cases/{case_id}/attachments/{attachment_id}",
            params=params,
        )

    async def get_attachment_download_url(
        self,
        case_id: str,
        attachment_id: str,
        *,
        expiry: int | None = None,
    ) -> str:
        """Get a presigned download URL for an attachment.

        Args:
            case_id: The case UUID.
            attachment_id: The attachment UUID.
            expiry: Optional URL expiry time in seconds.

        Returns:
            Presigned download URL.
        """
        attachment = await self.get_attachment(case_id, attachment_id, expiry=expiry)
        if not isinstance(attachment, dict):
            raise ValueError("Unexpected attachment response type")
        download_url = attachment.get("download_url")
        if not isinstance(download_url, str) or not download_url:
            raise ValueError("Attachment response missing download_url")
        return download_url

    async def download_attachment(
        self,
        case_id: UUID,
        attachment_id: UUID,
    ) -> types.CaseAttachmentDownloadData:
        """Download attachment content.

        Args:
            case_id: The case UUID.
            attachment_id: The attachment UUID.

        Returns:
            Attachment data with base64 content.
        """
        return await self._client.get(
            f"/cases/{case_id}/attachments/{attachment_id}/download"
        )

    async def delete_attachment(self, case_id: UUID, attachment_id: UUID) -> None:
        """Delete an attachment.

        Args:
            case_id: The case UUID.
            attachment_id: The attachment UUID.
        """
        await self._client.delete(f"/cases/{case_id}/attachments/{attachment_id}")

    # === Events === #

    async def list_events(self, case_id: str) -> types.CaseEventsWithUsers:
        """List events/activity for a case.

        Args:
            case_id: The case UUID.

        Returns:
            Case events with user info.
        """
        return await self._client.get(f"/cases/{case_id}/events")

    # === User Assignment === #

    async def assign_user(
        self,
        case_id: str,
        *,
        assignee_id: str,
    ) -> types.CaseRead:
        """Assign a user to a case by user ID.

        Args:
            case_id: The case UUID.
            assignee_id: The user UUID to assign.

        Returns:
            Updated case data.
        """
        return await self.update_case(case_id, assignee_id=assignee_id)

    async def assign_user_by_email(
        self,
        case_id: str,
        *,
        email: str,
    ) -> types.Case:
        """Assign a user to a case by email.

        Args:
            case_id: The case UUID.
            email: The user's email address.

        Returns:
            Updated case data (CaseDict format).
        """
        return await self._client.post(
            f"/cases/{case_id}/assign-by-email",
            json={"assignee_email": email},
        )

    # === Simple/UDF-compatible methods === #

    async def create_case_simple(
        self,
        *,
        summary: str,
        description: str,
        status: CaseStatus = "new",
        priority: CasePriority = "medium",
        severity: CaseSeverity = "medium",
        assignee_id: str | None | Unset = UNSET,
        payload: dict[str, Any] | None | Unset = UNSET,
        tags: list[str] | None | Unset = UNSET,
        fields: dict[str, Any] | None | Unset = UNSET,
    ) -> types.Case:
        """Create a new case and return simple dict format.

        This uses the UDF-compatible /cases/simple endpoint.

        Args:
            summary: Case summary/title.
            description: Case description.
            status: Case status.
            priority: Case priority.
            severity: Case severity.
            assignee_id: User ID to assign the case to.
            payload: Additional JSON payload.
            tags: List of tag names or IDs to attach.
            fields: Custom field values.

        Returns:
            Created case data (CaseDict format).
        """
        data: dict[str, Any] = {
            "summary": summary,
            "description": description,
            "status": status,
            "priority": priority,
            "severity": severity,
        }
        if is_set(assignee_id):
            data["assignee_id"] = assignee_id
        if is_set(payload):
            data["payload"] = payload
        if is_set(tags):
            data["tags"] = tags
        if is_set(fields):
            data["fields"] = fields

        return await self._client.post("/cases/simple", json=data)

    async def update_case_simple(
        self,
        case_id: str,
        *,
        summary: str | Unset = UNSET,
        description: str | None | Unset = UNSET,
        status: CaseStatus | Unset = UNSET,
        priority: CasePriority | Unset = UNSET,
        severity: CaseSeverity | Unset = UNSET,
        assignee_id: str | None | Unset = UNSET,
        payload: dict[str, Any] | None | Unset = UNSET,
        fields: dict[str, Any] | None | Unset = UNSET,
        tags: list[str] | None | Unset = UNSET,
        append_description: bool = False,
    ) -> types.Case:
        """Update a case and return simple dict format.

        This uses the UDF-compatible /cases/{case_id}/simple endpoint.

        Args:
            case_id: The case UUID.
            summary: New summary.
            description: New description. Pass None to clear.
            status: New status.
            priority: New priority.
            severity: New severity.
            assignee_id: New assignee user ID. Pass None to unassign.
            payload: New payload (merged with existing). Pass None to clear.
            fields: Custom field values to update.
            tags: List of tag IDs or refs to set (replaces existing).
            append_description: If True, append description to existing.

        Returns:
            Updated case data (CaseDict format).
        """
        data: dict[str, Any] = {}
        if is_set(summary):
            data["summary"] = summary
        if is_set(description):
            data["description"] = description
        if is_set(status):
            data["status"] = status
        if is_set(priority):
            data["priority"] = priority
        if is_set(severity):
            data["severity"] = severity
        if is_set(assignee_id):
            data["assignee_id"] = assignee_id
        if is_set(payload):
            data["payload"] = payload
        if is_set(fields):
            data["fields"] = fields
        if is_set(tags):
            data["tags"] = tags
        if append_description:
            data["append_description"] = append_description

        return await self._client.patch(f"/cases/{case_id}/simple", json=data)

    async def create_comment_simple(
        self,
        case_id: str,
        *,
        content: str,
        parent_id: str | Unset = UNSET,
    ) -> types.CaseComment:
        """Create a comment on a case and return simple dict format.

        This uses the UDF-compatible /cases/{case_id}/comments/simple endpoint.

        Args:
            case_id: The case UUID.
            content: Comment content.
            parent_id: Parent comment ID for replies.

        Returns:
            Created comment data (CaseCommentDict format).
        """
        data: dict[str, Any] = {"content": content}
        if is_set(parent_id):
            data["parent_id"] = parent_id
        return await self._client.post(
            f"/cases/{case_id}/comments/simple",
            json=data,
        )

    async def update_comment_simple(
        self,
        comment_id: str,
        *,
        content: str | Unset = UNSET,
        parent_id: str | Unset = UNSET,
    ) -> types.CaseComment:
        """Update a comment and return simple dict format.

        This uses the UDF-compatible /comments/{comment_id}/simple endpoint.

        Args:
            comment_id: The comment UUID.
            content: New content.
            parent_id: New parent comment ID.

        Returns:
            Updated comment data (CaseCommentDict format).
        """
        data: dict[str, Any] = {}
        if is_set(content):
            data["content"] = content
        if is_set(parent_id):
            data["parent_id"] = parent_id
        return await self._client.patch(
            f"/comments/{comment_id}/simple",
            json=data,
        )

    async def assign_user_simple(
        self,
        case_id: str,
        *,
        assignee_id: str,
    ) -> types.Case:
        """Assign a user to a case by user ID.

        This uses the UDF-compatible /cases/{case_id}/assign endpoint.

        Args:
            case_id: The case UUID.
            assignee_id: The user UUID to assign.

        Returns:
            Updated case data (CaseDict format).
        """
        return await self._client.post(
            f"/cases/{case_id}/assign",
            params={"assignee_id": assignee_id},
        )

    async def get_attachment_metadata(
        self,
        case_id: UUID,
        attachment_id: UUID,
    ) -> types.CaseAttachmentRead:
        """Get attachment metadata without download URL.

        Args:
            case_id: The case UUID.
            attachment_id: The attachment UUID.

        Returns:
            Attachment metadata.
        """
        return await self._client.get(
            f"/cases/{case_id}/attachments/{attachment_id}/metadata"
        )

    async def get_attachment_presigned_url(
        self,
        case_id: UUID,
        attachment_id: UUID,
        *,
        expiry: int | None = None,
    ) -> str:
        """Get a presigned download URL for an attachment.

        Args:
            case_id: The case UUID.
            attachment_id: The attachment UUID.
            expiry: Optional URL expiry time in seconds.

        Returns:
            Presigned download URL.
        """
        params = {"expiry": expiry} if expiry is not None else None
        return await self._client.get(
            f"/cases/{case_id}/attachments/{attachment_id}/url",
            params=params,
        )

    # === Case Metrics (Enterprise) === #

    async def get_case_metrics(
        self,
        case_ids: list[str],
    ) -> list["CaseDurationMetric"]:
        """Get case metrics as time-series for the provided case IDs.

        Args:
            case_ids: List of case UUIDs.

        Returns:
            List of time-series metrics.
        """
        return await self._client.post(
            "/cases/metrics",
            json={"case_ids": case_ids},
        )

    # === Case Tasks (Enterprise) === #

    async def create_task(
        self,
        case_id: str,
        *,
        title: str,
        description: str | None | Unset = UNSET,
        priority: str = "unknown",
        status: str = "todo",
        assignee_id: str | None | Unset = UNSET,
        workflow_id: str | None | Unset = UNSET,
        default_trigger_values: dict[str, Any] | None | Unset = UNSET,
    ) -> types.CaseTaskRead:
        """Create a new task for a case.

        Args:
            case_id: The case UUID.
            title: Task title.
            description: Task description.
            priority: Task priority (unknown, low, medium, high, critical).
            status: Task status (todo, in_progress, blocked, completed).
            assignee_id: User ID to assign the task to.
            workflow_id: Associated workflow ID.
            default_trigger_values: Default trigger values for the task.

        Returns:
            Created task data.
        """
        data: dict[str, Any] = {
            "title": title,
            "priority": priority,
            "status": status,
        }
        if is_set(description):
            data["description"] = description
        if is_set(assignee_id):
            data["assignee_id"] = assignee_id
        if is_set(workflow_id):
            data["workflow_id"] = workflow_id
        if is_set(default_trigger_values):
            data["default_trigger_values"] = default_trigger_values

        return await self._client.post(f"/cases/{case_id}/tasks", json=data)

    async def get_task(self, task_id: str) -> types.CaseTaskRead:
        """Get a specific case task by ID.

        Args:
            task_id: The task UUID.

        Returns:
            Task data.
        """
        return await self._client.get(f"/cases/tasks/{task_id}")

    async def list_tasks(self, case_id: str) -> list[types.CaseTaskRead]:
        """List all tasks for a case.

        Args:
            case_id: The case UUID.

        Returns:
            List of tasks.
        """
        return await self._client.get(f"/cases/{case_id}/tasks")

    async def update_task(
        self,
        task_id: str,
        *,
        title: str | Unset = UNSET,
        description: str | None | Unset = UNSET,
        priority: str | Unset = UNSET,
        status: str | Unset = UNSET,
        assignee_id: str | None | Unset = UNSET,
        workflow_id: str | None | Unset = UNSET,
        default_trigger_values: dict[str, Any] | None | Unset = UNSET,
    ) -> types.CaseTaskRead:
        """Update an existing case task.

        Args:
            task_id: The task UUID.
            title: Updated title.
            description: Updated description. Pass None to clear.
            priority: Updated priority.
            status: Updated status.
            assignee_id: Updated assignee ID. Pass None to unassign.
            workflow_id: Updated workflow ID. Pass None to clear.
            default_trigger_values: Updated default trigger values. Pass None to clear.

        Returns:
            Updated task data.
        """
        data: dict[str, Any] = {}
        if is_set(title):
            data["title"] = title
        if is_set(description):
            data["description"] = description
        if is_set(priority):
            data["priority"] = priority
        if is_set(status):
            data["status"] = status
        if is_set(assignee_id):
            data["assignee_id"] = assignee_id
        if is_set(workflow_id):
            data["workflow_id"] = workflow_id
        if is_set(default_trigger_values):
            data["default_trigger_values"] = default_trigger_values

        return await self._client.patch(f"/cases/tasks/{task_id}", json=data)

    async def delete_task(self, task_id: str) -> None:
        """Delete a case task.

        Args:
            task_id: The task UUID.
        """
        await self._client.delete(f"/cases/tasks/{task_id}")
