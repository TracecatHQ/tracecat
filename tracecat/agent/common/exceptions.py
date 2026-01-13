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
