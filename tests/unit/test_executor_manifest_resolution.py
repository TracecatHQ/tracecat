import uuid
from contextlib import asynccontextmanager
from datetime import UTC, datetime

import pytest

from tracecat.db.models import RegistryAction, RegistryRepository, RegistryVersion
from tracecat.dsl.common import create_default_execution_context
from tracecat.dsl.schemas import ActionStatement, RunActionInput, RunContext
from tracecat.executor.service import _prepare_step_context, prepare_resolved_context
from tracecat.identifiers.workflow import WorkflowUUID, generate_exec_id
from tracecat.registry.lock.types import RegistryLock
from tracecat.registry.versions.service import RegistryVersionsService


def _udf_impl(*, origin: str, module: str, name: str) -> dict[str, str]:
    return {"type": "udf", "url": origin, "module": module, "name": name}


def _manifest_action(*, action: str, action_type: str, implementation: dict) -> dict:
    namespace, name = action.rsplit(".", maxsplit=1)
    return {
        "namespace": namespace,
        "name": name,
        "action_type": action_type,
        "description": "",
        "interface": {"expects": {}, "returns": None},
        "implementation": implementation,
    }


@pytest.mark.anyio
async def test_prepare_resolved_context_uses_manifest_for_locked_workflows(
    db, session, test_role, mocker
):
    origin = "core-registry"
    version = "v1"
    action_name = "core.echo"

    repo = RegistryRepository(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        origin=origin,
    )
    session.add(repo)
    await session.commit()

    manifest = {
        "schema_version": "1.0",
        "actions": {
            action_name: _manifest_action(
                action=action_name,
                action_type="udf",
                implementation=_udf_impl(
                    origin=origin, module="manifest.module", name="manifest_fn"
                ),
            )
        },
    }
    rv = RegistryVersion(
        organization_id=uuid.uuid4(),
        repository_id=repo.id,
        version=version,
        manifest=manifest,
        tarball_uri="s3://example/core-v1.tar.gz",
    )
    session.add(rv)
    await session.commit()

    versions_svc = RegistryVersionsService(session)
    await versions_svc.populate_index_from_manifest(rv, commit=True)

    # Add a conflicting mutable RegistryAction that should NOT be used for locked workflows.
    session.add(
        RegistryAction(
            organization_id=uuid.uuid4(),
            repository_id=repo.id,
            origin=origin,
            namespace="core",
            name="echo",
            description="",
            type="udf",
            interface={"expects": {}, "returns": None},
            implementation=_udf_impl(origin=origin, module="db.module", name="db_fn"),
            options={},
        )
    )
    await session.commit()

    wf_id = WorkflowUUID.new_uuid4()
    run_ctx = RunContext(
        wf_id=wf_id,
        wf_exec_id=generate_exec_id(wf_id),
        wf_run_id=uuid.uuid4(),
        environment="default",
        logical_time=datetime.now(UTC),
    )
    task = ActionStatement(ref="a", action=action_name, args={})
    input = RunActionInput(
        task=task,
        exec_context=create_default_execution_context(),
        run_context=run_ctx,
        registry_lock=RegistryLock(
            origins={origin: version},
            actions={action_name: origin},
        ),
    )

    # Keep test focused on resolution, not secret/variable fetching.
    mocker.patch(
        "tracecat.executor.service.secrets_manager.get_action_secrets",
        return_value={},
    )
    mocker.patch("tracecat.executor.service.get_workspace_variables", return_value={})

    @asynccontextmanager
    async def _session_cm():
        yield session

    # Ensure manifest resolution uses this test session.
    mocker.patch(
        "tracecat.executor.service.get_async_session_context_manager", _session_cm
    )
    mocker.patch(
        "tracecat.executor.registry_resolver.get_async_session_context_manager",
        _session_cm,
    )

    prepared = await prepare_resolved_context(input=input, role=test_role)
    assert prepared.resolved_context.action_impl.type == "udf"
    assert prepared.resolved_context.action_impl.module == "manifest.module"
    assert prepared.resolved_context.action_impl.name == "manifest_fn"


@pytest.mark.anyio
async def test_prepare_step_context_uses_manifest_for_template_steps(
    db, session, test_role, mocker
):
    origin = "core-registry"
    version = "v1"
    step_action = "core.echo"

    repo = RegistryRepository(
        id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        origin=origin,
    )
    session.add(repo)
    await session.commit()

    manifest = {
        "schema_version": "1.0",
        "actions": {
            step_action: _manifest_action(
                action=step_action,
                action_type="udf",
                implementation=_udf_impl(
                    origin=origin, module="manifest.module", name="manifest_fn"
                ),
            )
        },
    }
    rv = RegistryVersion(
        organization_id=uuid.uuid4(),
        repository_id=repo.id,
        version=version,
        manifest=manifest,
        tarball_uri="s3://example/core-v1.tar.gz",
    )
    session.add(rv)
    await session.commit()

    versions_svc = RegistryVersionsService(session)
    await versions_svc.populate_index_from_manifest(rv, commit=True)

    wf_id = WorkflowUUID.new_uuid4()
    run_ctx = RunContext(
        wf_id=wf_id,
        wf_exec_id=generate_exec_id(wf_id),
        wf_run_id=uuid.uuid4(),
        environment="default",
        logical_time=datetime.now(UTC),
    )
    input = RunActionInput(
        task=ActionStatement(ref="parent", action=step_action, args={}),
        exec_context=create_default_execution_context(),
        run_context=run_ctx,
        registry_lock=RegistryLock(
            origins={origin: version},
            actions={step_action: origin},
        ),
    )

    @asynccontextmanager
    async def _session_cm():
        yield session

    mocker.patch(
        "tracecat.executor.service.get_async_session_context_manager", _session_cm
    )
    mocker.patch(
        "tracecat.executor.registry_resolver.get_async_session_context_manager",
        _session_cm,
    )

    mocker.patch(
        "tracecat.executor.service.secrets_manager.get_action_secrets",
        return_value={},
    )
    mocker.patch("tracecat.executor.service.get_workspace_variables", return_value={})

    parent_resolved = (
        await prepare_resolved_context(input=input, role=test_role)
    ).resolved_context
    step_ctx = await _prepare_step_context(
        step_action=step_action,
        evaluated_args={},
        parent_resolved=parent_resolved,
        input=input,
        role=test_role,
    )
    assert step_ctx.action_impl.type == "udf"
    assert step_ctx.action_impl.module == "manifest.module"
    assert step_ctx.action_impl.name == "manifest_fn"
