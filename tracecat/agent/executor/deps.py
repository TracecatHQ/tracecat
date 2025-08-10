from tracecat.agent.executor.aio import AioAgentExecutor
from tracecat.agent.executor.base import BaseAgentExecutor
from tracecat.db.dependencies import AsyncDBSession
from tracecat.types.auth import Role
from tracecat.utils import load_ee_impl


async def get_executor(
    session: AsyncDBSession,
    role: Role,
) -> BaseAgentExecutor:
    """Get the appropriate agent execution service based on edition."""
    impl = load_ee_impl(
        "tracecat.agent.executor",
        default=AioAgentExecutor,
    )
    if not issubclass(impl, BaseAgentExecutor):
        raise RuntimeError(
            f"EE agent executor implementation is not a valid AgentExecutor: {impl}"
        )
    return impl(session, role)
