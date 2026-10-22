import asyncio
import json
import uuid
from collections.abc import AsyncGenerator
from dataclasses import asdict
from typing import cast, get_args
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from asyncpg import CheckViolationError, UniqueViolationError
from fastapi import FastAPI
from sqlalchemy import delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.api.common import generic_exception_handler
from tracecat.auth.dependencies import (
    ExecutorWorkspaceRole,
    WorkspaceActorRouteRole,
    WorkspaceUserRouteRole,
)
from tracecat.auth.types import Role
from tracecat.db.common import DBConstraints
from tracecat.db.engine import get_async_session, get_async_session_context_manager
from tracecat.db.models import Action, Webhook, Workflow, Workspace
from tracecat.db.types import PostgresConstraintError
from tracecat.dsl.common import DSLInput
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.registry.lock.service import RegistryLockService
from tracecat.webhooks.schemas import WebhookCreate
from tracecat.webhooks.service import create_webhook
from tracecat.workflow.actions.router import router as actions_router
from tracecat.workflow.actions.schemas import ActionCreate
from tracecat.workflow.actions.service import WorkflowActionService
from tracecat.workflow.executions.internal_router import router as internal_router
from tracecat.workflow.graph.router import router as graph_router
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.draft import (
    build_workflow_edit_document,
    compute_workflow_edit_revision,
)
from tracecat.workflow.management.folders.router import router as folders_router
from tracecat.workflow.management.folders.service import WorkflowFolderService
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.router import router as workflows_router
from tracecat.workflow.store.import_service import WorkflowImportService
from tracecat.workflow.store.router import router as store_router
from tracecat.workflow.store.schemas import RemoteWorkflowDefinition
from tracecat.workspace_sync.adapters.case_tag import CaseTagAdapter
from tracecat.workspace_sync.schemas import CaseTagResourceSpec, WorkspaceSpec

pytestmark = pytest.mark.anyio

ERROR_MARKER = "synthetic database failure"


def integrity_error(constraint: str | None = None) -> IntegrityError:
    cause = (
        UniqueViolationError(ERROR_MARKER)
        if constraint is not None
        else CheckViolationError(ERROR_MARKER)
    )
    cast(PostgresConstraintError, cause).constraint_name = constraint
    return IntegrityError("synthetic statement", {}, cause)


@pytest.fixture
async def workflow(session: AsyncSession, svc_role: Role) -> Workflow:
    workflow = Workflow(
        workspace_id=svc_role.workspace_id,
        title="Synthetic workflow",
        description="",
    )
    session.add(workflow)
    await session.commit()
    await session.refresh(workflow, ["actions", "schedules", "case_trigger"])
    return workflow


@pytest.fixture
async def client(
    session: AsyncSession, svc_role: Role
) -> AsyncGenerator[httpx.AsyncClient, None]:
    app = FastAPI()
    for router in (
        actions_router,
        graph_router,
        workflows_router,
        folders_router,
        internal_router,
        store_router,
    ):
        app.include_router(router)
    app.add_exception_handler(Exception, generic_exception_handler)
    app.dependency_overrides[get_async_session] = lambda: session
    for role_type in (
        WorkspaceActorRouteRole,
        WorkspaceUserRouteRole,
        ExecutorWorkspaceRole,
    ):
        app.dependency_overrides[get_args(role_type)[1].dependency] = lambda: svc_role
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app, raise_app_exceptions=False),
        base_url="http://test",
    ) as client:
        yield client


@pytest.mark.parametrize("resource", ["action", "webhook"])
@pytest.mark.parametrize("foreign", [False, True])
async def test_create_requires_workspace_scoped_parent(
    client: httpx.AsyncClient,
    session: AsyncSession,
    workflow: Workflow,
    svc_role: Role,
    resource: str,
    foreign: bool,
) -> None:
    workflow_id = WorkflowUUID.new(workflow.id) if foreign else WorkflowUUID.new_uuid4()
    if foreign:
        # A row that exists but belongs to another workspace is still absent.
        other_workspace = Workspace(
            name="Other synthetic workspace", organization_id=svc_role.organization_id
        )
        session.add(other_workspace)
        await session.flush()
        workflow.workspace_id = other_workspace.id
        await session.commit()
    if resource == "action":
        response = await client.post(
            "/actions",
            json={
                "workflow_id": str(workflow_id),
                "type": "core.transform.reshape",
                "title": "Synthetic action",
            },
        )
        model = Action
    else:
        response = await client.post(f"/workflows/{workflow_id}/webhook", json={})
        model = Webhook
    assert response.status_code == 404, response.text
    assert ERROR_MARKER not in response.text
    assert (
        await session.scalar(
            select(func.count())
            .select_from(model)
            .where(model.workflow_id == workflow_id)
        )
        == 0
    )


@pytest.mark.parametrize("mutation", ["create", "update", "graph_add", "graph_update"])
async def test_action_ref_collisions_are_conflicts(
    client: httpx.AsyncClient,
    session: AsyncSession,
    workflow: Workflow,
    svc_role: Role,
    mutation: str,
) -> None:
    service = WorkflowActionService(session, svc_role)
    wf_id = WorkflowUUID.new(workflow.id)
    first = await service.create_action(
        ActionCreate(workflow_id=wf_id, type="core.transform.reshape", title="Step One")
    )
    second = await service.create_action(
        ActionCreate(workflow_id=wf_id, type="core.transform.reshape", title="Step Two")
    )
    if mutation == "create":
        response = await client.post(
            "/actions",
            json={"workflow_id": str(wf_id), "type": first.type, "title": "Step-One"},
        )
    elif mutation == "update":
        response = await client.post(
            f"/actions/{second.id}",
            params={"workflow_id": str(wf_id)},
            json={"title": "Step-One"},
        )
    else:
        payload = (
            {"type": first.type, "title": "Step-One"}
            if mutation == "graph_add"
            else {"action_id": str(second.id), "title": "Step-One"}
        )
        response = await client.patch(
            f"/workflows/{wf_id}/graph",
            json={
                "base_version": workflow.graph_version,
                "operations": [
                    {
                        "type": "add_node"
                        if mutation == "graph_add"
                        else "update_node",
                        "payload": payload,
                    }
                ],
            },
        )
    assert response.status_code == 409, response.text
    await session.rollback()
    titles = list(
        (
            await session.scalars(
                select(Action.title).where(Action.workflow_id == wf_id)
            )
        ).all()
    )
    assert sorted(titles) == ["Step One", "Step Two"]


async def test_action_ref_unchanged_title_and_other_workflow_are_allowed(
    session: AsyncSession, workflow: Workflow, svc_role: Role
) -> None:
    service = WorkflowActionService(session, svc_role)
    wf_id = WorkflowUUID.new(workflow.id)
    action = await service.create_action(
        ActionCreate(workflow_id=wf_id, type="core.transform.reshape", title="Step One")
    )
    await service.ensure_unique_ref(wf_id, "Step-One", exclude_action_id=action.id)
    other = Workflow(
        workspace_id=svc_role.workspace_id, title="Other workflow", description=""
    )
    session.add(other)
    await session.commit()
    await service.create_action(
        ActionCreate(
            workflow_id=WorkflowUUID.new(other.id), type=action.type, title="Step-One"
        )
    )


async def test_stale_batch_positions_remain_best_effort(
    client: httpx.AsyncClient, session: AsyncSession, workflow: Workflow, svc_role: Role
) -> None:
    wf_id = WorkflowUUID.new(workflow.id)
    action = await WorkflowActionService(session, svc_role).create_action(
        ActionCreate(workflow_id=wf_id, type="core.transform.reshape", title="Step")
    )
    response = await client.post(
        "/actions/batch-positions",
        params={"workflow_id": str(wf_id)},
        json={
            "actions": [
                {"action_id": str(action.id), "position": {"x": 12, "y": 34}},
                {"action_id": str(uuid.uuid4()), "position": {"x": 56, "y": 78}},
            ]
        },
    )
    assert response.status_code == 204, response.text
    await session.refresh(action)
    assert (action.position_x, action.position_y) == (12, 34)
    response = await client.post(
        "/actions/batch-positions",
        params={"workflow_id": str(WorkflowUUID.new_uuid4())},
        json={
            "actions": [{"action_id": str(action.id), "position": {"x": 90, "y": 90}}]
        },
    )
    assert response.status_code == 204, response.text
    await session.refresh(action)
    assert (action.position_x, action.position_y) == (12, 34)


@pytest.mark.parametrize("surface", ["file", "update", "restore"])
@pytest.mark.parametrize("known", [False, True])
async def test_workflow_routes_only_map_alias_constraint(
    client: httpx.AsyncClient,
    session: AsyncSession,
    workflow: Workflow,
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
    known: bool,
) -> None:
    error = integrity_error(
        DBConstraints.WORKFLOW_ALIAS_UNIQUE_IN_WORKSPACE if known else None
    )
    wf_id = WorkflowUUID.new(workflow.id)
    rollback = AsyncMock(wraps=session.rollback)
    monkeypatch.setattr(session, "rollback", rollback)
    if surface == "file":
        monkeypatch.setattr(
            WorkflowsManagementService,
            "create_workflow_from_external_definition",
            AsyncMock(side_effect=error),
        )
        response = await client.post(
            "/workflows", files={"file": ("workflow.json", "{}", "application/json")}
        )
    elif surface == "update":
        monkeypatch.setattr(
            WorkflowsManagementService, "update_workflow", AsyncMock(side_effect=error)
        )
        response = await client.patch(
            f"/workflows/{wf_id}", json={"alias": "synthetic-alias"}
        )
    else:
        monkeypatch.setattr(
            WorkflowsManagementService, "get_workflow", AsyncMock(return_value=workflow)
        )
        monkeypatch.setattr(
            WorkflowDefinitionsService,
            "get_definition_by_workflow_id",
            AsyncMock(return_value=MagicMock(workflow_id=workflow.id)),
        )
        monkeypatch.setattr(
            WorkflowsManagementService,
            "restore_workflow_definition",
            AsyncMock(side_effect=error),
        )
        response = await client.post(f"/workflows/{wf_id}/definitions/1/restore")
    assert response.status_code == (409 if known else 500), response.text
    assert ERROR_MARKER not in response.text
    rollback.assert_awaited()
    if known:
        assert "alias" in response.text.lower()
    else:
        assert response.json() == {
            "message": "An unexpected error occurred. Please try again later."
        }


@pytest.mark.parametrize("internal", [False, True])
@pytest.mark.parametrize("known", [False, True])
async def test_draft_write_only_maps_alias_constraint(
    client: httpx.AsyncClient,
    session: AsyncSession,
    workflow: Workflow,
    monkeypatch: pytest.MonkeyPatch,
    internal: bool,
    known: bool,
) -> None:
    wf_id = WorkflowUUID.new(workflow.id)
    document = build_workflow_edit_document(workflow)
    monkeypatch.setattr(
        WorkflowsManagementService, "get_workflow", AsyncMock(return_value=workflow)
    )
    monkeypatch.setattr(
        session,
        "commit",
        AsyncMock(
            side_effect=integrity_error(
                DBConstraints.WORKFLOW_ALIAS_UNIQUE_IN_WORKSPACE if known else None
            )
        ),
    )
    monkeypatch.setattr(session, "rollback", AsyncMock(wraps=session.rollback))
    if internal:
        response = await client.patch(
            f"/internal/workflows/{wf_id}/edit-document",
            json={
                "base_revision": compute_workflow_edit_revision(document),
                "patch_ops": [
                    {
                        "op": "replace",
                        "path": "/metadata/title",
                        "value": "Updated synthetic title",
                    }
                ],
            },
        )
    else:
        document.metadata.title = "Updated synthetic title"
        response = await client.put(
            f"/workflows/{wf_id}/draft",
            json={"document": document.model_dump(mode="json", exclude_unset=True)},
        )
    assert response.status_code == (409 if known else 500), response.text
    assert ERROR_MARKER not in response.text
    cast(AsyncMock, session.rollback).assert_awaited()


@pytest.mark.parametrize("surface", ["create", "rename", "move"])
@pytest.mark.parametrize("known", [False, True])
async def test_folder_writes_only_map_path_constraint(
    client: httpx.AsyncClient,
    session: AsyncSession,
    svc_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    surface: str,
    known: bool,
) -> None:
    service = WorkflowFolderService(session, svc_role)
    folder = await service.create_folder("source")
    parent = await service.create_folder("parent")
    error = integrity_error("uq_workflow_folder_path_workspace" if known else None)
    monkeypatch.setattr(session, "commit", AsyncMock(side_effect=error))
    monkeypatch.setattr(session, "rollback", AsyncMock(wraps=session.rollback))
    if surface == "create":
        response = await client.post("/folders", json={"name": "child"})
    elif surface == "rename":
        response = await client.patch(f"/folders/{folder.id}", json={"name": "renamed"})
    else:
        response = await client.post(
            f"/folders/{folder.id}/move", json={"new_parent_path": parent.path}
        )
    assert response.status_code == (409 if known else 500), response.text
    assert ERROR_MARKER not in response.text
    cast(AsyncMock, session.rollback).assert_awaited()


@pytest.fixture
async def committed_workflow(svc_role: Role) -> AsyncGenerator[WorkflowUUID, None]:
    async with get_async_session_context_manager() as session:
        workflow = Workflow(
            workspace_id=svc_role.workspace_id,
            title="Concurrent workflow",
            description="",
        )
        session.add(workflow)
        await session.commit()
        wf_id = WorkflowUUID.new(workflow.id)
    try:
        yield wf_id
    finally:
        async with get_async_session_context_manager() as session:
            await session.execute(delete(Workflow).where(Workflow.id == wf_id))
            await session.commit()


@pytest.mark.parametrize("resource", ["action", "webhook"])
async def test_parent_lock_serializes_concurrent_creates(
    committed_workflow: WorkflowUUID, svc_role: Role, resource: str
) -> None:
    wf_id = committed_workflow
    async with (
        get_async_session_context_manager() as first,
        get_async_session_context_manager() as second,
    ):
        await first.scalar(
            select(Workflow.id).where(Workflow.id == wf_id).with_for_update()
        )

        async def create(session: AsyncSession) -> None:
            if resource == "action":
                await WorkflowActionService(session, svc_role).create_action(
                    ActionCreate(
                        workflow_id=wf_id,
                        type="core.transform.reshape",
                        title="Step One",
                    )
                )
            else:
                await create_webhook(
                    role=svc_role,
                    session=session,
                    workflow_id=wf_id,
                    params=WebhookCreate(),
                )

        loser = asyncio.create_task(create(second))
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(loser), timeout=0.2)
            await create(first)
            with pytest.raises(TracecatConflictError):
                await asyncio.wait_for(loser, timeout=10)
            await second.rollback()
            model = Action if resource == "action" else Webhook
            assert (
                await first.scalar(
                    select(func.count())
                    .select_from(model)
                    .where(model.workflow_id == wf_id)
                )
                == 1
            )
        finally:
            if not loser.done():
                loser.cancel()
                await asyncio.gather(loser, return_exceptions=True)


async def test_webhook_auto_repair_rechecks_after_parent_lock(
    committed_workflow: WorkflowUUID, svc_role: Role
) -> None:
    wf_id = committed_workflow
    async with (
        get_async_session_context_manager() as first,
        get_async_session_context_manager() as second,
    ):
        await first.scalar(
            select(Workflow.id).where(Workflow.id == wf_id).with_for_update()
        )
        management = WorkflowsManagementService(second, svc_role)
        repair = asyncio.create_task(management.get_workflow(wf_id))
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(repair), timeout=0.2)
            webhook = await create_webhook(
                role=svc_role, session=first, workflow_id=wf_id, params=WebhookCreate()
            )
            restored = await asyncio.wait_for(repair, timeout=10)
            assert restored is not None
            assert restored.webhook is not None
            assert restored.webhook.id == webhook.id
            await second.commit()
            assert (
                await first.scalar(
                    select(func.count())
                    .select_from(Webhook)
                    .where(Webhook.workflow_id == wf_id)
                )
                == 1
            )
        finally:
            if not repair.done():
                repair.cancel()
                await asyncio.gather(repair, return_exceptions=True)


def synthetic_dsl(refs: tuple[str, ...] = ("step",)) -> DSLInput:
    return DSLInput.model_validate(
        {
            "title": "Synthetic workflow",
            "description": "",
            "entrypoint": {"ref": refs[0]},
            "actions": [
                {
                    "ref": ref,
                    "action": "core.workflow.execute",
                    "args": {"workflow_alias": "synthetic-child"},
                }
                for ref in refs
            ],
        }
    )


async def test_bulk_actions_reject_normalized_ref_collisions(
    session: AsyncSession, workflow: Workflow, svc_role: Role
) -> None:
    dsl = synthetic_dsl(("step_one", "step__one"))
    with pytest.raises(TracecatValidationError, match="normalization"):
        await WorkflowsManagementService(session, svc_role).create_actions_from_dsl(
            dsl, workflow.id
        )
    assert (
        await session.scalar(
            select(func.count())
            .select_from(Action)
            .where(Action.workflow_id == workflow.id)
        )
        == 0
    )


@pytest.mark.parametrize(
    "stage",
    ["transaction", "workflow_validation", "registry_validation", "child_validation"],
)
async def test_import_diagnostics_do_not_expose_unexpected_errors(
    svc_role: Role, monkeypatch: pytest.MonkeyPatch, stage: str
) -> None:
    session = MagicMock()
    session.info = {}
    session.rollback = AsyncMock()
    session.begin_nested.return_value.__aenter__ = AsyncMock()
    session.begin_nested.return_value.__aexit__ = AsyncMock(return_value=False)
    importer = WorkflowImportService(session, svc_role)
    remote = RemoteWorkflowDefinition(
        id=WorkflowUUID.new_uuid4().short(), definition=synthetic_dsl()
    )
    error = integrity_error()
    if stage == "transaction":
        monkeypatch.setattr(importer, "validate_workflows", AsyncMock(return_value=[]))
        monkeypatch.setattr(importer, "import_workflows", AsyncMock(side_effect=error))
        result = await importer.import_workflows_atomic([remote], "a" * 40)
        assert result.success is False
        diagnostics = result.diagnostics
        session.rollback.assert_awaited_once()
    elif stage == "workflow_validation":
        monkeypatch.setattr(
            importer.wf_mgmt, "get_workflow", AsyncMock(side_effect=error)
        )
        diagnostics = await importer._validate_single_workflow(remote)
    elif stage == "registry_validation":
        monkeypatch.setattr(
            RegistryLockService,
            "resolve_lock_with_bindings",
            AsyncMock(side_effect=error),
        )
        diagnostics = await importer._validate_registry_actions([remote])
    else:
        monkeypatch.setattr(
            importer.wf_mgmt, "resolve_workflow_alias", AsyncMock(side_effect=error)
        )
        diagnostics = await importer._validate_cross_workflow_integrity([remote])
    assert len(diagnostics) == 1
    assert diagnostics[0].details == {}
    assert ERROR_MARKER not in json.dumps(asdict(diagnostics[0]))


@pytest.mark.parametrize("known", [False, True])
@pytest.mark.parametrize("phase", [1, 2])
async def test_case_tag_sync_only_maps_known_constraints(
    svc_role: Role, monkeypatch: pytest.MonkeyPatch, known: bool, phase: int
) -> None:
    session = MagicMock()
    session.scalars = AsyncMock(return_value=MagicMock(all=lambda: []))
    error = integrity_error("uq_case_tag_name_workspace" if known else None)
    session.flush = AsyncMock(side_effect=error if phase == 1 else [None, error])
    service = MagicMock(session=session, workspace_id=svc_role.workspace_id)
    adapter = CaseTagAdapter()
    monkeypatch.setattr(adapter, "plan_name_swap", AsyncMock())
    monkeypatch.setattr(adapter, "local_ids_by_source_id", AsyncMock(return_value={}))
    spec = WorkspaceSpec(
        case_tags={
            "synthetic-tag": CaseTagResourceSpec(
                id="synthetic-tag", name="Synthetic tag"
            )
        }
    )
    with pytest.raises(ValueError if known else IntegrityError) as raised:
        await adapter.import_specs(service, spec)
    if not known:
        assert raised.value is error


async def test_definition_versions_are_serialized(
    committed_workflow: WorkflowUUID, svc_role: Role
) -> None:
    wf_id = committed_workflow
    async with (
        get_async_session_context_manager() as first,
        get_async_session_context_manager() as second,
    ):
        await first.scalar(
            select(Workflow.id).where(Workflow.id == wf_id).with_for_update()
        )
        second_service = WorkflowDefinitionsService(second, svc_role)
        next_version = asyncio.create_task(
            second_service.create_workflow_definition(wf_id, synthetic_dsl())
        )
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(asyncio.shield(next_version), timeout=0.2)
            initial = await WorkflowDefinitionsService(
                first, svc_role
            ).create_workflow_definition(wf_id, synthetic_dsl())
            following = await asyncio.wait_for(next_version, timeout=10)
            assert (initial.version, following.version) == (1, 2)
        finally:
            if not next_version.done():
                next_version.cancel()
                await asyncio.gather(next_version, return_exceptions=True)


async def test_definition_create_requires_parent(
    session: AsyncSession, svc_role: Role
) -> None:
    with pytest.raises(TracecatNotFoundError):
        await WorkflowDefinitionsService(session, svc_role).create_workflow_definition(
            WorkflowUUID.new_uuid4(), synthetic_dsl()
        )


@pytest.mark.parametrize(
    "field",
    [
        "title",
        "description",
        "status",
        "position_x",
        "position_y",
        "upstream_edges",
        "control_flow",
    ],
)
async def test_action_update_rejects_required_nulls(
    client: httpx.AsyncClient,
    session: AsyncSession,
    workflow: Workflow,
    svc_role: Role,
    field: str,
) -> None:
    wf_id = WorkflowUUID.new(workflow.id)
    action = await WorkflowActionService(session, svc_role).create_action(
        ActionCreate(
            workflow_id=wf_id, title="Synthetic action", type="core.transform.reshape"
        )
    )
    response = await client.post(
        f"/actions/{action.id}", params={"workflow_id": str(wf_id)}, json={field: None}
    )
    assert response.status_code == 422, response.text
    await session.refresh(action)
    assert action.title == "Synthetic action"
    assert action.description == ""
    assert action.status == "offline"
    assert action.position_x == action.position_y == 0
    assert action.upstream_edges == []
    assert action.control_flow is not None


@pytest.mark.parametrize("field", ["title", "description", "status"])
async def test_workflow_update_rejects_required_nulls(
    client: httpx.AsyncClient, session: AsyncSession, workflow: Workflow, field: str
) -> None:
    response = await client.patch(f"/workflows/{workflow.id}", json={field: None})
    assert response.status_code == 422, response.text
    await session.refresh(workflow)
    assert workflow.title == "Synthetic workflow"
    assert workflow.description == ""
    assert workflow.status == "offline"


@pytest.mark.parametrize("field", ["status", "include_headers"])
async def test_webhook_update_rejects_required_nulls(
    client: httpx.AsyncClient,
    session: AsyncSession,
    workflow: Workflow,
    svc_role: Role,
    field: str,
) -> None:
    webhook = await create_webhook(
        role=svc_role,
        session=session,
        workflow_id=WorkflowUUID.new(workflow.id),
        params=WebhookCreate(),
    )
    response = await client.patch(
        f"/workflows/{workflow.id}/webhook", json={field: None}
    )
    assert response.status_code == 400, response.text
    await session.refresh(webhook)
    assert webhook.status == "offline"
    assert webhook.include_headers is False


async def test_webhook_nullable_lists_still_clear(
    client: httpx.AsyncClient,
    session: AsyncSession,
    workflow: Workflow,
    svc_role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        config, "TRACECAT__SIGNING_SECRET", "synthetic-webhook-signing-secret"
    )
    await create_webhook(
        role=svc_role,
        session=session,
        workflow_id=WorkflowUUID.new(workflow.id),
        params=WebhookCreate(methods=["POST"], allowlisted_cidrs=["192.0.2.0/24"]),
    )
    response = await client.patch(
        f"/workflows/{workflow.id}/webhook",
        json={"methods": None, "allowlisted_cidrs": None},
    )
    assert response.status_code == 200, response.text
    assert response.json()["methods"] == []
    assert response.json()["allowlisted_cidrs"] == []
