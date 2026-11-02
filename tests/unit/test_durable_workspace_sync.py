"""Durability boundaries for the workspace sync operation lifecycle."""

import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio import workflow
from temporalio.exceptions import ApplicationError
from temporalio.testing import ActivityEnvironment

from tests.support.fake_vcs import FakeVcsServer
from tracecat.auth.types import Role
from tracecat.background.sandbox import new_sandbox_runner
from tracecat.db.models import (
    Action,
    WorkflowDefinition,
    Workspace,
    WorkspaceSyncOperation,
    WorkspaceVariable,
)
from tracecat.dsl.common import DSLEntrypoint, DSLInput
from tracecat.dsl.schemas import ActionStatement
from tracecat.exceptions import ScopeDeniedError, TracecatNotFoundError
from tracecat.git.types import GitUrl
from tracecat.pagination import PageParams
from tracecat.registry.lock.types import RegistryLock
from tracecat.sync import PullResourceDiff, PullResult
from tracecat.workflow.store.schemas import WorkflowSyncPullRequest
from tracecat.workspace_sync.enums import VcsProvider
from tracecat.workspace_sync.operations import activities, storage
from tracecat.workspace_sync.operations.domain import DurableSyncService
from tracecat.workspace_sync.operations.schemas import (
    PreparedSync,
    SyncOperationCreate,
    SyncPushResult,
)
from tracecat.workspace_sync.operations.service import SyncOperationService
from tracecat.workspace_sync.operations.types import (
    SyncOperationConflictError,
    SyncOperationRef,
)
from tracecat.workspace_sync.operations.workflows import WorkspaceSyncWorkflow
from tracecat.workspace_sync.schemas import (
    VariableResourceSpec,
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


@pytest.mark.anyio
async def test_start_lost_response_reuses_same_operation(
    session: AsyncSession, svc_role: Role
):
    service = SyncOperationService(session, svc_role)
    inputs = push_inputs()
    first = await service.create(inputs)
    second = await service.create(inputs)
    assert first.id == second.id
    assert second.status == "queued"
    operations = await service.list(PageParams(limit=10))
    assert len(operations.items) == 1
    with pytest.raises(SyncOperationConflictError):
        await service.create(inputs.model_copy(update={"compare_ref": "different"}))


@pytest.mark.anyio
async def test_operation_is_not_readable_by_another_actor(
    session: AsyncSession, svc_role: Role
):
    service = SyncOperationService(session, svc_role)
    operation = await service.create(push_inputs())
    other = SyncOperationService(
        session, svc_role.model_copy(update={"user_id": uuid.uuid4()})
    )
    with pytest.raises(TracecatNotFoundError):
        await other.get(operation.id)
    assert not (await other.list(PageParams())).items


@pytest.mark.anyio
async def test_duplicate_confirmation_does_not_schedule_a_second_apply(
    session: AsyncSession, svc_role: Role
):
    service = SyncOperationService(session, svc_role)
    operation = await service.create(push_inputs())
    operation.status = "ready"
    await session.commit()
    await service.apply(operation.id)
    second = await service.apply(operation.id)
    assert second.status == "applying"
    assert second.attempt == 0


@pytest.mark.anyio
async def test_expired_preview_cannot_apply(session: AsyncSession, svc_role: Role):
    service = SyncOperationService(session, svc_role)
    operation = await service.create(push_inputs())
    operation.status = "ready"
    operation.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    await session.commit()
    assert service.read(operation).status == "expired"
    with pytest.raises(SyncOperationConflictError):
        await service.apply(operation.id)


@pytest.mark.anyio
async def test_committed_receipt_prevents_activity_reexecution(monkeypatch):
    operation = WorkspaceSyncOperation(status="completed")
    session = AsyncMock()

    @asynccontextmanager
    async def context(_ref):
        yield operation, session

    monkeypatch.setattr(activities, "receipt_context", context)
    role = Role(type="user", service_id="tracecat-api", user_id=uuid.uuid4())
    await ActivityEnvironment().run(
        activities.workspace_sync_apply, SyncOperationRef(uuid.uuid4(), role, "apply")
    )
    session.commit.assert_not_awaited()


@pytest.mark.anyio
async def test_worker_workflow_passes_sandbox_validation():
    definition = workflow._Definition.must_from_class(WorkspaceSyncWorkflow)
    new_sandbox_runner().prepare_workflow(definition)


def test_direction_contract_requires_correct_input():
    with pytest.raises(ValidationError):
        SyncOperationCreate(id=uuid.uuid4(), direction="pull", push=push_inputs().push)


@pytest.mark.anyio
async def test_pull_receipt_and_resource_writes_share_transaction(
    session: AsyncSession, svc_role: Role
):
    service = SyncOperationService(session, svc_role)
    operation = await service.create(push_inputs())
    operation_id = operation.id
    operation.status = "applying"
    await session.commit()
    sync = WorkspaceSyncService(session, svc_role)
    snapshot = WorkspaceRemoteSnapshot(
        commit_sha="a" * 40,
        files={},
        spec=WorkspaceSpec(
            variables={
                "example": VariableResourceSpec(
                    id="example", name="example", environment="default"
                )
            }
        ),
    )
    result = await sync._import_snapshot(snapshot, sync_schedules=False, commit=False)
    assert result.success
    operation.result = {"success": True}
    await session.flush()
    # A crash before the outer commit discards both the import and its receipt.
    await session.rollback()
    operation = await service.get(operation_id)
    assert operation.status == "applying"
    assert operation.result is None
    assert (
        await session.scalar(
            select(WorkspaceVariable).where(
                WorkspaceVariable.workspace_id == svc_role.workspace_id,
                WorkspaceVariable.name == "example",
            )
        )
        is None
    )
    result = await sync._import_snapshot(snapshot, sync_schedules=False, commit=False)
    assert result.success
    operation.result = {"success": True}
    await session.commit()
    assert (await service.get(operation_id)).result == {"success": True}
    assert (
        await session.scalar(
            select(WorkspaceVariable).where(
                WorkspaceVariable.workspace_id == svc_role.workspace_id,
                WorkspaceVariable.name == "example",
            )
        )
        is not None
    )


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


@pytest.mark.anyio
async def test_saved_preview_requires_current_resource_read_scope(
    session: AsyncSession, svc_role: Role
):
    service = SyncOperationService(session, svc_role)
    operation = await service.create(push_inputs())
    operation.summary = {"read_scopes": ["variable:read"]}
    await session.commit()
    restricted = SyncOperationService(
        session,
        svc_role.model_copy(update={"scopes": frozenset({"workspace_sync:sync"})}),
    )
    with pytest.raises(ScopeDeniedError):
        await restricted.get(operation.id)


@pytest.mark.anyio
async def test_validation_failure_requires_new_preview(
    session: AsyncSession, svc_role: Role
):
    service = SyncOperationService(session, svc_role)
    operation = await service.create(push_inputs())
    operation.status = "failed"
    operation.summary = {"retryable": False}
    await session.commit()
    assert not service.read(operation).can_retry
    with pytest.raises(SyncOperationConflictError):
        await service.retry(operation.id)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("error", "retryable"),
    [
        (RuntimeError("synthetic-private-content"), True),
        (
            ScopeDeniedError(
                required_scopes=["variable:read"], missing_scopes=["variable:read"]
            ),
            False,
        ),
    ],
)
async def test_activity_failures_do_not_serialize_original_exception(error, retryable):
    async def fail():
        raise error

    with pytest.raises(ApplicationError) as result:
        await activities.safe_activity(fail)()
    assert "synthetic-private-content" not in str(result.value)
    assert result.value.__cause__ is None
    assert result.value.__context__ is None
    assert result.value.non_retryable is not retryable


@pytest.mark.anyio
@pytest.mark.parametrize("changed_after_preview", [None, "workspace", "branch"])
async def test_prepared_push_uses_fenced_apply_and_durable_receipt(
    svc_role: Role, monkeypatch, changed_after_preview
):
    async with SyncOperationService.with_session(svc_role) as service:
        session = service.session
        server = FakeVcsServer()
        url = GitUrl(host="github.com", org="example", repo="sync-test")
        workspace = await session.scalar(
            select(Workspace).where(Workspace.id == svc_role.workspace_id)
        )
        assert workspace is not None
        workspace.settings = {
            "git_repo_url": url.to_url(),
            "git_provider": VcsProvider.GITHUB,
        }
        variable = WorkspaceVariable(
            workspace_id=svc_role.workspace_id,
            name="example",
            environment="default",
            values={"value": "original"},
        )
        session.add(variable)
        await session.commit()
        objects: dict[str, bytes] = {}

        async def upload(content: bytes, key: str) -> None:
            objects[key] = content

        async def download(key: str) -> bytes:
            return objects[key]

        monkeypatch.setattr(storage, "_upload", upload)
        monkeypatch.setattr(storage, "_download", download)
        monkeypatch.setattr(
            activities, "refresh_sync_role", AsyncMock(return_value=svc_role)
        )
        monkeypatch.setattr(
            "tracecat.workspace_sync.service.vcs_transport_for_provider",
            server.transport_factory,
        )
        service = SyncOperationService(session, svc_role)
        inputs = push_inputs()
        operation = await service.create(inputs)
        # A queue outage must not consume the confirmation window before the
        # worker has prepared anything for the user to review.
        operation.expires_at = datetime.now(UTC) - timedelta(days=1)
        await session.commit()
        environment = ActivityEnvironment()
        await environment.run(
            activities.workspace_sync_prepare,
            SyncOperationRef(operation.id, svc_role, "preview"),
        )
        await session.refresh(operation)
        assert operation.status == "ready"
        assert operation.expires_at > datetime.now(UTC) + timedelta(hours=23)
        if changed_after_preview == "workspace":
            variable.description = "Edited after preview"
            await session.commit()
        elif changed_after_preview == "branch":
            transport = server.transport_factory(
                VcsProvider.GITHUB, session=session, role=svc_role
            )
            await transport.write_files(
                url=url,
                files={"variables/unreviewed.yml": "changed"},
                message="Concurrent change",
                branch="main",
                create_pr=False,
            )
        await service.apply(operation.id)
        ref = SyncOperationRef(operation.id, svc_role, "apply")
        if changed_after_preview:
            with pytest.raises(ApplicationError) as failure:
                await environment.run(activities.workspace_sync_apply, ref)
            assert failure.value.type == "StaleSyncPreviewError"
            transport = server.transport_factory(
                VcsProvider.GITHUB, session=session, role=svc_role
            )
            branches = await transport.list_branches(url=url, limit=100)
            assert "sync/test" not in {branch.name for branch in branches}
            return
        await environment.run(activities.workspace_sync_apply, ref)
        await session.refresh(operation)
        assert operation.status == "completed"
        transport = server.transport_factory(
            VcsProvider.GITHUB, session=session, role=svc_role
        )
        first = await transport.list_commits(url=url, branch="sync/test")
        await environment.run(activities.workspace_sync_apply, ref)
        assert await transport.list_commits(url=url, branch="sync/test") == first
        # Simulate a write whose acknowledgement/DB receipt was lost. The same
        # prepared files are recognized, so a resumed attempt adds no second commit.
        operation.status = "applying"
        operation.result = None
        await session.commit()
        await environment.run(activities.workspace_sync_apply, ref)
        await session.refresh(operation)
        assert operation.status == "completed"
        assert await transport.list_commits(url=url, branch="sync/test") == first

        # A later unrelated write must not hide this operation's completed commit.
        await transport.write_files(
            url=url,
            files={"README.md": "Unreviewed external update"},
            message="External update",
            branch="sync/test",
            create_pr=False,
        )
        operation.status = "applying"
        operation.result = None
        await session.commit()
        history = await transport.list_commits(url=url, branch="sync/test")
        await environment.run(activities.workspace_sync_apply, ref)
        await session.refresh(operation)
        assert operation.status == "completed"
        result = operation.result
        assert result is not None
        assert set(result) == {"commit"}
        receipt = SyncPushResult.model_validate(result)
        assert receipt.commit.sha == first[0].sha
        assert await transport.list_commits(url=url, branch="sync/test") == history
