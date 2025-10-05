from abc import ABC, abstractmethod
from typing import Any

from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.agent.models import RunAgentArgs, RunAgentResult
from tracecat.contexts import ctx_role
from tracecat.types.auth import Role
from tracecat.types.exceptions import TracecatAuthorizationError


class BaseAgentRunHandle(ABC):
    """Uniform handle for an in-flight agent run."""

    run_id: str

    def __init__(self, run_id: str):
        self.run_id = run_id

    @abstractmethod
    async def result(self) -> RunAgentResult:
        """Wait for completion and return the result."""
        ...

    @abstractmethod
    async def cancel(self) -> None:
        """Best-effort cancellation."""
        ...


class BaseAgentExecutor(ABC):
    """Single public interface for executing agent turns."""

    def __init__(self, session: AsyncSession, role: Role | None = None, **kwargs: Any):
        self.session = session
        self.role = role or ctx_role.get()

        if self.role is None or self.role.workspace_id is None:
            raise TracecatAuthorizationError(
                f"{self.__class__.__name__} requires workspace"
            )
        self.workspace_id = self.role.workspace_id

    async def run(self, args: RunAgentArgs) -> RunAgentResult:
        """Run an agentic turn and wait for completion."""
        handle = await self.start(args)
        return await handle.result()

    @abstractmethod
    async def start(self, args: RunAgentArgs) -> BaseAgentRunHandle:
        """Start an agentic turn without waiting for it to complete."""
        ...
