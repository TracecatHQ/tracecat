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


async def _seed_repo(
    session: AsyncSession,
    role: Role,
    manifests: dict[str, dict],
    *,
    current: str | None = None,
) -> tuple[RegistryRepository, dict[str, RegistryVersion]]:
    """Create a repository with one version per manifest and commit."""
    repo = RegistryRepository(
        organization_id=role.organization_id,
        origin="test_origin",
    )
    session.add(repo)
    await session.flush()
    versions = {
        version: RegistryVersion(
            organization_id=role.organization_id,
            repository_id=repo.id,
            version=version,
            manifest=manifest,
            tarball_uri=f"s3://test/{version}.tar.gz",
        )
        for version, manifest in manifests.items()
    }
    session.add_all(versions.values())
    await session.flush()
    if current is not None:
        repo.current_version_id = versions[current].id
        session.add(repo)
    await session.commit()
    return repo, versions


@pytest.mark.anyio
async def test_delete_version_success(
    svc_admin_role: Role,
    session: AsyncSession,
) -> None:
    """Test that deleting a non-current version without references works."""
    repo, versions = await _seed_repo(
        session,
        svc_admin_role,
        {
            "1.0.0": _make_manifest(["test.action_v1"]),
            "2.0.0": _make_manifest(["test.action_v2"]),
        },
        current="2.0.0",
    )
    version1, version2 = versions["1.0.0"], versions["2.0.0"]

    repos_service = RegistryReposService(session, role=svc_admin_role)
    versions_service = RegistryVersionsService(session, role=svc_admin_role)

    # Validation should pass for non-current version
    await repos_service.validate_version_deletion(repo, version1)
    await versions_service.delete_version(version1)

    assert await versions_service.get_version(version1.id) is None
    assert await versions_service.get_version(version2.id) is not None


@pytest.mark.anyio
async def test_cannot_delete_current_version(
    svc_admin_role: Role,
    session: AsyncSession,
) -> None:
    """Test that deleting the current version is blocked."""
    repo, versions = await _seed_repo(
        session,
        svc_admin_role,
        {"1.0.0": _make_manifest(["test.action"])},
        current="1.0.0",
    )

    repos_service = RegistryReposService(session, role=svc_admin_role)

    with pytest.raises(RegistryError, match="currently promoted version"):
        await repos_service.validate_version_deletion(repo, versions["1.0.0"])


@pytest.mark.anyio
async def test_cannot_delete_version_in_use(
    svc_admin_role: Role,
    session: AsyncSession,
    svc_workspace,
) -> None:
    """Test that deleting a version referenced by published workflows is blocked."""
    workspace_id = svc_workspace.id
    repo, versions = await _seed_repo(
        session,
        svc_admin_role,
        {
            "1.0.0": _make_manifest(["test.action"]),
            "2.0.0": _make_manifest(["test.action"]),
        },
        current="2.0.0",
    )
    version1 = versions["1.0.0"]

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
    repo, versions = await _seed_repo(
        session,
        svc_admin_role,
        {
            "1.0.0": _make_manifest(["test.action_v1"]),
            "2.0.0": _make_manifest(["test.action_v2"]),
            "3.0.0": _make_manifest(["test.action_v3"]),
        },
    )
    version1, version2, version3 = versions.values()

    # Manually set created_at to ensure ordering
    base_time = datetime.now(UTC)
    version1.created_at = base_time - timedelta(hours=2)
    version2.created_at = base_time - timedelta(hours=1)
    version3.created_at = base_time
    session.add_all([version1, version2, version3])
    await session.commit()

    versions_service = RegistryVersionsService(session, role=svc_admin_role)

    prev = await versions_service.get_previous_version(repo.id, version3.id)
    assert prev is not None
    assert prev.id == version2.id

    prev = await versions_service.get_previous_version(repo.id, version2.id)
    assert prev is not None
    assert prev.id == version1.id


@pytest.mark.anyio
async def test_get_previous_version_none(
    svc_admin_role: Role,
    session: AsyncSession,
) -> None:
    """Test that getting previous version returns None for the oldest version."""
    repo, versions = await _seed_repo(
        session, svc_admin_role, {"1.0.0": _make_manifest(["test.action"])}
    )

    versions_service = RegistryVersionsService(session, role=svc_admin_role)
    prev = await versions_service.get_previous_version(repo.id, versions["1.0.0"].id)
    assert prev is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("base_actions", "compare_actions", "modify", "added", "removed", "modified"),
    [
        pytest.param(
            ["test.action_a"],
            ["test.action_a", "test.action_b"],
            False,
            ["test.action_b"],
            [],
            [],
            id="added",
        ),
        pytest.param(
            ["test.action_a", "test.action_b"],
            ["test.action_a"],
            False,
            [],
            ["test.action_b"],
            [],
            id="removed",
        ),
        pytest.param(
            ["test.action"],
            ["test.action"],
            True,
            [],
            [],
            ["test.action"],
            id="modified",
        ),
        pytest.param(
            ["test.action"], ["test.action"], False, [], [], [], id="no_changes"
        ),
    ],
)
async def test_compare_versions(
    svc_admin_role: Role,
    session: AsyncSession,
    base_actions: list[str],
    compare_actions: list[str],
    modify: bool,
    added: list[str],
    removed: list[str],
    modified: list[str],
) -> None:
    """Version diff identifies added, removed, and modified actions."""
    compare_manifest = _make_manifest(compare_actions)
    if modify:
        action = compare_manifest["actions"]["test.action"]
        action["interface"]["expects"] = {"input": "str", "extra": "int"}
        action["description"] = "Modified description"
    _, versions = await _seed_repo(
        session,
        svc_admin_role,
        {"1.0.0": _make_manifest(base_actions), "2.0.0": compare_manifest},
    )

    versions_service = RegistryVersionsService(session, role=svc_admin_role)
    diff = await versions_service.compare_versions(versions["1.0.0"], versions["2.0.0"])

    assert diff.base_version == "1.0.0"
    assert diff.compare_version == "2.0.0"
    assert list(diff.actions_added) == added
    assert list(diff.actions_removed) == removed
    assert [change.action_name for change in diff.actions_modified] == modified
    for change in diff.actions_modified:
        assert change.description_changed is True
        assert len(change.interface_changes) > 0
    assert diff.total_changes == len(added) + len(removed) + len(modified)


@pytest.mark.anyio
async def test_get_workflow_definitions_using_version(
    svc_admin_role: Role,
    session: AsyncSession,
    svc_workspace,
) -> None:
    """Test finding workflow definitions that reference a specific version."""
    workspace_id = svc_workspace.id
    await _seed_repo(
        session, svc_admin_role, {"1.0.0": _make_manifest(["test.action"])}
    )

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
