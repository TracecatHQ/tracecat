from typing import Annotated

from tracecat.agent.executor.aio import AioStreamingAgentExecutor
from tracecat.agent.executor.base import BaseAgentExecutor
from tracecat.auth.credentials import RoleACL
from tracecat.db.dependencies import AsyncDBSession
from tracecat.types.auth import Role
from tracecat.utils import load_ee_impl

WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
    ),
]


async def get_executor(
    session: AsyncDBSession,
    role: WorkspaceUser,
) -> BaseAgentExecutor:
    """Get the appropriate agent execution service based on edition."""
    impl = load_ee_impl(
        "tracecat.agent.executor",
        default=AioStreamingAgentExecutor,
    )
    if not issubclass(impl, BaseAgentExecutor):
        raise RuntimeError(
            f"EE agent executor implementation is not a valid AgentExecutor: {impl}"
        )
    return impl(session, role)
