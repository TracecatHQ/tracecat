"""Forms SDK client for Tracecat API."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from tracecat_registry.sdk.client import TracecatClient


def _set(**values: Any) -> dict[str, Any]:
    """Drop unset values. httpx would send a ``None`` query value as empty."""
    return {key: value for key, value in values.items() if value is not None}


class FormsClient:
    """Client for Forms API operations.

    Form reads return ``workflow_id`` as a short ``wf_...`` ID. A missing form,
    submission, file or workflow raises ``TracecatNotFoundError``.
    """

    def __init__(self, client: TracecatClient) -> None:
        self._client = client

    async def list_forms(
        self, *, limit: int = 20, cursor: str | None = None
    ) -> dict[str, Any]:
        """List the workspace's forms, newest first, as a cursor page."""
        return await self._client.get("/forms", params=_set(limit=limit, cursor=cursor))

    async def get_form(self, form_id: str) -> dict[str, Any]:
        """Get a form's status, config, issues, URL and counts."""
        return await self._client.get(f"/forms/{form_id}")

    async def update_form(
        self,
        *,
        workflow_id: str,
        status: Literal["online", "offline"] | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update a workflow's form, creating it if the workflow has none.

        ``workflow_id`` takes a short ``wf_...`` or full ID. Unset arguments
        leave the stored value unchanged. Raises ``TracecatValidationError`` if
        the config has errors, and ``TracecatConflictError`` if the form goes
        online on an unpublished workflow.
        """
        return await self._client.put(
            f"/forms/workflows/{workflow_id}", json=_set(status=status, config=config)
        )

    async def list_submissions(
        self,
        form_id: str,
        *,
        limit: int = 20,
        cursor: str | None = None,
        submitted_after: datetime | None = None,
        submitted_before: datetime | None = None,
    ) -> dict[str, Any]:
        """List a form's submissions with their answers, newest first."""
        params = _set(
            limit=limit,
            cursor=cursor,
            submitted_after=submitted_after and submitted_after.isoformat(),
            submitted_before=submitted_before and submitted_before.isoformat(),
        )
        return await self._client.get(f"/forms/{form_id}/submissions", params=params)

    async def get_submission(self, submission_id: str) -> dict[str, Any]:
        """Get one submission with its answers."""
        return await self._client.get(f"/forms/submissions/{submission_id}")

    async def delete_submission(self, submission_id: str) -> None:
        """Delete a submission. The workflow run it started is not affected."""
        await self._client.delete(f"/forms/submissions/{submission_id}")

    async def download_file(self, file_id: str) -> dict[str, Any]:
        """Download a submitted file as its metadata plus ``content_base64``."""
        return await self._client.get(f"/forms/files/{file_id}/download")
