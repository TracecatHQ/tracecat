"""Forms SDK client for Tracecat API."""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from tracecat_registry.sdk.client import TracecatClient


def _page_params(limit: int, cursor: str | None) -> dict[str, Any]:
    params: dict[str, Any] = {"limit": limit}
    if cursor is not None:
        params["cursor"] = cursor
    return params


class FormsClient:
    """Client for Forms API operations."""

    def __init__(self, client: TracecatClient) -> None:
        self._client = client

    async def list_forms(
        self, *, limit: int = 20, cursor: str | None = None
    ) -> dict[str, Any]:
        """List the workspace's forms, newest first.

        Args:
            limit: Maximum number of forms to return.
            cursor: Opaque ``next_cursor`` from a previous page.

        Returns:
            dict with ``items`` (each with ``id``, ``workflow_id``,
            ``workflow_title``, ``status``, ``title``, ``description``,
            ``submission_count``, ``last_submitted_at``), plus ``next_cursor``,
            ``prev_cursor``, ``has_more`` and ``has_previous``.
        """
        return await self._client.get("/forms", params=_page_params(limit, cursor))

    async def get_form(self, form_id: str) -> dict[str, Any]:
        """Get a form's config, the issues it has against the workflow's
        inputs, its URL and its counts.

        Args:
            form_id: The form ID.

        Raises:
            TracecatNotFoundError: If the form does not exist.
        """
        return await self._client.get(f"/forms/{form_id}")

    async def update_form(
        self,
        *,
        workflow_id: str,
        status: Literal["online", "offline"] | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Update a workflow's form, creating it if the workflow has none.

        Args:
            workflow_id: Workflow UUID (short ``wf_...`` or full format).
            status: ``online`` or ``offline``. Omit to leave unchanged.
            config: The whole form config. Omit to leave unchanged.

        Returns:
            The form, in the same shape as :meth:`get_form`.

        Raises:
            TracecatNotFoundError: If the workflow does not exist.
            TracecatValidationError: If the config has errors.
            TracecatConflictError: If the form goes online on an unpublished
                workflow.
        """
        payload: dict[str, Any] = {}
        if status is not None:
            payload["status"] = status
        if config is not None:
            payload["config"] = config
        return await self._client.put(f"/forms/workflows/{workflow_id}", json=payload)

    async def list_submissions(
        self,
        form_id: str,
        *,
        limit: int = 20,
        cursor: str | None = None,
        submitted_after: datetime | None = None,
        submitted_before: datetime | None = None,
    ) -> dict[str, Any]:
        """List a form's submissions, newest first.

        Args:
            form_id: The form ID.
            limit: Maximum number of submissions to return.
            cursor: Opaque ``next_cursor`` from a previous page.
            submitted_after: Only submissions at or after this time.
            submitted_before: Only submissions before this time.

        Returns:
            dict with ``items`` (each with ``id``, ``reference``, ``answers``,
            ``submitted_by``, ``wf_exec_id``, ``run_status``,
            ``submitted_at``), plus the cursor fields.

        Raises:
            TracecatNotFoundError: If the form does not exist.
        """
        params = _page_params(limit, cursor)
        if submitted_after is not None:
            params["submitted_after"] = submitted_after.isoformat()
        if submitted_before is not None:
            params["submitted_before"] = submitted_before.isoformat()
        return await self._client.get(f"/forms/{form_id}/submissions", params=params)

    async def get_submission(self, submission_id: str) -> dict[str, Any]:
        """Get one submission with its answers.

        Args:
            submission_id: The submission ID.

        Raises:
            TracecatNotFoundError: If the submission does not exist.
        """
        return await self._client.get(f"/forms/submissions/{submission_id}")

    async def delete_submission(self, submission_id: str) -> None:
        """Delete a submission. The workflow run it started is not affected.

        Args:
            submission_id: The submission ID.

        Raises:
            TracecatNotFoundError: If the submission does not exist.
        """
        await self._client.delete(f"/forms/submissions/{submission_id}")

    async def download_file(self, file_id: str) -> dict[str, Any]:
        """Download a file a form submission carries.

        Args:
            file_id: The ``id`` of a file object in a submission's answers.

        Returns:
            dict with ``id``, ``name``, ``content_type``, ``size``, ``sha256``
            and ``content_base64``.

        Raises:
            TracecatNotFoundError: If the file does not exist or no submission
                has claimed it.
        """
        return await self._client.get(f"/forms/files/{file_id}/download")
