from __future__ import annotations

from collections.abc import Awaitable, Callable

import pytest

from tracecat.agent.preset import router as agent_preset_router
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.exceptions import ScopeDeniedError
from tracecat.inbox import router as inbox_router
from tracecat.registry.repositories import router as registry_repos_router
from tracecat.tables import router as tables_router

type AsyncEndpoint = Callable[..., Awaitable[object]]


async def _assert_endpoint_requires_scope(
    endpoint: AsyncEndpoint, required_scope: str
) -> None:
    no_scope_role = Role(
        type="user",
        service_id="tracecat-api",
        scopes=frozenset(),
    )
    token = ctx_role.set(no_scope_role)
    try:
        with pytest.raises(ScopeDeniedError):
            await endpoint()
    finally:
        ctx_role.reset(token)

    allowed_role = Role(
        type="user",
        service_id="tracecat-api",
        scopes=frozenset({required_scope}),
    )
    token = ctx_role.set(allowed_role)
    try:
        with pytest.raises(TypeError):
            await endpoint()
    finally:
        ctx_role.reset(token)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("endpoint", "required_scope"),
    [
        (registry_repos_router.list_repository_versions, "org:registry:read"),
        (registry_repos_router.list_registry_repositories, "org:registry:read"),
        (registry_repos_router.get_registry_repository, "org:registry:read"),
        (registry_repos_router.list_repository_commits, "org:registry:read"),
        (registry_repos_router.compare_registry_versions, "org:registry:read"),
        (registry_repos_router.get_previous_registry_version, "org:registry:read"),
        (registry_repos_router.sync_registry_repository, "org:registry:manage"),
        (registry_repos_router.create_registry_repository, "org:registry:manage"),
        (registry_repos_router.update_registry_repository, "org:registry:manage"),
        (registry_repos_router.delete_registry_repository, "org:registry:manage"),
        (registry_repos_router.promote_registry_version, "org:registry:manage"),
        (registry_repos_router.delete_registry_version, "org:registry:manage"),
    ],
)
async def test_registry_repository_scope_guards(
    endpoint: AsyncEndpoint, required_scope: str
) -> None:
    await _assert_endpoint_requires_scope(endpoint, required_scope)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("endpoint", "required_scope"),
    [
        (agent_preset_router.list_agent_presets, "agent:read"),
        (agent_preset_router.get_agent_preset, "agent:read"),
        (agent_preset_router.get_agent_preset_by_slug, "agent:read"),
        (agent_preset_router.create_agent_preset, "agent:update"),
        (agent_preset_router.update_agent_preset, "agent:update"),
        (agent_preset_router.delete_agent_preset, "agent:delete"),
    ],
)
async def test_agent_preset_scope_guards(
    endpoint: AsyncEndpoint, required_scope: str
) -> None:
    await _assert_endpoint_requires_scope(endpoint, required_scope)


@pytest.mark.anyio
async def test_table_update_row_requires_table_update_scope() -> None:
    await _assert_endpoint_requires_scope(tables_router.update_row, "table:update")


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("endpoint", "required_scope"),
    [
        (inbox_router.list_items, "inbox:read"),
        (inbox_router.list_items_paginated, "inbox:read"),
    ],
)
async def test_inbox_scope_guards(endpoint: AsyncEndpoint, required_scope: str) -> None:
    await _assert_endpoint_requires_scope(endpoint, required_scope)
