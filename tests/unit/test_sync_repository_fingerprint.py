"""Git identity guards ignore unrelated workspace settings."""

from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select

from tests.support.fake_vcs import FakeVcsServer
from tests.unit.test_durable_workspace_sync import push_inputs
from tracecat.db.models import Workspace
from tracecat.git.types import GitUrl
from tracecat.workspace_sync.enums import VcsProvider
from tracecat.workspace_sync.operations.domain import DurableSyncService


@pytest.mark.anyio
async def test_repository_guard_tracks_only_git_settings(session, svc_role):
    workspace = await session.scalar(
        select(Workspace).where(Workspace.id == svc_role.workspace_id)
    )
    assert workspace is not None
    workspace.settings = {
        "git_repo_url": "git+ssh://git@github.com/example/sync.git",
        "git_provider": "github",
    }
    sync = DurableSyncService(session, svc_role)
    before = await sync.repository_fingerprint()
    workspace.settings = {
        **workspace.settings,
        "workflow_timeout": 123,
        "allow_attachments": False,
    }
    assert await sync.repository_fingerprint() == before
    settings = dict(workspace.settings)
    workspace.settings = {
        **settings,
        "git_repo_url": "git+ssh://git@github.com/example/other.git",
    }
    assert await sync.repository_fingerprint() != before
    workspace.settings = {**settings, "git_provider": "gitlab"}
    assert await sync.repository_fingerprint() != before


@pytest.mark.anyio
async def test_hex_branch_preview_uses_explicit_branch_kind(
    session, svc_role, monkeypatch
):
    server = FakeVcsServer()
    url = GitUrl(host="github.com", org="example", repo="sync")
    branch = "b" * 40
    transport = server.transport_factory(VcsProvider.GITHUB, session=None, role=None)
    await transport.write_files(
        url=url,
        files={"README.md": "base"},
        branch=branch,
        message="Synthetic",
        create_pr=False,
    )
    read = AsyncMock(wraps=transport.read_files)
    monkeypatch.setattr(transport, "read_files", read)
    sync = DurableSyncService(session, svc_role)
    monkeypatch.setattr(sync, "_transport_for_provider", lambda: transport)
    monkeypatch.setattr(sync, "_workspace_git_url", AsyncMock(return_value=url))
    monkeypatch.setattr(sync, "repository_fingerprint", AsyncMock(return_value="repo"))
    monkeypatch.setattr(sync, "require_entitlement", AsyncMock())
    inputs = push_inputs()
    assert inputs.push is not None
    inputs.push.branch = branch
    await sync.fetch_remote(inputs)
    read.assert_awaited_once_with(url=url, ref=branch, ref_kind="branch")
