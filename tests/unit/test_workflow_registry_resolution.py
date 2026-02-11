"""Tests for workflow registry resolution with template platform actions."""

from __future__ import annotations

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import RegistryIndex, RegistryRepository, RegistryVersion
from tracecat.dsl.common import DSLInput
from tracecat.dsl.enums import PlatformAction
from tracecat.workflow.management.registry_resolution import (
    resolve_action_origins_from_lock,
)

pytestmark = pytest.mark.usefixtures("db")


def _create_dsl(action_name: str) -> DSLInput:
    """Create a minimal DSL with a single action."""
    return DSLInput(
        **{
            "title": "Test workflow",
            "description": "Test",
            "entrypoint": {"ref": "template_action", "expects": {}},
            "actions": [
                {
                    "ref": "template_action",
                    "action": action_name,
                    "args": {},
                    "depends_on": [],
                }
            ],
            "triggers": [],
        }
    )


def _template_manifest(*, template_action: str, step_action: str) -> dict:
    """Create a manifest containing a template and its referenced step action."""
    template_namespace, template_name = template_action.rsplit(".", 1)
    step_namespace, step_name = step_action.rsplit(".", 1)

    step_module = "tracecat_registry.core.script"
    step_fn = "run_python"
    if step_action == PlatformAction.CHILD_WORKFLOW_EXECUTE:
        step_module = "tracecat_registry.core.workflow"
        step_fn = "execute"

    return {
        "version": "1.0",
        "actions": {
            template_action: {
                "namespace": template_namespace,
                "name": template_name,
                "action_type": "template",
                "description": "Template action",
                "interface": {"expects": {}, "returns": None},
                "implementation": {
                    "type": "template",
                    "template_action": {
                        "type": "action",
                        "definition": {
                            "name": template_name,
                            "namespace": template_namespace,
                            "title": "Template action",
                            "display_group": "Testing",
                            "expects": {},
                            "steps": [
                                {
                                    "ref": "step1",
                                    "action": step_action,
                                    "args": {},
                                }
                            ],
                            "returns": "${{ steps.step1.result }}",
                        },
                    },
                },
            },
            step_action: {
                "namespace": step_namespace,
                "name": step_name,
                "action_type": "udf",
                "description": "Step action",
                "interface": {"expects": {}, "returns": None},
                "implementation": {
                    "type": "udf",
                    "url": "tracecat_registry",
                    "module": step_module,
                    "name": step_fn,
                },
            },
        },
    }


async def _seed_registry(
    *,
    session: AsyncSession,
    svc_role: Role,
    origin: str,
    version: str,
    manifest: dict,
    index_actions: dict[str, str],
) -> None:
    """Seed registry repository/version/index for resolution tests."""
    repo = RegistryRepository(
        organization_id=svc_role.organization_id,
        origin=origin,
    )
    session.add(repo)
    await session.flush()

    registry_version = RegistryVersion(
        organization_id=svc_role.organization_id,
        repository_id=repo.id,
        version=version,
        manifest=manifest,
        tarball_uri=f"s3://{origin}/{version}.tar.gz",
    )
    session.add(registry_version)
    await session.flush()

    index_entries = []
    for action_name, action_type in index_actions.items():
        namespace, name = action_name.rsplit(".", 1)
        index_entries.append(
            RegistryIndex(
                organization_id=svc_role.organization_id,
                registry_version_id=registry_version.id,
                namespace=namespace,
                name=name,
                action_type=action_type,
                description=f"Indexed {action_name}",
            )
        )
    session.add_all(index_entries)
    await session.commit()


@pytest.mark.anyio
async def test_registry_resolution_allows_run_python_template_step(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    """Publish-time resolution should allow core.script.run_python in templates."""
    origin = "test_origin"
    version = "1.0.0"
    template_action = "tools.testing.template_with_python"
    step_action = PlatformAction.RUN_PYTHON
    org_id = svc_role.organization_id
    assert org_id is not None

    await _seed_registry(
        session=session,
        svc_role=svc_role,
        origin=origin,
        version=version,
        manifest=_template_manifest(
            template_action=template_action,
            step_action=step_action,
        ),
        index_actions={
            template_action: "template",
            step_action: "udf",
        },
    )

    resolved, errors = await resolve_action_origins_from_lock(
        session=session,
        dsl=_create_dsl(template_action),
        registry_lock={origin: version},
        organization_id=org_id,
    )

    assert errors == []
    assert resolved[template_action] == origin
    assert resolved[step_action] == origin


@pytest.mark.anyio
async def test_registry_resolution_rejects_non_run_python_platform_step(
    svc_role: Role,
    session: AsyncSession,
) -> None:
    """Publish-time resolution should still reject other platform actions in templates."""
    origin = "test_origin"
    version = "1.0.0"
    template_action = "tools.testing.template_with_subflow"
    step_action = PlatformAction.CHILD_WORKFLOW_EXECUTE
    org_id = svc_role.organization_id
    assert org_id is not None

    await _seed_registry(
        session=session,
        svc_role=svc_role,
        origin=origin,
        version=version,
        manifest=_template_manifest(
            template_action=template_action,
            step_action=step_action,
        ),
        index_actions={
            template_action: "template",
            step_action: "udf",
        },
    )

    resolved, errors = await resolve_action_origins_from_lock(
        session=session,
        dsl=_create_dsl(template_action),
        registry_lock={origin: version},
        organization_id=org_id,
    )

    assert resolved == {}
    assert errors
    assert errors[0].action == step_action
    assert "cannot be used inside template" in errors[0].msg
