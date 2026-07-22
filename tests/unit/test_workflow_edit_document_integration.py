"""DB-backed integration tests for the workflow edit-document persist path.

Covers customer-feedback fixes:
- Fix 1: action ``environment`` survives an edit-document round-trip.
- Fix 5: server-side rebase of ref-addressed ops under a stale base revision.
"""

from __future__ import annotations

from typing import Any

import pytest

from tracecat.auth.types import Role
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.management.draft import (
    apply_or_rebase_patch,
    build_workflow_edit_document,
    compute_workflow_edit_revision,
    parse_workflow_edit_request,
    persist_workflow_edit_document,
)
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.schemas import ExternalWorkflowDefinition

pytestmark = pytest.mark.usefixtures("registry_version_with_manifest")


def _import_definition() -> dict[str, Any]:
    dsl = ExternalWorkflowDefinition.model_validate(
        {
            "workflow_id": WorkflowUUID.new_uuid4(),
            "definition": {
                "title": "Edit round-trip workflow",
                "description": "",
                "entrypoint": {"expects": {}, "ref": "a"},
                "actions": [
                    {
                        "ref": "a",
                        "action": "core.transform.reshape",
                        "args": {"value": 1},
                    },
                    {
                        "ref": "b",
                        "action": "core.transform.reshape",
                        "args": {"value": 2},
                        "depends_on": ["a"],
                    },
                ],
            },
        }
    )
    return dsl.model_dump(mode="json")


async def _create_workflow(service: WorkflowsManagementService):
    return await service.create_workflow_from_external_definition(_import_definition())


@pytest.mark.anyio
async def test_environment_patch_survives_persist(test_role: Role) -> None:
    """Fix 1: patching an action's environment persists and bumps the revision."""
    async with WorkflowsManagementService.with_session(test_role) as service:
        workflow = await _create_workflow(service)

        document = build_workflow_edit_document(workflow)
        original_revision = compute_workflow_edit_revision(document)

        request = parse_workflow_edit_request(
            base_revision=original_revision,
            patch_ops=[
                {
                    "op": "replace",
                    "path": "/definition/actions/b/environment",
                    "value": "prod",
                }
            ],
            validate_only=False,
        )
        updated_document, _ = apply_or_rebase_patch(
            head_document=document,
            request=request,
            current_revision=original_revision,
        )
        changed = await persist_workflow_edit_document(
            role=test_role,
            service=service,
            workflow=workflow,
            original_document=document,
            updated_document=updated_document,
        )
        assert "definition" in changed

        await service.session.refresh(workflow, ["actions"])
        rebuilt = build_workflow_edit_document(workflow)
        action_b = next(a for a in rebuilt.definition.actions if a.ref == "b")
        assert action_b.environment == "prod"
        assert compute_workflow_edit_revision(rebuilt) != original_revision


@pytest.mark.anyio
async def test_no_op_patch_returns_empty_changed_sections(test_role: Role) -> None:
    """Fix 1: a patch that changes nothing persists no sections (no_op signal)."""
    async with WorkflowsManagementService.with_session(test_role) as service:
        workflow = await _create_workflow(service)

        document = build_workflow_edit_document(workflow)
        # Replace a value with its current value -> no net change.
        current_value = next(
            a for a in document.definition.actions if a.ref == "b"
        ).args["value"]
        revision = compute_workflow_edit_revision(document)
        request = parse_workflow_edit_request(
            base_revision=revision,
            patch_ops=[
                {
                    "op": "replace",
                    "path": "/definition/actions/b/args/value",
                    "value": current_value,
                }
            ],
            validate_only=False,
        )
        updated_document, _ = apply_or_rebase_patch(
            head_document=document, request=request, current_revision=revision
        )
        changed = await persist_workflow_edit_document(
            role=test_role,
            service=service,
            workflow=workflow,
            original_document=document,
            updated_document=updated_document,
        )
        assert changed == set()


@pytest.mark.anyio
async def test_stale_base_ref_addressed_patch_rebases(test_role: Role) -> None:
    """Fix 5: a ref-addressed definition patch on a stale base auto-rebases."""
    async with WorkflowsManagementService.with_session(test_role) as service:
        workflow = await _create_workflow(service)
        document = build_workflow_edit_document(workflow)
        head_revision = compute_workflow_edit_revision(document)

        # Simulate the client holding a stale base revision (a concurrent
        # layout-only change happened). The ref-addressed op is safe to replay.
        request = parse_workflow_edit_request(
            base_revision="stale-client-revision",
            patch_ops=[
                {
                    "op": "replace",
                    "path": "/definition/actions/b/args/value",
                    "value": 99,
                }
            ],
            validate_only=False,
        )
        updated_document, rebased = apply_or_rebase_patch(
            head_document=document,
            request=request,
            current_revision=head_revision,
        )
        assert rebased is True

        changed = await persist_workflow_edit_document(
            role=test_role,
            service=service,
            workflow=workflow,
            original_document=document,
            updated_document=updated_document,
        )
        assert "definition" in changed
        await service.session.refresh(workflow, ["actions"])
        rebuilt = build_workflow_edit_document(workflow)
        action_b = next(a for a in rebuilt.definition.actions if a.ref == "b")
        assert action_b.args["value"] == 99


@pytest.mark.anyio
async def test_graph_version_bumps_on_rebased_definition_persist(
    test_role: Role,
) -> None:
    """Fix 5: a rebased definition persist bumps graph_version."""
    async with WorkflowsManagementService.with_session(test_role) as service:
        workflow = await _create_workflow(service)
        before_version = workflow.graph_version
        document = build_workflow_edit_document(workflow)
        head_revision = compute_workflow_edit_revision(document)

        request = parse_workflow_edit_request(
            base_revision="stale-client-revision",
            patch_ops=[
                {
                    "op": "replace",
                    "path": "/definition/actions/b/args/value",
                    "value": 7,
                }
            ],
            validate_only=False,
        )
        updated_document, rebased = apply_or_rebase_patch(
            head_document=document,
            request=request,
            current_revision=head_revision,
        )
        assert rebased is True
        await persist_workflow_edit_document(
            role=test_role,
            service=service,
            workflow=workflow,
            original_document=document,
            updated_document=updated_document,
        )
        await service.session.refresh(workflow)
        assert workflow.graph_version == before_version + 1
