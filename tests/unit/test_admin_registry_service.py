"""Tests for admin registry service behavior."""

from __future__ import annotations

import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.admin.registry.service import AdminRegistryService
from tracecat.auth.types import PlatformRole
from tracecat.db.models import (
    PlatformRegistryRepository,
    PlatformRegistryVersion,
    Workflow,
    WorkflowDefinition,
    Workspace,
)

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

    session.add(
        WorkflowDefinition(
            workflow_id=workflow.id,
            workspace_id=svc_workspace.id,
            version=1,
            content={},
            registry_lock={
                "origins": {"tracecat_registry": "1.0.0"},
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
    assert old_version.workflow_definition_count == 1
    assert old_version.in_use is True
    assert old_version.is_current is False
    assert old_version.artifacts_ready is False

    new_version = versions_by_number["2.0.0"]
    assert new_version.workflow_definition_count == 0
    assert new_version.in_use is True
    assert new_version.is_current is True
    assert new_version.artifacts_ready is True
