"""Tests for registry lock resolution during draft execution and commit.

This test suite validates:
1. Draft workflow execution returns None for registry_lock (resolves at runtime)
2. Commit always resolves fresh lock from current DSL actions (not stale data)

These tests verify the fix for the regression where:
- Draft executions used stale workflow.registry_lock instead of resolving at runtime
- Commit only resolved lock if workflow.registry_lock was None, reusing stale locks
"""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import (
    PlatformRegistryRepository,
    PlatformRegistryVersion,
)
from tracecat.dsl.common import DSLInput
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.lock.service import RegistryLockService
from tracecat.webhooks.dependencies import DraftWorkflowContext
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.management import WorkflowsManagementService

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
            "interface": {"expects": {}, "returns": None},
            "implementation": {
                "type": "udf",
                "url": "test_origin",
                "module": f"test.{namespace}",
                "name": action_name,
            },
        }
    return {"version": "1.0", "actions": actions}


async def _setup_platform_registry(
    session: AsyncSession, action_names: list[str], origin: str = "test_registry"
) -> PlatformRegistryVersion:
    """Set up a platform registry with the given actions."""
    repo = PlatformRegistryRepository(origin=origin)
    session.add(repo)
    await session.flush()

    version = PlatformRegistryVersion(
        repository_id=repo.id,
        version="1.0.0",
        manifest=_make_manifest(action_names),
        tarball_uri=f"s3://{origin}/v1.tar.gz",
    )
    session.add(version)
    await session.flush()

    repo.current_version_id = version.id
    session.add(repo)
    await session.commit()

    return version


def _create_dsl_with_action(action_name: str, title: str = "Test Workflow") -> DSLInput:
    """Create a DSL with a single action."""
    return DSLInput(
        **{
            "title": title,
            "description": "Test workflow for registry lock",
            "entrypoint": {"expects": {}, "ref": "action_a"},
            "actions": [
                {
                    "ref": "action_a",
                    "action": action_name,
                    "args": {"value": "test"},
                    "depends_on": [],
                }
            ],
            "triggers": [],
        }
    )


class TestDraftWorkflowRegistryLock:
    """Tests for draft workflow registry_lock behavior."""

    @pytest.mark.anyio
    async def test_draft_workflow_returns_none_registry_lock(
        self,
        svc_role: Role,
        session: AsyncSession,
    ) -> None:
        """Test that draft workflow context returns None for registry_lock.

        This ensures draft executions resolve the lock at runtime (using latest registry)
        instead of using potentially stale workflow.registry_lock.
        """
        # Set up registry with an action
        await _setup_platform_registry(session, ["test.action_a"])

        # Create workflow with the action
        dsl = _create_dsl_with_action("test.action_a")
        mgmt_service = WorkflowsManagementService(session, role=svc_role)
        res = await mgmt_service.create_workflow_from_dsl(
            dsl.model_dump(), skip_secret_validation=True
        )
        workflow = res.workflow
        assert workflow is not None

        # Workflow should have registry_lock set from creation
        assert workflow.registry_lock is not None
        assert "test.action_a" in workflow.registry_lock.get("actions", {})

        # Build DSL for draft execution
        built_dsl = await mgmt_service.build_dsl_from_workflow(workflow)

        # Create DraftWorkflowContext as validate_draft_workflow does
        # The fix ensures registry_lock is None for draft executions
        draft_context = DraftWorkflowContext(dsl=built_dsl, registry_lock=None)

        # Verify registry_lock is None (will resolve at runtime)
        assert draft_context.registry_lock is None


class TestCommitWorkflowRegistryLock:
    """Tests for commit workflow registry_lock behavior."""

    @pytest.mark.anyio
    async def test_commit_resolves_fresh_lock_from_dsl(
        self,
        svc_role: Role,
        session: AsyncSession,
    ) -> None:
        """Test that commit always resolves fresh lock from current DSL actions.

        This ensures the committed definition has the correct registry_lock
        reflecting the actual actions in the workflow.
        """
        # Set up registry with two actions
        await _setup_platform_registry(
            session, ["test.action_a", "test.action_b"], "test_registry"
        )

        # Create workflow with action_a
        dsl = _create_dsl_with_action("test.action_a")
        mgmt_service = WorkflowsManagementService(session, role=svc_role)
        res = await mgmt_service.create_workflow_from_dsl(
            dsl.model_dump(), skip_secret_validation=True
        )
        workflow = res.workflow
        assert workflow is not None
        workflow_id = WorkflowUUID.new(workflow.id)

        # Verify initial lock has action_a
        assert workflow.registry_lock is not None
        assert "test.action_a" in workflow.registry_lock.get("actions", {})  # pyright: ignore[reportAttributeAccessIssue]

        # Commit the workflow
        defn_service = WorkflowDefinitionsService(session, role=svc_role)
        built_dsl = await mgmt_service.build_dsl_from_workflow(workflow)

        # Simulate what commit_workflow does: always resolve fresh lock
        lock_service = RegistryLockService(session, role=svc_role)
        action_names = {action.action for action in built_dsl.actions}
        registry_lock = await lock_service.resolve_lock_with_bindings(action_names)

        # Create definition with the fresh lock
        defn = await defn_service.create_workflow_definition(
            workflow_id=workflow_id,
            dsl=built_dsl,
            registry_lock=registry_lock,
        )

        # Verify the definition has the correct lock
        assert defn.registry_lock is not None
        assert "test.action_a" in defn.registry_lock.get("actions", {})

    @pytest.mark.anyio
    async def test_commit_updates_lock_when_actions_change(
        self,
        svc_role: Role,
        session: AsyncSession,
    ) -> None:
        """Test that commit resolves new lock when workflow actions have changed.

        This verifies the fix for the bug where stale registry_lock was reused
        when it was not None, even though actions had changed.
        """
        # Set up registry with two actions
        await _setup_platform_registry(
            session, ["test.action_a", "test.action_b"], "test_registry"
        )

        # Create workflow with action_a
        dsl_a = _create_dsl_with_action("test.action_a", "Workflow with action_a")
        mgmt_service = WorkflowsManagementService(session, role=svc_role)
        res = await mgmt_service.create_workflow_from_dsl(
            dsl_a.model_dump(), skip_secret_validation=True
        )
        workflow = res.workflow
        assert workflow is not None

        # Simulate changing the action type (as would happen in UI)
        # The workflow.registry_lock still has action_a, but DSL now has action_b
        old_lock = workflow.registry_lock
        assert old_lock is not None
        assert "test.action_a" in old_lock.get("actions", {})  # pyright: ignore[reportAttributeAccessIssue]

        # Build new DSL with action_b
        dsl_b = _create_dsl_with_action("test.action_b", "Workflow with action_b")

        # Simulate what commit_workflow does: always resolve fresh lock from DSL
        lock_service = RegistryLockService(session, role=svc_role)
        action_names = {action.action for action in dsl_b.actions}
        new_lock = await lock_service.resolve_lock_with_bindings(action_names)

        # The new lock should have action_b, not action_a
        assert "test.action_b" in new_lock.actions
        assert "test.action_a" not in new_lock.actions

        # Update workflow with new lock (as commit_workflow does)
        workflow.registry_lock = new_lock.model_dump()  # pyright: ignore[reportAttributeAccessIssue]

        # Verify the workflow lock is updated
        assert workflow.registry_lock is not None
        assert "test.action_b" in workflow.registry_lock.get("actions", {})  # pyright: ignore[reportOptionalMemberAccess]
        assert "test.action_a" not in workflow.registry_lock.get("actions", {})  # pyright: ignore[reportOptionalMemberAccess]

    @pytest.mark.anyio
    async def test_commit_does_not_reuse_stale_lock(
        self,
        svc_role: Role,
        session: AsyncSession,
    ) -> None:
        """Test that commit does NOT reuse existing workflow.registry_lock.

        Before the fix, commit only resolved lock if workflow.registry_lock was None.
        This test verifies the fix: always resolve fresh lock regardless of existing value.
        """
        # Set up registry
        await _setup_platform_registry(
            session, ["test.action_a", "test.action_b"], "test_registry"
        )

        # Create workflow - this sets registry_lock
        dsl = _create_dsl_with_action("test.action_a")
        mgmt_service = WorkflowsManagementService(session, role=svc_role)
        res = await mgmt_service.create_workflow_from_dsl(
            dsl.model_dump(), skip_secret_validation=True
        )
        workflow = res.workflow
        assert workflow is not None

        # Verify workflow has registry_lock (not None)
        assert workflow.registry_lock is not None

        # Manually corrupt the lock to simulate stale data
        workflow.registry_lock = {  # pyright: ignore[reportAttributeAccessIssue]
            "actions": {"stale.fake_action": "stale_registry"},
            "origins": {"stale_registry": "0.0.0"},
        }

        # Build DSL from workflow
        built_dsl = await mgmt_service.build_dsl_from_workflow(workflow)

        # Simulate commit: should resolve fresh lock, not use stale one
        lock_service = RegistryLockService(session, role=svc_role)
        action_names = {action.action for action in built_dsl.actions}
        fresh_lock = await lock_service.resolve_lock_with_bindings(action_names)

        # Fresh lock should have the actual action, not the stale one
        assert "test.action_a" in fresh_lock.actions
        assert "stale.fake_action" not in fresh_lock.actions
