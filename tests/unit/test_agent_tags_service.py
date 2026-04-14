from collections.abc import Awaitable, Callable
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from tracecat.agent.tags.service import AgentTagsService
from tracecat.auth.types import Role
from tracecat.exceptions import ScopeDeniedError


def _role_with_scopes(scopes: frozenset[str] | None) -> Role:
    return Role(
        type="user",
        user_id=uuid4(),
        organization_id=uuid4(),
        workspace_id=uuid4(),
        service_id="tracecat-api",
        scopes=scopes,
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "invoker",
    [
        lambda service: service.list_tags(),
        lambda service: service.get_tag(uuid4()),
    ],
)
async def test_agent_tag_read_methods_require_agent_read_scope(
    invoker: Callable[[AgentTagsService], Awaitable[object]],
) -> None:
    """Read methods should reject callers without agent:read before querying."""
    session = AsyncMock()
    service = AgentTagsService(session=session, role=_role_with_scopes(frozenset()))

    with pytest.raises(ScopeDeniedError) as exc_info:
        await invoker(service)

    assert exc_info.value.missing_scopes == ["agent:read"]
    session.execute.assert_not_awaited()
