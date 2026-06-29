"""Tests for admin registry service behavior."""

from __future__ import annotations

import uuid
from datetime import timedelta
from typing import cast
from unittest.mock import AsyncMock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.admin.registry.schemas import RegistryArtifactsBackfillStartRequest
from tracecat.admin.registry.service import AdminRegistryService
from tracecat.auth.types import PlatformRole
from tracecat.db.models import (
    PlatformRegistryRepository,
    PlatformRegistryVersion,
    Workflow,
    WorkflowDefinition,
    Workspace,
)
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture
def platform_role() -> PlatformRole:
    return PlatformRole(
        type="user",
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )


@pytest.mark.anyio
async def test_list_versions_includes_artifact_and_usage_status(
    session: AsyncSession,
    svc_workspace: Workspace,
    platform_role: PlatformRole,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = PlatformRegistryRepository(origin="tracecat_registry")
    session.add(repo)
    await session.flush()

    version_in_definition = PlatformRegistryVersion(
        repository_id=repo.id,
        version="1.0.0",
        manifest={"schema_version": "1.0", "actions": {}},
        tarball_uri="s3://registry-artifacts/platform/v1/site-packages.tar.gz",
    )
    current_version = PlatformRegistryVersion(
        repository_id=repo.id,
        version="2.0.0",
        manifest={"schema_version": "1.0", "actions": {}},
        tarball_uri="s3://registry-artifacts/platform/v2/site-packages.tar.gz",
    )
    session.add_all([version_in_definition, current_version])
    await session.flush()

    repo.current_version_id = current_version.id
    session.add(repo)

    workflow = Workflow(
        workspace_id=svc_workspace.id,
        title="Workflow using old registry version",
        description="",
    )
    session.add(workflow)
    await session.flush()

    session.add_all(
        [
            WorkflowDefinition(
                workflow_id=workflow.id,
                workspace_id=svc_workspace.id,
                version=1,
                content={},
                registry_lock={
                    "origins": {"tracecat_registry": "1.0.0"},
                    "actions": {},
                },
            ),
            WorkflowDefinition(
                workflow_id=workflow.id,
                workspace_id=svc_workspace.id,
                version=2,
                content={},
                registry_lock={
                    "origins": {"tracecat_registry": "2.0.0"},
                    "actions": {},
                },
            ),
        ]
    )
    malformed_lock_workflow = Workflow(
        workspace_id=svc_workspace.id,
        title="Workflow with malformed registry lock",
        description="",
    )
    session.add(malformed_lock_workflow)
    await session.flush()
    session.add(
        WorkflowDefinition(
            workflow_id=malformed_lock_workflow.id,
            workspace_id=svc_workspace.id,
            version=1,
            content={},
            registry_lock={
                "origins": ["tracecat_registry"],
                "actions": {},
            },
        )
    )
    await session.commit()

    async def fake_artifacts_ready(
        _service: AdminRegistryService,
        tarball_uri: str | None,
    ) -> bool:
        if tarball_uri is None:
            return False
        return "v2" in tarball_uri

    monkeypatch.setattr(
        AdminRegistryService,
        "_artifacts_ready",
        fake_artifacts_ready,
    )

    service = AdminRegistryService(session, platform_role)
    versions = await service.list_versions(limit=10)
    versions_by_number = {version.version: version for version in versions}

    old_version = versions_by_number["1.0.0"]
    assert old_version.workflow_definition_count == 0
    assert old_version.in_use is False
    assert old_version.is_current is False
    assert old_version.artifacts_ready is False

    new_version = versions_by_number["2.0.0"]
    assert new_version.workflow_definition_count == 1
    assert new_version.in_use is True
    assert new_version.is_current is True
    assert new_version.artifacts_ready is True


@pytest.mark.anyio
async def test_artifacts_ready_accepts_direct_squashfs_uri(
    session: AsyncSession,
    platform_role: PlatformRole,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    checked: dict[str, str] = {}

    async def fake_file_exists(*, key: str, bucket: str) -> bool:
        checked["key"] = key
        checked["bucket"] = bucket
        return True

    monkeypatch.setattr(
        "tracecat.admin.registry.service.blob.file_exists",
        fake_file_exists,
    )

    service = AdminRegistryService(session, platform_role)
    ready = await service._artifacts_ready(
        "s3://registry-artifacts/platform/v1/site-packages.squashfs"
    )

    assert ready is True
    assert checked == {
        "bucket": "registry-artifacts",
        "key": "platform/v1/site-packages.squashfs",
    }


@pytest.mark.anyio
async def test_promote_version_requires_ready_artifact(
    session: AsyncSession,
    platform_role: PlatformRole,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = PlatformRegistryRepository(origin="tracecat_registry")
    session.add(repo)
    await session.flush()

    version = PlatformRegistryVersion(
        repository_id=repo.id,
        version="1.0.0",
        manifest={"schema_version": "1.0", "actions": {}},
        tarball_uri="s3://registry-artifacts/platform/v1/site-packages.squashfs",
    )
    session.add(version)
    await session.commit()

    async def artifact_not_ready(
        _service: AdminRegistryService,
        _artifact_uri: str | None,
    ) -> str | None:
        return None

    monkeypatch.setattr(
        AdminRegistryService,
        "_ready_execution_artifact_uri",
        artifact_not_ready,
    )

    service = AdminRegistryService(session, platform_role)
    with pytest.raises(TracecatValidationError, match="artifact is not ready"):
        await service.promote_version(repo.id, version.id)

    await session.refresh(repo)
    assert repo.current_version_id is None


@pytest.mark.anyio
async def test_promote_version_allows_ready_legacy_tarball_artifact(
    session: AsyncSession,
    platform_role: PlatformRole,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = PlatformRegistryRepository(origin="tracecat_registry")
    session.add(repo)
    await session.flush()

    version = PlatformRegistryVersion(
        repository_id=repo.id,
        version="1.0.0",
        manifest={"schema_version": "1.0", "actions": {}},
        tarball_uri="s3://registry-artifacts/platform/v1/site-packages.tar.gz",
    )
    session.add(version)
    await session.commit()

    checked: dict[str, str] = {}

    async def fake_file_exists(*, key: str, bucket: str) -> bool:
        checked["key"] = key
        checked["bucket"] = bucket
        return True

    monkeypatch.setattr(
        "tracecat.admin.registry.service.blob.file_exists",
        fake_file_exists,
    )

    service = AdminRegistryService(session, platform_role)
    response = await service.promote_version(repo.id, version.id)

    await session.refresh(repo)
    await session.refresh(version)
    assert response.current_version_id == version.id
    assert repo.current_version_id == version.id
    assert (
        version.tarball_uri
        == "s3://registry-artifacts/platform/v1/site-packages.tar.gz"
    )
    assert checked == {
        "bucket": "registry-artifacts",
        "key": "platform/v1/site-packages.tar.gz",
    }


@pytest.mark.anyio
async def test_promote_version_allows_backfilled_legacy_squashfs_sidecar(
    session: AsyncSession,
    platform_role: PlatformRole,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = PlatformRegistryRepository(origin="tracecat_registry")
    session.add(repo)
    await session.flush()

    version = PlatformRegistryVersion(
        repository_id=repo.id,
        version="1.0.0",
        manifest={"schema_version": "1.0", "actions": {}},
        tarball_uri="s3://registry-artifacts/platform/v1/site-packages.tar.gz",
    )
    session.add(version)
    await session.commit()

    checked: list[tuple[str, str]] = []

    async def fake_file_exists(*, key: str, bucket: str) -> bool:
        checked.append((bucket, key))
        return key == "platform/v1/site-packages.squashfs"

    monkeypatch.setattr(
        "tracecat.admin.registry.service.blob.file_exists",
        fake_file_exists,
    )

    service = AdminRegistryService(session, platform_role)
    response = await service.promote_version(repo.id, version.id)

    await session.refresh(repo)
    await session.refresh(version)
    assert response.current_version_id == version.id
    assert repo.current_version_id == version.id
    assert (
        version.tarball_uri
        == "s3://registry-artifacts/platform/v1/site-packages.squashfs"
    )
    assert checked == [
        ("registry-artifacts", "platform/v1/site-packages.tar.gz"),
        ("registry-artifacts", "platform/v1/site-packages.squashfs"),
    ]


@pytest.mark.anyio
async def test_start_artifacts_backfill_scales_workflow_timeout(
    session: AsyncSession,
    platform_role: PlatformRole,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    repo = PlatformRegistryRepository(origin="tracecat_registry")
    session.add(repo)
    await session.flush()

    versions = [
        PlatformRegistryVersion(
            repository_id=repo.id,
            version=f"1.0.{idx}",
            manifest={"schema_version": "1.0", "actions": {}},
            tarball_uri=f"s3://registry-artifacts/platform/v{idx}/site-packages.tar.gz",
        )
        for idx in range(3)
    ]
    session.add_all(versions)
    await session.commit()

    fake_client = AsyncMock()

    async def fake_get_temporal_client() -> AsyncMock:
        return fake_client

    monkeypatch.setattr(
        "tracecat.dsl.client.get_temporal_client",
        fake_get_temporal_client,
    )

    service = AdminRegistryService(session, platform_role)
    response = await service.start_artifacts_backfill(
        RegistryArtifactsBackfillStartRequest(
            version_ids=[version.id for version in versions],
        )
    )

    assert response.requested_count == 3
    fake_client.start_workflow.assert_awaited_once()
    assert fake_client.start_workflow.await_args.kwargs[
        "execution_timeout"
    ] == timedelta(minutes=135)


@pytest.mark.anyio
async def test_start_artifacts_backfill_missing_version_raises_not_found(
    session: AsyncSession,
    platform_role: PlatformRole,
) -> None:
    service = AdminRegistryService(session, platform_role)
    missing_version_id = uuid.uuid4()

    with pytest.raises(TracecatNotFoundError, match="Registry versions not found"):
        await service.start_artifacts_backfill(
            RegistryArtifactsBackfillStartRequest(version_ids=[missing_version_id])
        )


@pytest.mark.anyio
async def test_start_artifacts_backfill_version_without_tarball_raises_validation(
    platform_role: PlatformRole,
) -> None:
    version_id = uuid.uuid4()
    version = PlatformRegistryVersion(
        repository_id=uuid.uuid4(),
        version="1.0.0",
        manifest={"schema_version": "1.0", "actions": {}},
        tarball_uri="s3://registry-artifacts/platform/v1/site-packages.tar.gz",
    )
    object.__setattr__(version, "id", version_id)
    object.__setattr__(version, "tarball_uri", None)

    class FakeScalars:
        def all(self) -> list[PlatformRegistryVersion]:
            return [version]

    class FakeResult:
        def scalars(self) -> FakeScalars:
            return FakeScalars()

    session = AsyncMock(spec=AsyncSession)
    session.execute.return_value = FakeResult()

    service = AdminRegistryService(cast(AsyncSession, session), platform_role)
    with pytest.raises(
        TracecatValidationError, match="Registry versions do not have tarballs"
    ):
        await service.start_artifacts_backfill(
            RegistryArtifactsBackfillStartRequest(version_ids=[version_id])
        )
