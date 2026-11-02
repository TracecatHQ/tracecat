"""Sandbox configuration for platform background workflows."""

from temporalio.worker.workflow_sandbox import (
    SandboxedWorkflowRunner,
    SandboxRestrictions,
)


def new_sandbox_runner() -> SandboxedWorkflowRunner:
    """Preserve workflow restrictions without reimporting beartype's hooks."""
    # FastMCP dependencies install a global beartype import hook. Reloading the
    # hook inside the sandbox recursively imports its partially initialized state.
    return SandboxedWorkflowRunner(
        restrictions=SandboxRestrictions.default.with_passthrough_modules("beartype")
    )
