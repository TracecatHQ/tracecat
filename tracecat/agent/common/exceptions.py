"""Exceptions for agent sandbox execution."""

from __future__ import annotations


class AgentSandboxError(Exception):
    """Base exception for agent sandbox errors."""


class AgentSandboxValidationError(AgentSandboxError):
    """Raised when sandbox input validation fails."""


class AgentSandboxTimeoutError(AgentSandboxError):
    """Raised when agent execution times out."""


class AgentSandboxExecutionError(AgentSandboxError):
    """Raised when agent execution fails."""


class UserMCPDiscoveryError(RuntimeError):
    """Raised when strict tool discovery fails for a user-configured MCP server.

    Carries only the workspace-owned server name and a sanitized error summary
    (exception type plus status/MCP code); response bodies, headers, and URLs
    stay in the chained cause.
    """

    def __init__(self, server_name: str, error_summary: str) -> None:
        super().__init__(
            f"Failed to discover tools from user MCP server '{server_name}'"
        )
        self.server_name = server_name
        self.error_summary = error_summary


class AgentSandboxProcessExitError(AgentSandboxExecutionError):
    """Raised when the jailed agent runtime process exited with a failure code.

    The Claude SDK erases the transport's typed process error into a plain
    ``Exception``, so the runtime rebuilds this from the exit code the sandbox
    transport recorded. The code follows the nsjail contract: a signal death
    is ``128 + signal``.
    """

    def __init__(self, exit_code: int) -> None:
        super().__init__(f"Agent sandbox process exited with code {exit_code}")
        self.exit_code = exit_code
