"""Tests for registry version management.

This test suite validates:
1. Version deletion with safety checks
2. Version comparison (diff)
3. Previous version retrieval for rollback
4. Workflow definition usage detection
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import (
    RegistryRepository,
    RegistryVersion,
    Workflow,
    WorkflowDefinition,
)
from tracecat.exceptions import RegistryError
from tracecat.registry.repositories.service import RegistryReposService
from tracecat.registry.versions.service import RegistryVersionsService

pytestmark = pytest.mark.usefixtures("db")


def _make_manifest(action_names: list[str]) -> dict:
    """Create a test manifest with given action names."""
    actions = {}
    for name in action_names:
        parts = name.rsplit(".", 1)
        namespace = parts[0] if len(parts) > 1 else "test"
        action_name = parts[-1]
        actions[name] = {
            "namespace": namespace,
            "name": action_name,
            "action_type": "udf",
            "description": f"Test action {name}",
            "interface": {"expects": {"input": "str"}, "returns": "str"},
            "implementation": {
                "type": "udf",
                "url": "test_origin",
                "module": f"test.{namespace}",
                "name": action_name,
            },
        }
    return {"schema_version": "1.0", "actions": actions}


@pytest.mark.anyio
async def test_delete_version_success(
    svc_admin_role: Role,
    session: AsyncSession,
) -> None:
    """Test that deleting a non-current version without references works."""
    # Create a repository
    repo = RegistryRepository(
        organization_id=svc_admin_role.organization_id,
        origin="test_origin",
    )
    session.add(repo)
    await session.flush()

    # Create two versions
    version1 = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action_v1"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    version2 = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="2.0.0",
        manifest=_make_manifest(["test.action_v2"]),
        tarball_uri="s3://test/v2.tar.gz",
    )
    session.add_all([version1, version2])
    await session.flush()

    # Set version 2 as current
    repo.current_version_id = version2.id
    session.add(repo)
    await session.commit()

    # Validate and delete version 1
    repos_service = RegistryReposService(session, role=svc_admin_role)
    versions_service = RegistryVersionsService(session, role=svc_admin_role)

    # Validation should pass for non-current version
    await repos_service.validate_version_deletion(repo, version1)

    # Delete version 1
    await versions_service.delete_version(version1)

    # Verify version 1 is deleted
    deleted = await versions_service.get_version(version1.id)
    assert deleted is None

    # Verify version 2 still exists
    remaining = await versions_service.get_version(version2.id)
    assert remaining is not None


@pytest.mark.anyio
async def test_cannot_delete_current_version(
    svc_admin_role: Role,
    session: AsyncSession,
) -> None:
    """Test that deleting the current version is blocked."""
    # Create a repository
    repo = RegistryRepository(
        organization_id=svc_admin_role.organization_id,
        origin="test_origin",
    )
    session.add(repo)
    await session.flush()

    # Create version
    version = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    session.add(version)
    await session.flush()

    # Set as current version
    repo.current_version_id = version.id
    session.add(repo)
    await session.commit()

    # Try to validate deletion
    repos_service = RegistryReposService(session, role=svc_admin_role)

    with pytest.raises(RegistryError, match="currently promoted version"):
        await repos_service.validate_version_deletion(repo, version)


@pytest.mark.anyio
async def test_cannot_delete_version_in_use(
    svc_admin_role: Role,
    session: AsyncSession,
    svc_workspace,
) -> None:
    """Test that deleting a version referenced by published workflows is blocked."""
    workspace_id = svc_workspace.id

    # Create a repository
    repo = RegistryRepository(
        organization_id=svc_admin_role.organization_id,
        origin="test_origin",
    )
    session.add(repo)
    await session.flush()

    # Create versions
    version1 = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    version2 = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="2.0.0",
        manifest=_make_manifest(["test.action"]),
        tarball_uri="s3://test/v2.tar.gz",
    )
    session.add_all([version1, version2])
    await session.flush()

    # Set version 2 as current
    repo.current_version_id = version2.id
    session.add(repo)
    await session.flush()

    # Create a workflow that references version 1 in its registry_lock
    workflow = Workflow(
        workspace_id=workspace_id,
        title="Test Workflow",
        description="Test workflow for registry version testing",
    )
    session.add(workflow)
    await session.flush()

    # Create a workflow definition referencing version 1
    # Note: WorkflowDefinition uses workspace_id, not organization_id
    definition = WorkflowDefinition(
        workflow_id=workflow.id,
        workspace_id=workspace_id,
        version=1,
        content={},
        registry_lock={
            "origins": {"test_origin": "1.0.0"},
            "actions": {"test.action": "test_origin"},
        },
    )
    session.add(definition)
    await session.commit()

    # Try to validate deletion
    repos_service = RegistryReposService(session, role=svc_admin_role)

    with pytest.raises(RegistryError, match="in use by published workflows"):
        await repos_service.validate_version_deletion(repo, version1)


@pytest.mark.anyio
async def test_get_previous_version(
    svc_admin_role: Role,
    session: AsyncSession,
) -> None:
    """Test getting the previous version for rollback."""
    # Create a repository
    repo = RegistryRepository(
        organization_id=svc_admin_role.organization_id,
        origin="test_origin",
    )
    session.add(repo)
    await session.flush()

    # Create versions with different created_at times
    base_time = datetime.now(UTC)
    version1 = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action_v1"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    session.add(version1)
    await session.flush()

    # Manually set created_at to ensure ordering
    version1.created_at = base_time - timedelta(hours=2)
    session.add(version1)
    await session.flush()

    version2 = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="2.0.0",
        manifest=_make_manifest(["test.action_v2"]),
        tarball_uri="s3://test/v2.tar.gz",
    )
    session.add(version2)
    await session.flush()

    version2.created_at = base_time - timedelta(hours=1)
    session.add(version2)

    version3 = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="3.0.0",
        manifest=_make_manifest(["test.action_v3"]),
        tarball_uri="s3://test/v3.tar.gz",
    )
    session.add(version3)
    await session.flush()

    version3.created_at = base_time
    session.add(version3)
    await session.commit()

    # Test getting previous version
    versions_service = RegistryVersionsService(session, role=svc_admin_role)

    # Previous to version3 should be version2
    prev = await versions_service.get_previous_version(repo.id, version3.id)
    assert prev is not None
    assert prev.id == version2.id

    # Previous to version2 should be version1
    prev = await versions_service.get_previous_version(repo.id, version2.id)
    assert prev is not None
    assert prev.id == version1.id


@pytest.mark.anyio
async def test_get_previous_version_none(
    svc_admin_role: Role,
    session: AsyncSession,
) -> None:
    """Test that getting previous version returns None for the oldest version."""
    # Create a repository
    repo = RegistryRepository(
        organization_id=svc_admin_role.organization_id,
        origin="test_origin",
    )
    session.add(repo)
    await session.flush()

    # Create single version
    version = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    session.add(version)
    await session.commit()

    # Get previous version (should be None)
    versions_service = RegistryVersionsService(session, role=svc_admin_role)
    prev = await versions_service.get_previous_version(repo.id, version.id)
    assert prev is None


@pytest.mark.anyio
async def test_compare_versions_added(
    svc_admin_role: Role,
    session: AsyncSession,
) -> None:
    """Test that version diff correctly identifies added actions."""
    # Create a repository
    repo = RegistryRepository(
        organization_id=svc_admin_role.organization_id,
        origin="test_origin",
    )
    session.add(repo)
    await session.flush()

    # Version 1 with one action
    version1 = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action_a"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    # Version 2 with two actions (one added)
    version2 = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="2.0.0",
        manifest=_make_manifest(["test.action_a", "test.action_b"]),
        tarball_uri="s3://test/v2.tar.gz",
    )
    session.add_all([version1, version2])
    await session.commit()

    # Compare versions
    versions_service = RegistryVersionsService(session, role=svc_admin_role)
    diff = await versions_service.compare_versions(version1, version2)

    assert diff.base_version == "1.0.0"
    assert diff.compare_version == "2.0.0"
    assert "test.action_b" in diff.actions_added
    assert len(diff.actions_removed) == 0
    assert len(diff.actions_modified) == 0
    assert diff.total_changes == 1


@pytest.mark.anyio
async def test_compare_versions_removed(
    svc_admin_role: Role,
    session: AsyncSession,
) -> None:
    """Test that version diff correctly identifies removed actions."""
    # Create a repository
    repo = RegistryRepository(
        organization_id=svc_admin_role.organization_id,
        origin="test_origin",
    )
    session.add(repo)
    await session.flush()

    # Version 1 with two actions
    version1 = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action_a", "test.action_b"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    # Version 2 with one action (one removed)
    version2 = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="2.0.0",
        manifest=_make_manifest(["test.action_a"]),
        tarball_uri="s3://test/v2.tar.gz",
    )
    session.add_all([version1, version2])
    await session.commit()

    # Compare versions
    versions_service = RegistryVersionsService(session, role=svc_admin_role)
    diff = await versions_service.compare_versions(version1, version2)

    assert "test.action_b" in diff.actions_removed
    assert len(diff.actions_added) == 0
    assert len(diff.actions_modified) == 0
    assert diff.total_changes == 1


@pytest.mark.anyio
async def test_compare_versions_modified(
    svc_admin_role: Role,
    session: AsyncSession,
) -> None:
    """Test that version diff correctly identifies modified actions."""
    # Create a repository
    repo = RegistryRepository(
        organization_id=svc_admin_role.organization_id,
        origin="test_origin",
    )
    session.add(repo)
    await session.flush()

    # Version 1 with action
    manifest1 = _make_manifest(["test.action"])
    version1 = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="1.0.0",
        manifest=manifest1,
        tarball_uri="s3://test/v1.tar.gz",
    )

    # Version 2 with modified action (different interface)
    manifest2 = _make_manifest(["test.action"])
    manifest2["actions"]["test.action"]["interface"]["expects"] = {
        "input": "str",
        "extra": "int",
    }
    manifest2["actions"]["test.action"]["description"] = "Modified description"
    version2 = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="2.0.0",
        manifest=manifest2,
        tarball_uri="s3://test/v2.tar.gz",
    )
    session.add_all([version1, version2])
    await session.commit()

    # Compare versions
    versions_service = RegistryVersionsService(session, role=svc_admin_role)
    diff = await versions_service.compare_versions(version1, version2)

    assert len(diff.actions_added) == 0
    assert len(diff.actions_removed) == 0
    assert len(diff.actions_modified) == 1
    assert diff.actions_modified[0].action_name == "test.action"
    assert diff.actions_modified[0].description_changed is True
    assert len(diff.actions_modified[0].interface_changes) > 0
    assert diff.total_changes == 1


@pytest.mark.anyio
async def test_compare_versions_no_changes(
    svc_admin_role: Role,
    session: AsyncSession,
) -> None:
    """Test that version diff returns empty when versions are identical."""
    # Create a repository
    repo = RegistryRepository(
        organization_id=svc_admin_role.organization_id,
        origin="test_origin",
    )
    session.add(repo)
    await session.flush()

    # Create two versions with identical manifests
    manifest = _make_manifest(["test.action"])
    version1 = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="1.0.0",
        manifest=manifest,
        tarball_uri="s3://test/v1.tar.gz",
    )
    version2 = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="1.0.1",
        manifest=manifest.copy(),  # Same manifest
        tarball_uri="s3://test/v2.tar.gz",
    )
    session.add_all([version1, version2])
    await session.commit()

    # Compare versions
    versions_service = RegistryVersionsService(session, role=svc_admin_role)
    diff = await versions_service.compare_versions(version1, version2)

    assert diff.total_changes == 0
    assert len(diff.actions_added) == 0
    assert len(diff.actions_removed) == 0
    assert len(diff.actions_modified) == 0


@pytest.mark.anyio
async def test_get_workflow_definitions_using_version(
    svc_admin_role: Role,
    session: AsyncSession,
    svc_workspace,
) -> None:
    """Test finding workflow definitions that reference a specific version."""
    workspace_id = svc_workspace.id

    # Create a repository
    repo = RegistryRepository(
        organization_id=svc_admin_role.organization_id,
        origin="test_origin",
    )
    session.add(repo)
    await session.flush()

    # Create version
    version = RegistryVersion(
        organization_id=svc_admin_role.organization_id,
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(["test.action"]),
        tarball_uri="s3://test/v1.tar.gz",
    )
    session.add(version)
    await session.flush()

    # Create workflows with registry_lock referencing this version
    workflow1 = Workflow(
        workspace_id=workspace_id,
        title="Workflow 1",
        description="Test workflow 1",
    )
    workflow2 = Workflow(
        workspace_id=workspace_id,
        title="Workflow 2",
        description="Test workflow 2",
    )
    workflow3 = Workflow(
        workspace_id=workspace_id,
        title="Workflow 3 (different version)",
        description="Test workflow 3",
    )
    session.add_all([workflow1, workflow2, workflow3])
    await session.flush()

    # Create definitions - two reference v1.0.0, one references v2.0.0
    # Note: WorkflowDefinition uses workspace_id, not organization_id
    def1 = WorkflowDefinition(
        workflow_id=workflow1.id,
        workspace_id=workspace_id,
        version=1,
        content={},
        registry_lock={
            "origins": {"test_origin": "1.0.0"},
            "actions": {},
        },
    )
    def2 = WorkflowDefinition(
        workflow_id=workflow2.id,
        workspace_id=workspace_id,
        version=1,
        content={},
        registry_lock={
            "origins": {"test_origin": "1.0.0"},
            "actions": {},
        },
    )
    def3 = WorkflowDefinition(
        workflow_id=workflow3.id,
        workspace_id=workspace_id,
        version=1,
        content={},
        registry_lock={
            "origins": {"test_origin": "2.0.0"},  # Different version
            "actions": {},
        },
    )
    session.add_all([def1, def2, def3])
    await session.commit()

    # Query for definitions using version 1.0.0
    versions_service = RegistryVersionsService(session, role=svc_admin_role)
    definitions = await versions_service.get_workflow_definitions_using_version(
        origin="test_origin",
        version_string="1.0.0",
    )

    assert len(definitions) == 2
    workflow_ids = {d.workflow_id for d in definitions}
    assert workflow1.id in workflow_ids
    assert workflow2.id in workflow_ids
    assert workflow3.id not in workflow_ids
