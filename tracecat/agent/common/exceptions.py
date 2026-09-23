"""Exceptions for agent preparation and sandbox execution."""

from __future__ import annotations


class AgentSandboxError(Exception):
    """Base exception for agent sandbox errors."""


class AgentSandboxValidationError(AgentSandboxError):
    """Raised when sandbox input validation fails."""


class AgentSandboxTimeoutError(AgentSandboxError):
    """Raised when agent execution times out."""


class AgentSandboxExecutionError(AgentSandboxError):
    """Raised when agent execution fails."""


class AgentRuntimeInvariantError(AgentSandboxExecutionError):
    """Raised when trusted runtime state violates a platform invariant."""


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


class AgentToolResolutionError(ValueError):
    """Raised when requested registry actions cannot be built into agent tools."""

    def __init__(
        self,
        *,
        missing_actions: frozenset[str] = frozenset(),
        missing_platform_actions: frozenset[str] = frozenset(),
        entitlement_denied_actions: frozenset[str] = frozenset(),
        failed_actions: frozenset[str] = frozenset(),
    ) -> None:
        super().__init__("Unable to build the requested agent tools")
        self.missing_actions = missing_actions
        """Missing actions that no platform registry provides."""
        self.missing_platform_actions = missing_platform_actions
        """Missing actions that a platform registry provides."""
        self.entitlement_denied_actions = entitlement_denied_actions
        """Missing platform actions that require a disabled entitlement."""
        self.failed_actions = failed_actions
        """Actions found in the registry whose tool build failed."""


class AgentToolLimitExceededError(ValueError):
    """Raised when an agent requests more tools than the configured limit."""

    def __init__(self, *, requested: int, limit: int) -> None:
        super().__init__(f"Cannot request more than {limit} tools")
        self.requested = requested
        self.limit = limit


class UserMCPDiscoveryError(RuntimeError):
    """Raised when tool discovery fails for a user-configured MCP server."""

    def __init__(self, server_name: str) -> None:
        super().__init__(
            f"Failed to discover tools from user MCP server '{server_name}'"
        )
        self.server_name = server_name


class UserMCPDiscoveryAuthError(UserMCPDiscoveryError):
    """Raised when a user MCP server rejects the configured credentials."""


class UserMCPDiscoveryUnavailableError(UserMCPDiscoveryError):
    """Raised when a user MCP server is unreachable or rejects the request."""

    def __init__(self, server_name: str, *, retryable: bool) -> None:
        super().__init__(server_name)
        self.retryable = retryable
