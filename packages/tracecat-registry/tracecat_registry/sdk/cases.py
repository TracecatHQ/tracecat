"""Cases SDK client for Tracecat API."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

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


class CasesClient:
    """Client for Cases API operations."""

    def __init__(self, client: TracecatClient) -> None:
        self._client = client

    # === Case CRUD === #

    async def create_case(
        self,
        *,
        summary: str,
        status: CaseStatus = "new",
        priority: CasePriority = "medium",
        severity: CaseSeverity = "medium",
        description: str | None | Unset = UNSET,
        assignee_id: str | None | Unset = UNSET,
        payload: dict[str, Any] | None | Unset = UNSET,
        tags: list[str] | None | Unset = UNSET,
        fields: dict[str, Any] | None | Unset = UNSET,
    ) -> dict[str, Any]:
        """Create a new case.

        Args:
            summary: Case summary/title.
            status: Case status.
            priority: Case priority.
            severity: Case severity.
            description: Detailed case description.
            assignee_id: User ID to assign the case to.
            payload: Additional JSON payload.
            tags: List of tag names or IDs to attach.
            fields: Custom field values.

        Returns:
            Created case data.
        """
        data: dict[str, Any] = {
            "summary": summary,
            "status": status,
            "priority": priority,
            "severity": severity,
        }
        if is_set(description):
            data["description"] = description
        if is_set(assignee_id):
            data["assignee_id"] = assignee_id
        if is_set(payload):
            data["payload"] = payload
        if is_set(tags):
            data["tags"] = tags
        if is_set(fields):
            data["fields"] = fields

        return await self._client.post("/cases", json=data)

    async def get_case(self, case_id: str) -> dict[str, Any]:
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
        summary: str | Unset = UNSET,
        status: CaseStatus | Unset = UNSET,
        priority: CasePriority | Unset = UNSET,
        severity: CaseSeverity | Unset = UNSET,
        description: str | None | Unset = UNSET,
        assignee_id: str | None | Unset = UNSET,
        payload: dict[str, Any] | None | Unset = UNSET,
        fields: dict[str, Any] | None | Unset = UNSET,
        tags: list[str] | None | Unset = UNSET,
    ) -> dict[str, Any]:
        """Update a case.

        Args:
            case_id: The case UUID.
            summary: New summary.
            status: New status.
            priority: New priority.
            severity: New severity.
            description: New description. Pass None to clear.
            assignee_id: New assignee user ID. Pass None to unassign.
            payload: New payload (merged with existing). Pass None to clear.
            fields: Custom field values to update.
        """
        data: dict[str, Any] = {}
        if is_set(summary):
            data["summary"] = summary
        if is_set(status):
            data["status"] = status
        if is_set(priority):
            data["priority"] = priority
        if is_set(severity):
            data["severity"] = severity
        if is_set(description):
            data["description"] = description
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
        cursor: str | Unset = UNSET,
        search_term: str | Unset = UNSET,
        status: list[CaseStatus] | Unset = UNSET,
        priority: list[CasePriority] | Unset = UNSET,
        severity: list[CaseSeverity] | Unset = UNSET,
        assignee_id: list[str] | Unset = UNSET,
        tags: list[str] | Unset = UNSET,
        order_by: str | Unset = UNSET,
        sort: Literal["asc", "desc"] | Unset = UNSET,
    ) -> dict[str, Any]:
        """List cases with filtering and pagination.

        Args:
            limit: Maximum items per page.
            cursor: Pagination cursor.
            search_term: Text to search in summary/description.
            status: Filter by status(es).
            priority: Filter by priority(ies).
            severity: Filter by severity(ies).
            assignee_id: Filter by assignee ID(s).
            tags: Filter by tag names/IDs.
            order_by: Column to order by.
            sort: Sort direction.

        Returns:
            Paginated list of cases.
        """
        params: dict[str, Any] = {"limit": limit}
        if is_set(cursor):
            params["cursor"] = cursor
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
        if is_set(order_by):
            params["order_by"] = order_by
        if is_set(sort):
            params["sort"] = sort

        return await self._client.get("/cases", params=params)

    async def search_cases(
        self,
        *,
        search_term: str | Unset = UNSET,
        status: list[CaseStatus] | Unset = UNSET,
        priority: list[CasePriority] | Unset = UNSET,
        severity: list[CaseSeverity] | Unset = UNSET,
        tags: list[str] | Unset = UNSET,
        limit: int | Unset = UNSET,
        order_by: str | Unset = UNSET,
        sort: Literal["asc", "desc"] | Unset = UNSET,
        start_time: str | Unset = UNSET,
        end_time: str | Unset = UNSET,
        updated_after: str | Unset = UNSET,
        updated_before: str | Unset = UNSET,
    ) -> list[dict[str, Any]]:
        """Search cases with filtering.

        Args:
            search_term: Text to search.
            status: Filter by status(es).
            priority: Filter by priority(ies).
            severity: Filter by severity(ies).
            tags: Filter by tag names/IDs.
            limit: Maximum results.
            order_by: Column to order by.
            sort: Sort direction.
            start_time: Cases created after (ISO format).
            end_time: Cases created before (ISO format).
            updated_after: Cases updated after (ISO format).
            updated_before: Cases updated before (ISO format).

        Returns:
            List of matching cases.
        """
        params: dict[str, Any] = {}
        if is_set(search_term):
            params["search_term"] = search_term
        if is_set(status):
            params["status"] = status
        if is_set(priority):
            params["priority"] = priority
        if is_set(severity):
            params["severity"] = severity
        if is_set(tags):
            params["tags"] = tags
        if is_set(limit):
            params["limit"] = limit
        if is_set(order_by):
            params["order_by"] = order_by
        if is_set(sort):
            params["sort"] = sort
        if is_set(start_time):
            params["start_time"] = start_time
        if is_set(end_time):
            params["end_time"] = end_time
        if is_set(updated_after):
            params["updated_after"] = updated_after
        if is_set(updated_before):
            params["updated_before"] = updated_before

        return await self._client.get("/cases/search", params=params)

    # === Comments === #

    async def list_comments(self, case_id: str) -> list[dict[str, Any]]:
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
    ) -> dict[str, Any]:
        """Create a comment on a case.

        Args:
            case_id: The case UUID.
            content: Comment content.

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
    ) -> None:
        """Update a comment.

        Args:
            case_id: The case UUID.
            comment_id: The comment UUID.
            content: New content.
        """
        await self._client.patch(
            f"/cases/{case_id}/comments/{comment_id}",
            json={"content": content},
        )

    async def delete_comment(self, case_id: str, comment_id: str) -> None:
        """Delete a comment.

        Args:
            case_id: The case UUID.
            comment_id: The comment UUID.
        """
        await self._client.delete(f"/cases/{case_id}/comments/{comment_id}")

    # === Tags === #

    async def list_tags(self, case_id: str) -> list[dict[str, Any]]:
        """List tags on a case.

        Args:
            case_id: The case UUID.

        Returns:
            List of tags.
        """
        return await self._client.get(f"/cases/{case_id}/tags")

    async def add_tag(self, case_id: str, *, tag_id: str) -> None:
        """Add a tag to a case.

        Args:
            case_id: The case UUID.
            tag_id: The tag UUID or name.
        """
        await self._client.post(f"/cases/{case_id}/tags", json={"tag_id": tag_id})

    async def remove_tag(self, case_id: str, *, tag_id: str) -> None:
        """Remove a tag from a case.

        Args:
            case_id: The case UUID.
            tag_id: The tag UUID or name.
        """
        await self._client.delete(f"/cases/{case_id}/tags/{tag_id}")

    async def create_tag(self, *, name: str) -> dict[str, Any]:
        """Create a new case tag.

        Args:
            name: Tag name.

        Returns:
            Created tag data.
        """
        return await self._client.post("/case-tags", json={"name": name})

    # === Attachments === #

    async def list_attachments(self, case_id: str) -> list[dict[str, Any]]:
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
    ) -> dict[str, Any]:
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
    ) -> dict[str, Any]:
        """Get attachment download info.

        Args:
            case_id: The case UUID.
            attachment_id: The attachment UUID.

        Returns:
            Attachment data with download URL.
        """
        return await self._client.get(f"/cases/{case_id}/attachments/{attachment_id}")

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
        params = {"expiry": expiry} if expiry is not None else None
        attachment = await self._client.get(
            f"/cases/{case_id}/attachments/{attachment_id}",
            params=params,
        )
        if not isinstance(attachment, dict):
            raise ValueError("Unexpected attachment response type")
        download_url = attachment.get("download_url")
        if not isinstance(download_url, str) or not download_url:
            raise ValueError("Attachment response missing download_url")
        return download_url

    async def download_attachment(
        self,
        case_id: str,
        attachment_id: str,
    ) -> dict[str, Any]:
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

    async def delete_attachment(self, case_id: str, attachment_id: str) -> None:
        """Delete an attachment.

        Args:
            case_id: The case UUID.
            attachment_id: The attachment UUID.
        """
        await self._client.delete(f"/cases/{case_id}/attachments/{attachment_id}")

    # === Events === #

    async def list_events(self, case_id: str) -> dict[str, Any]:
        """List events/activity for a case.

        Args:
            case_id: The case UUID.

        Returns:
            Case events with user info.
        """
        return await self._client.get(f"/cases/{case_id}/events")
