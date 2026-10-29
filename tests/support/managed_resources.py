"""Shared setup for managed resources tests.

Seeds a workspace by pulling the expanded workspace sync fixture tree, so
every resource type exists with real rows, and marks chosen resources as
managed the way a rollout would.
"""

from __future__ import annotations

import uuid
from contextlib import AbstractContextManager
from typing import Any, Protocol
from unittest.mock import AsyncMock, patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests.support.fake_vcs import FakeVcsServer
from tests.unit.test_workspace_sync_acceptance_contract import (
    _expanded_full_git_tree,
    _set_workspace_git_repo_url,
)
from tracecat.auth.types import Role
from tracecat.db.models import ManagedResource
from tracecat.git.types import GitUrl
from tracecat.registry.lock.types import RegistryLock
from tracecat.sync import PullOptions
from tracecat.workspace_sync.adapters import RESOURCE_ADAPTERS_BY_TYPE
from tracecat.workspace_sync.enums import SyncResourceType, VcsProvider
from tracecat.workspace_sync.service import WorkspaceSyncService

REPO_URL = "git+ssh://git@github.com/TracecatHQ/managed-test.git"
GIT_URL = GitUrl(host="github.com", org="TracecatHQ", repo="managed-test")


def no_registry_lock() -> AbstractContextManager[object]:
    """Skip registry lock resolution, which needs a synced platform registry."""
    return patch(
        "tracecat.workflow.management.management.RegistryLockService.resolve_lock_with_bindings",
        AsyncMock(return_value=RegistryLock(origins={}, actions={})),
    )


async def seed_workspace(
    session: AsyncSession,
    role: Role,
    *,
    fake_vcs: FakeVcsServer,
    repo_url: str = REPO_URL,
    git_url: GitUrl = GIT_URL,
) -> WorkspaceSyncService:
    """Pull the expanded fixture tree into ``role``'s workspace."""
    assert role.workspace_id is not None
    await _set_workspace_git_repo_url(
        session, workspace_id=role.workspace_id, repo_url=repo_url
    )
    service = WorkspaceSyncService(
        session=session,
        role=role,
        provider=VcsProvider.GITHUB,
        transport_factory=fake_vcs.transport_factory,
    )
    seed = await fake_vcs.transport_factory(
        VcsProvider.GITHUB, session=session, role=role
    ).write_files(
        url=git_url,
        files=_expanded_full_git_tree(include_schedules=False),
        message="Seed",
        branch=f"seed-{uuid.uuid4().hex[:6]}",
        create_pr=False,
    )
    assert seed.sha is not None
    with no_registry_lock():
        pulled = await service.pull(options=PullOptions(commit_sha=seed.sha))
    assert pulled.success, pulled.diagnostics
    return service


async def mark_managed(
    session: AsyncSession,
    role: Role,
    resource_type: SyncResourceType,
    local_id: uuid.UUID,
    *,
    source_id: str | None = None,
    tag: str = "v1.0.0",
) -> ManagedResource:
    """Record a resource as managed, as a rollout of ``tag`` would."""
    assert role.workspace_id is not None
    source_id = source_id or str(local_id)
    row = ManagedResource(
        workspace_id=role.workspace_id,
        resource_type=resource_type.value,
        local_id=local_id,
        source_id=source_id,
        # The path a rollout records: the adapter's repository path.
        source_path=RESOURCE_ADAPTERS_BY_TYPE[resource_type].source_path(source_id),
        deployed_tag=tag,
        deployed_commit_sha="a" * 40,
    )
    session.add(row)
    await session.flush()
    return row


class _HasId(Protocol):
    id: Any


async def local_id_of[T: _HasId](
    session: AsyncSession, model: type[T], role: Role, **filters: object
) -> uuid.UUID:
    """Return the id of the one ``model`` row in the workspace matching filters."""
    stmt = select(model).filter_by(workspace_id=role.workspace_id, **filters)
    row = await session.scalar(stmt)
    assert row is not None, f"No {model.__name__} with {filters}"
    return row.id
