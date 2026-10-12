"""Durability boundaries for the workspace sync operation lifecycle."""

import uuid
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import (
    Action,
    WorkflowDefinition,
)
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.git.types import GitUrl
from tracecat.registry.lock.types import RegistryLock
from tracecat.sync import PullResourceDiff, PullResult
from tracecat.workflow.store.schemas import WorkflowSyncPullRequest
from tracecat.workspace_sync.operations import storage
from tracecat.workspace_sync.operations.domain import DurableSyncService
from tracecat.workspace_sync.operations.schemas import PreparedSync, SyncOperationCreate
from tracecat.workspace_sync.schemas import (
    WorkflowResourceSpec,
    WorkspaceRemoteSnapshot,
    WorkspaceSpec,
    WorkspaceSyncExportPreview,
    WorkspaceSyncExportRequest,
)
from tracecat.workspace_sync.service import WorkspaceSyncService


def push_inputs() -> SyncOperationCreate:
    return SyncOperationCreate(
        id=uuid.uuid4(),
        direction="push",
        push=WorkspaceSyncExportRequest(
            message="Sync test resources", branch="sync/test", create_pr=True
        ),
    )


def prepared_diff(count: int) -> PreparedSync:
    return PreparedSync(
        preview=WorkspaceSyncExportPreview(
            resource_counts={},
            files=[],
            resource_diffs=[
                PullResourceDiff(
                    resource_type="variable",
                    source_id=f"item-{i}",
                    source_path=f"variables/item-{i}.yml",
                    change_type="modified",
                    title=None,
                    diff=f"@@ -1 +1 @@\n-before-{i}\n+after-{i}",
                )
                for i in range(count)
            ],
        ),
        workspace_fingerprint="local",
        repository_fingerprint="repository",
        compare_ref="main",
        compare_sha="a" * 40,
    )


@pytest.mark.anyio
async def test_diff_pages_do_not_download_snapshot_or_other_file_contents(monkeypatch):
    objects: dict[str, bytes] = {}
    reads: list[str] = []

    async def upload(content: bytes, key: str) -> None:
        objects[key] = content

    async def download(key: str) -> bytes:
        reads.append(key)
        return objects[key]

    monkeypatch.setattr(storage, "_upload", upload)
    monkeypatch.setattr(storage, "_download", download)
    key = await storage.store_prepared(
        "workspace-sync/synthetic/test", prepared_diff(101)
    )
    first = await storage.read_diff_page(key, None)
    second = await storage.read_diff_page(key, first.next_cursor)
    third = await storage.read_diff_page(key, second.next_cursor)
    assert [len(page.items) for page in [first, second, third]] == [50, 50, 1]
    assert third.next_cursor is None
    assert all(item.diff == "" for item in first.items)
    assert key not in reads
    diff = await storage.read_diff(key, 100)
    assert "+after-100" in diff.diff
    assert reads[-1] == f"{key}.diff-100"
    prepared = await storage.load_prepared(key)
    assert prepared.preview.resource_diffs
    assert all(diff.diff == "" for diff in prepared.preview.resource_diffs)
    assert prepared.preview.resource_diffs[100].source_id == "item-100"


@pytest.mark.anyio
@pytest.mark.parametrize("cursor", ["invalid!", "LTE=", "MQ==", "x" * 40])
async def test_diff_rejects_invalid_cursor_before_storage(cursor, monkeypatch):
    download = AsyncMock()
    monkeypatch.setattr(storage, "_download", download)
    with pytest.raises(ValueError):
        await storage.read_diff_page("test", cursor)
    download.assert_not_awaited()


def test_direction_contract_requires_correct_input():
    with pytest.raises(ValidationError):
        SyncOperationCreate(id=uuid.uuid4(), direction="pull", push=push_inputs().push)


@pytest.mark.anyio
async def test_unchanged_pull_does_not_create_workflow_version(
    session: AsyncSession, svc_role: Role, monkeypatch
):
    monkeypatch.setattr(
        "tracecat.workflow.management.management.RegistryLockService.resolve_lock_with_bindings",
        AsyncMock(return_value=RegistryLock(origins={}, actions={})),
    )
    sync = WorkspaceSyncService(session, svc_role)
    snapshot = WorkspaceRemoteSnapshot(
        commit_sha="a" * 40,
        files={},
        spec=WorkspaceSpec(
            workflows={
                "example": WorkflowResourceSpec(
                    id="example",
                    definition=DSLInput(
                        title="Example",
                        description="Synthetic test workflow",
                        entrypoint=DSLEntrypoint(ref="reshape"),
                        actions=[
                            ActionStatement(
                                ref="reshape",
                                action="core.transform.reshape",
                                args={"value": "test"},
                            )
                        ],
                    ),
                )
            }
        ),
    )
    first = await sync._import_snapshot(snapshot, sync_schedules=False)
    assert first.success
    version_count = await session.scalar(
        select(func.count())
        .select_from(WorkflowDefinition)
        .where(WorkflowDefinition.workspace_id == svc_role.workspace_id)
    )
    repeated = await sync._import_snapshot(
        snapshot, sync_schedules=False, changed_resources=set()
    )
    assert repeated.success
    assert repeated.workflows_imported == 0
    assert (
        await session.scalar(
            select(func.count())
            .select_from(WorkflowDefinition)
            .where(WorkflowDefinition.workspace_id == svc_role.workspace_id)
        )
        == version_count
    )


@pytest.mark.anyio
async def test_confirmed_pull_restores_draft_even_when_published_diff_is_empty(
    session: AsyncSession, svc_role: Role, monkeypatch
):
    monkeypatch.setattr(
        "tracecat.workflow.management.management.RegistryLockService.resolve_lock_with_bindings",
        AsyncMock(return_value=RegistryLock(origins={}, actions={})),
    )
    sync = DurableSyncService(session, svc_role)
    snapshot = WorkspaceRemoteSnapshot(
        commit_sha="a" * 40,
        files={},
        spec=WorkspaceSpec(
            workflows={
                "example": WorkflowResourceSpec(
                    id="example",
                    definition=DSLInput(
                        title="Example",
                        description="Synthetic workflow",
                        entrypoint=DSLEntrypoint(ref="reshape"),
                        actions=[
                            ActionStatement(
                                ref="reshape",
                                action="core.transform.reshape",
                                args={"value": "published"},
                            )
                        ],
                    ),
                )
            }
        ),
    )
    assert (await sync._import_snapshot(snapshot, sync_schedules=False)).success
    action = await session.scalar(
        select(Action).where(Action.workspace_id == svc_role.workspace_id)
    )
    assert action is not None
    action.inputs = "value: unpublished-draft"
    await session.commit()
    monkeypatch.setattr(sync, "require_entitlement", AsyncMock())
    monkeypatch.setattr(sync, "repository_fingerprint", AsyncMock(return_value="repo"))
    monkeypatch.setattr(sync, "local_fingerprint", AsyncMock(return_value="local"))
    monkeypatch.setattr(
        sync,
        "_workspace_git_url",
        AsyncMock(
            return_value=GitUrl(host="github.com", org="example", repo="sync-test")
        ),
    )
    prepared = PreparedSync(
        snapshot=snapshot,
        preview=PullResult(
            success=True,
            commit_sha=snapshot.commit_sha,
            workflows_found=1,
            workflows_imported=0,
            diagnostics=[],
            message="No published changes",
            resource_diffs=[],
        ),
        workspace_fingerprint="local",
        repository_fingerprint="repo",
        compare_ref=snapshot.commit_sha,
        compare_sha=snapshot.commit_sha,
    )
    result = await sync.apply(
        SyncOperationCreate(
            id=uuid.uuid4(),
            direction="pull",
            pull=WorkflowSyncPullRequest(commit_sha=snapshot.commit_sha),
        ),
        prepared,
        uuid.uuid4(),
    )
    assert isinstance(result, PullResult) and result.success
    await session.commit()
    restored = await session.scalar(
        select(Action.inputs).where(Action.workspace_id == svc_role.workspace_id)
    )
    assert restored is not None
    assert "published" in restored and "unpublished-draft" not in restored
