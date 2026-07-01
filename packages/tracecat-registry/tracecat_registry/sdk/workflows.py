"""Workflows SDK client for Tracecat API."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, Any, Literal

from tracecat_registry.sdk.types import UNSET, Unset, is_set

if TYPE_CHECKING:
    from tracecat_registry.sdk.client import TracecatClient

# Terminal statuses that indicate workflow completion
TERMINAL_STATUSES = frozenset(
    {"COMPLETED", "FAILED", "CANCELED", "TERMINATED", "TIMED_OUT"}
)

# Default polling configuration
DEFAULT_POLL_INTERVAL = 2.0  # seconds
DEFAULT_MAX_WAIT_TIME = 300.0  # 5 minutes


class WorkflowExecutionError(Exception):
    """Raised when a workflow execution fails."""

    def __init__(
        self, status: str, workflow_execution_id: str, message: str | None = None
    ):
        self.status = status
        self.workflow_execution_id = workflow_execution_id
        self.message = (
            message or f"Workflow execution {workflow_execution_id} {status.lower()}"
        )
        super().__init__(self.message)


class WorkflowExecutionTimeout(Exception):
    """Raised when waiting for workflow execution times out."""

    def __init__(self, workflow_execution_id: str, timeout: float):
        self.workflow_execution_id = workflow_execution_id
        self.timeout = timeout
        super().__init__(
            f"Timeout waiting for workflow execution {workflow_execution_id} after {timeout}s"
        )


class WorkflowsClient:
    """Client for Workflows API operations."""

    def __init__(self, client: TracecatClient) -> None:
        self._client = client

    async def create_workflow(
        self,
        *,
        title: str | None = None,
        description: str | None = None,
        definition_yaml: str | None = None,
    ) -> dict[str, Any]:
        """Create a new workflow in the current workspace.

        Args:
            title: Workflow title (3-100 characters). For an empty create
                (no ``definition_yaml``), the API assigns a timestamped title
                when omitted. With ``definition_yaml``, the title must come from
                this arg or a ``title:`` in the YAML, else the create is rejected.
            description: Optional workflow description.
            definition_yaml: Optional full workflow definition as YAML. When
                provided, the workflow is created with these actions (and any
                layout/case trigger) instead of being empty. Schedules are not
                created from this YAML; add them afterwards via
                :meth:`edit_workflow`.

        Returns:
            dict containing:
                - id: Workflow ID in short ``wf_...`` format.
                - title: The workflow title.

        Raises:
            TracecatValidationError: If the title or definition is invalid.
            TracecatAPIError: For other API errors.
        """
        data: dict[str, Any] = {}
        if title is not None:
            data["title"] = title
        if description is not None:
            data["description"] = description
        if definition_yaml is not None:
            data["definition_yaml"] = definition_yaml
        return await self._client.post("/workflows", json=data)

    async def get_workflow(self, *, workflow_id: str) -> dict[str, Any]:
        """Read a workflow's editable draft document and revision.

        Args:
            workflow_id: Workflow UUID (short ``wf_...`` or full format).

        Returns:
            dict with ``workflow_id``, ``draft_revision``, and ``draft_document``
            (the editable metadata/definition/layout/schedules/case_trigger).
            Pass ``draft_revision`` as ``base_revision`` to :meth:`edit_workflow`.

        Raises:
            TracecatNotFoundError: If the workflow does not exist.
            TracecatAPIError: For other API errors.
        """
        return await self._client.get(f"/workflows/{workflow_id}/edit-document")

    async def edit_workflow(
        self,
        *,
        workflow_id: str,
        base_revision: str,
        patch_ops: list[dict[str, Any]],
        validate_only: bool = False,
    ) -> dict[str, Any]:
        """Edit a workflow draft using RFC 6902 JSON Patch operations.

        Fetch the current document and revision with :meth:`get_workflow`,
        compute the patch ops against ``draft_document``, then call this with the
        returned ``draft_revision`` as ``base_revision``.

        Args:
            workflow_id: Workflow UUID (short ``wf_...`` or full format).
            base_revision: The ``draft_revision`` the patch is computed against.
            patch_ops: RFC 6902 JSON Patch operations restricted to the editable
                sections (metadata, definition, layout, schedules, case_trigger).
            validate_only: When True, validate the patch without persisting.

        Returns:
            dict with ``message``, ``workflow_id``, and the new ``draft_revision``.

        Raises:
            TracecatConflictError: If ``base_revision`` no longer matches the
                current draft (concurrent edit). Re-fetch and retry.
            TracecatValidationError: If the patch is invalid.
            TracecatAPIError: For other API errors.
        """
        return await self._client.patch(
            f"/workflows/{workflow_id}/edit-document",
            json={
                "base_revision": base_revision,
                "patch_ops": patch_ops,
                "validate_only": validate_only,
            },
        )

    async def publish(self, *, workflow_id: str) -> dict[str, Any]:
        """Publish (commit) a workflow's current draft as a new version.

        Validates the draft, freezes registry dependencies, and creates a new
        versioned definition. Run the published workflow afterwards with
        :meth:`execute`.

        Args:
            workflow_id: Workflow UUID (short ``wf_...`` or full format).

        Returns:
            dict with ``workflow_id``, ``version`` (the new definition version),
            and ``message``.

        Raises:
            TracecatValidationError: If the draft fails validation (400). The
                ``detail`` carries the per-error list so the caller can fix the
                draft and retry.
            TracecatNotFoundError: If the workflow does not exist.
            TracecatAPIError: For other API errors.
        """
        return await self._client.post(f"/workflows/{workflow_id}/publish")

    async def run(
        self,
        *,
        workflow_id: str,
        inputs: Any | None = None,
        use_draft: bool = True,
        version: int | None = None,
    ) -> dict[str, Any]:
        """Run a workflow from its draft state or a published definition.

        Args:
            workflow_id: Workflow UUID (short ``wf_...`` or full format).
            inputs: Trigger inputs to pass to the workflow.
            use_draft: When ``True`` (default), run the current draft graph
                without publishing. When ``False``, run a published definition.
            version: Published definition version to run. Only applies when
                ``use_draft`` is ``False``; ``None`` runs the current published
                version. Ignored when ``use_draft`` is ``True``.

        Returns:
            dict with ``workflow_id``, ``workflow_execution_id``, and
            ``status`` (``"STARTED"``).

        Raises:
            TracecatValidationError: If the draft fails validation (400).
            TracecatNotFoundError: If the workflow or requested version does not
                exist.
            TracecatAPIError: For other API errors.
        """
        data: dict[str, Any] = {
            "workflow_id": workflow_id,
            "use_draft": use_draft,
        }
        if inputs is not None:
            data["inputs"] = inputs
        if version is not None:
            data["version"] = version

        response = await self._client.post("/workflows/run", json=data)
        return {
            "workflow_id": response["workflow_id"],
            "workflow_execution_id": response["workflow_execution_id"],
            "status": "STARTED",
        }

    async def get_authoring_context(
        self,
        *,
        action_names: list[str] | None = None,
        query: str | None = None,
    ) -> dict[str, Any]:
        """Fetch authoring context (schemas, secrets, examples) for actions.

        Resolve actions either by explicit ``action_names`` or, when none are
        given, by ``query`` search. With neither, only the workspace
        variable/secret hints are returned.

        Args:
            action_names: Fully qualified action names (e.g.
                ``["core.http_request"]``) to fetch context for.
            query: Search string to resolve actions by name/description when
                ``action_names`` is omitted.

        Returns:
            dict with ``actions`` (each a schema/secrets/examples context),
            ``variable_hints``, ``secret_hints``, and ``enabled_models`` (the
            models available in this workspace; select a ``catalog_id`` from
            this list when configuring AI actions or agent presets).

        Raises:
            TracecatAPIError: For API errors.
        """
        data: dict[str, Any] = {}
        if action_names is not None:
            data["action_names"] = action_names
        if query is not None:
            data["query"] = query
        return await self._client.post("/workflows/authoring-context", json=data)

    async def get_webhook(self, *, workflow_id: str) -> dict[str, Any]:
        """Read a workflow's webhook trigger configuration.

        Args:
            workflow_id: Workflow UUID (short ``wf_...`` or full format).

        Returns:
            dict with the webhook ``status`` (``"online"``/``"offline"``), the
            public ``url`` to POST events to, the allowed ``methods``, and
            ``entrypoint_ref``.

        Raises:
            TracecatNotFoundError: If the workflow has no webhook.
            TracecatAPIError: For other API errors.
        """
        return await self._client.get(f"/workflows/{workflow_id}/webhook")

    async def update_webhook(
        self,
        *,
        workflow_id: str,
        status: Literal["online", "offline"],
    ) -> None:
        """Enable or disable a workflow's webhook trigger.

        Args:
            workflow_id: Workflow UUID (short ``wf_...`` or full format).
            status: ``"online"`` to enable the webhook (the workflow becomes
                triggerable via its webhook ``url``) or ``"offline"`` to disable
                it.

        Raises:
            TracecatNotFoundError: If the workflow does not exist.
            TracecatAPIError: For other API errors.
        """
        await self._client.patch(
            f"/workflows/{workflow_id}/webhook",
            json={"status": status},
        )

    async def get_case_trigger(self, *, workflow_id: str) -> dict[str, Any]:
        """Read a workflow's case-trigger configuration.

        Args:
            workflow_id: Workflow UUID (short ``wf_...`` or full format).

        Returns:
            dict with ``status`` (``"online"``/``"offline"``), ``event_types``
            (the case events that fire the workflow), and ``tag_filters``.

        Raises:
            TracecatNotFoundError: If the workflow has no case trigger.
            TracecatAPIError: For other API errors.
        """
        return await self._client.get(f"/workflows/{workflow_id}/case-trigger")

    async def update_case_trigger(
        self,
        *,
        workflow_id: str,
        status: Literal["online", "offline"] | None = None,
        event_types: list[str] | None = None,
        tag_filters: list[str] | None = None,
    ) -> None:
        """Configure a workflow's case trigger.

        This is the only supported way to set a case trigger. The case-trigger
        config is NOT editable through :meth:`edit_workflow` JSON patches.

        Args:
            workflow_id: Workflow UUID (short ``wf_...`` or full format).
            status: ``"online"`` to enable or ``"offline"`` to disable. When
                setting ``"online"``, ``event_types`` must be non-empty (either
                passed here or already configured).
            event_types: Case event types that fire the workflow (e.g.
                ``["case_created", "status_changed"]``).
            tag_filters: Optional case-tag refs to restrict which cases fire the
                trigger.

        Raises:
            TracecatNotFoundError: If the workflow does not exist.
            TracecatValidationError: If ``status`` is ``"online"`` with no
                ``event_types``.
            TracecatAPIError: For other API errors.
        """
        data: dict[str, Any] = {}
        if status is not None:
            data["status"] = status
        if event_types is not None:
            data["event_types"] = event_types
        if tag_filters is not None:
            data["tag_filters"] = tag_filters
        await self._client.patch(
            f"/workflows/{workflow_id}/case-trigger",
            json=data,
        )

    async def execute(
        self,
        *,
        workflow_id: str | None = None,
        workflow_alias: str | None = None,
        trigger_inputs: Any | Unset = UNSET,
        environment: str | None = None,
        wait_strategy: Literal["wait", "detach"] = "detach",
        timeout: float | None = None,
        poll_interval: float = DEFAULT_POLL_INTERVAL,
        parent_workflow_execution_id: str | None = None,
    ) -> dict[str, Any]:
        """Execute a workflow by ID or alias.

        Args:
            workflow_id: Workflow UUID (short or full format).
            workflow_alias: Workflow alias (alternative to ID).
            trigger_inputs: Inputs to pass to the workflow.
            environment: Target execution environment for secrets isolation.
            wait_strategy: How to handle execution:
                - "detach": Return immediately with execution info.
                - "wait": Poll until completion and return the result.
            timeout: Maximum time to wait for completion in seconds (only used with
                wait_strategy="wait"). Defaults to 300s (5 minutes).
            poll_interval: Time between status polls in seconds (default: 2.0).
            parent_workflow_execution_id: Parent workflow execution ID for correlation.
                                          Stored in Temporal memo for tracing.

        Returns:
            For wait_strategy="detach":
                {"workflow_id": str, "workflow_execution_id": str, "status": "STARTED"}
            For wait_strategy="wait":
                The workflow result if successful.

        Raises:
            WorkflowExecutionError: If workflow fails, is canceled, or terminated.
            WorkflowExecutionTimeout: If timeout is reached while waiting.
            TracecatNotFoundError: If workflow or alias not found.
            TracecatAPIError: For other API errors.
        """
        if not workflow_id and not workflow_alias:
            raise ValueError("Either workflow_id or workflow_alias must be provided")

        # Build request payload
        data: dict[str, Any] = {}
        if workflow_id:
            data["workflow_id"] = workflow_id
        if workflow_alias:
            data["workflow_alias"] = workflow_alias
        if is_set(trigger_inputs):
            data["trigger_inputs"] = trigger_inputs
        if environment:
            data["environment"] = environment
        if parent_workflow_execution_id:
            data["parent_workflow_execution_id"] = parent_workflow_execution_id

        # Start the workflow execution
        response = await self._client.post("/workflows/executions", json=data)

        wf_id = response["workflow_id"]
        wf_exec_id = response["workflow_execution_id"]

        if wait_strategy == "detach":
            return {
                "workflow_id": wf_id,
                "workflow_execution_id": wf_exec_id,
                "status": "STARTED",
            }

        # wait_strategy == "wait": Poll until completion
        max_wait = timeout if timeout else DEFAULT_MAX_WAIT_TIME
        return await self._poll_until_complete(
            workflow_execution_id=wf_exec_id,
            max_wait=max_wait,
            poll_interval=poll_interval,
        )

    async def get_status(self, workflow_execution_id: str) -> dict[str, Any]:
        """Get the status of a workflow execution.

        Args:
            workflow_execution_id: The workflow execution ID.

        Returns:
            dict containing:
                - workflow_execution_id: Execution ID
                - status: RUNNING, COMPLETED, FAILED, CANCELED, TERMINATED, TIMED_OUT
                - start_time: When execution started (ISO format or None)
                - close_time: When execution completed (ISO format or None)
                - result: Workflow result (if completed successfully)

        Raises:
            TracecatNotFoundError: If execution not found.
            TracecatAPIError: For other API errors.
        """
        # Server uses {execution_id:path} to handle '/' in the ID
        return await self._client.get(f"/workflows/executions/{workflow_execution_id}")

    async def _poll_until_complete(
        self,
        workflow_execution_id: str,
        max_wait: float,
        poll_interval: float,
    ) -> Any:
        """Poll workflow execution status until completion or timeout.

        Args:
            workflow_execution_id: The workflow execution ID.
            max_wait: Maximum time to wait in seconds.
            poll_interval: Time between polls in seconds (must be > 0).

        Returns:
            The workflow result if completed successfully.

        Raises:
            WorkflowExecutionError: If workflow fails, is canceled, or terminated.
            WorkflowExecutionTimeout: If max_wait is exceeded.
            ValueError: If poll_interval is not positive.
        """
        if poll_interval <= 0:
            raise ValueError("poll_interval must be positive")
        elapsed = 0.0

        while elapsed < max_wait:
            status_response = await self.get_status(workflow_execution_id)
            status = status_response["status"]

            if status == "COMPLETED":
                return status_response.get("result")

            if status in TERMINAL_STATUSES:
                # Workflow ended but not successfully - include error details if available
                error_detail = status_response.get("error")
                message = (
                    f"Workflow execution {workflow_execution_id} failed: {error_detail}"
                    if error_detail
                    else f"Workflow execution {workflow_execution_id} ended with status: {status}"
                )
                raise WorkflowExecutionError(
                    status=status,
                    workflow_execution_id=workflow_execution_id,
                    message=message,
                )

            # Still running, wait and poll again
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

        raise WorkflowExecutionTimeout(
            workflow_execution_id=workflow_execution_id, timeout=max_wait
        )
