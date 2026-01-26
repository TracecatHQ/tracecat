"""Registry execution context.

This module provides context management for registry actions running
in sandboxed environments. The context is injected from environment
variables and provides access to platform services via the SDK.
"""

from __future__ import annotations

import os
from contextvars import ContextVar
from dataclasses import dataclass, field
from functools import cached_property
from typing import TYPE_CHECKING


if TYPE_CHECKING:
    from tracecat_registry.sdk.client import TracecatClient
    from tracecat_registry.sdk.cases import CasesClient
    from tracecat_registry.sdk.presets import PresetsClient
    from tracecat_registry.sdk.tables import TablesClient
    from tracecat_registry.sdk.variables import VariablesClient
    from tracecat_registry.sdk.workflows import WorkflowsClient


@dataclass
class RegistryContext:
    """Execution context for registry actions.

    This context is populated from environment variables when a UDF
    executes in a sandbox. It provides access to platform services
    via the SDK client.

    Attributes:
        workspace_id: The workspace UUID where the workflow is running.
        workflow_id: The workflow UUID being executed.
        run_id: The workflow run UUID.
        wf_exec_id: The full workflow execution ID (for correlation).
        environment: The execution environment (e.g., "default").
        api_url: The Tracecat API URL.
        executor_url: The Tracecat executor service URL (sandbox execution).
        token: The executor JWT for authentication.
    """

    workspace_id: str
    workflow_id: str
    run_id: str
    wf_exec_id: str | None = None
    environment: str = "default"
    api_url: str = "http://api:8000"
    executor_url: str = "http://executor:8000"
    token: str = ""

    # Lazily initialized SDK client
    _client: TracecatClient | None = field(default=None, repr=False)

    @classmethod
    def from_env(cls) -> RegistryContext:
        """Create a context from environment variables.

        Expected environment variables:
        - TRACECAT__WORKSPACE_ID: Workspace UUID
        - TRACECAT__WORKFLOW_ID: Workflow UUID
        - TRACECAT__RUN_ID: Run UUID
        - TRACECAT__WF_EXEC_ID: Full workflow execution ID (for correlation)
        - TRACECAT__ENVIRONMENT: Execution environment (default: "default")
        - TRACECAT__API_URL: API URL (default: "http://api:8000")
        - TRACECAT__EXECUTOR_URL: Executor URL (default: "http://executor:8000")
        - TRACECAT__EXECUTOR_TOKEN: JWT token for authentication

        Returns:
            RegistryContext populated from environment.

        Raises:
            ValueError: If required environment variables are missing.
        """
        workspace_id = os.environ.get("TRACECAT__WORKSPACE_ID")
        workflow_id = os.environ.get("TRACECAT__WORKFLOW_ID")
        run_id = os.environ.get("TRACECAT__RUN_ID")

        if not workspace_id:
            raise ValueError("TRACECAT__WORKSPACE_ID environment variable is required")
        if not workflow_id:
            raise ValueError("TRACECAT__WORKFLOW_ID environment variable is required")
        if not run_id:
            raise ValueError("TRACECAT__RUN_ID environment variable is required")

        return cls(
            workspace_id=workspace_id,
            workflow_id=workflow_id,
            run_id=run_id,
            wf_exec_id=os.environ.get("TRACECAT__WF_EXEC_ID"),
            environment=os.environ.get("TRACECAT__ENVIRONMENT", "default"),
            api_url=os.environ.get("TRACECAT__API_URL", "http://api:8000"),
            executor_url=os.environ.get(
                "TRACECAT__EXECUTOR_URL", "http://executor:8000"
            ),
            token=os.environ.get("TRACECAT__EXECUTOR_TOKEN", ""),
        )

    @cached_property
    def client(self) -> TracecatClient:
        """Get the SDK client for this context."""
        from tracecat_registry.sdk.client import TracecatClient

        return TracecatClient(
            api_url=self.api_url,
            token=self.token,
            workspace_id=self.workspace_id,
        )

    @property
    def cases(self) -> "CasesClient":
        """Get the Cases API client."""
        return self.client.cases

    @property
    def presets(self) -> "PresetsClient":
        """Get the Agent Presets API client."""
        return self.client.presets

    @property
    def tables(self) -> "TablesClient":
        """Get the Tables API client."""
        return self.client.tables

    @property
    def variables(self) -> "VariablesClient":
        """Get the Variables API client."""
        return self.client.variables

    @property
    def workflows(self) -> "WorkflowsClient":
        """Get the Workflows API client."""
        return self.client.workflows


# Context variable for the current registry context
_ctx: ContextVar[RegistryContext | None] = ContextVar("registry_context", default=None)


def get_context() -> RegistryContext:
    """Get the current registry context.

    Returns:
        The current RegistryContext.

    Raises:
        RuntimeError: If no context is set.
    """
    ctx = _ctx.get()
    if ctx is None:
        raise RuntimeError(
            "No registry context is set. "
            "Context must be set before calling registry actions."
        )
    return ctx


def set_context(ctx: RegistryContext) -> None:
    """Set the current registry context.

    Args:
        ctx: The context to set.
    """
    _ctx.set(ctx)


def clear_context() -> None:
    """Clear the current registry context."""
    _ctx.set(None)


def init_context_from_env() -> RegistryContext:
    """Initialize and set context from environment variables.

    This is a convenience function that creates a context from
    environment variables and sets it as the current context.

    Returns:
        The initialized RegistryContext.
    """
    ctx = RegistryContext.from_env()
    set_context(ctx)
    return ctx
