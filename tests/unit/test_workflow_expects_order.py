"""Tests for preserving the author's key order of workflow inputs.

PostgreSQL JSONB does not keep object key order, so the order is stored as a
list next to ``expects`` and reapplied on read. The list is internal: it must
never appear in dumps, exports, Temporal payloads or the OpenAPI schema.
"""

import uuid
from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.api.app import app
from tracecat.auth.types import Role
from tracecat.db.models import Workflow
from tracecat.dsl._converter import PydanticPayloadConverter
from tracecat.dsl.common import (
    DSLEntrypoint,
    DSLInput,
    DSLRunArgs,
    apply_expects_order,
    expects_key_order,
)
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.management.definitions import WorkflowDefinitionsService
from tracecat.workflow.management.draft import (
    build_workflow_edit_document,
    compute_workflow_edit_revision,
    persist_workflow_edit_document,
    replace_workflow_definition_from_dsl,
    workflow_edit_document_changed_sections,
)
from tracecat.workflow.management.management import WorkflowsManagementService
from tracecat.workflow.management.router import (
    _build_workflow_read,
    get_workflow_definition,
    list_workflow_definitions,
)
from tracecat.workflow.management.schemas import (
    ExternalWorkflowDefinition,
    WorkflowCreate,
    WorkflowUpdate,
)
from tracecat.workspace_sync.serialization import serialize_yaml_model

pytestmark = pytest.mark.usefixtures("db")

# JSONB orders object keys by length and then bytewise: a, mm, zeta.
AUTHOR_ORDER = ["zeta", "mm", "a"]
JSONB_ORDER = ["a", "mm", "zeta"]
EXPECTS: dict[str, dict[str, Any]] = {
    "zeta": {"type": "str"},
    "mm": {"type": "int", "default": 1},
    "a": {"type": "bool", "default": False},
}
STORED_EXPECTS = {name: EXPECTS[name] for name in JSONB_ORDER}


def _dsl_data() -> dict[str, Any]:
    return {
        "title": "Input order workflow",
        "description": "Test workflow",
        "entrypoint": {"ref": "start", "expects": EXPECTS},
        "actions": [{"ref": "start", "action": "core.transform.reshape"}],
    }


def _stored_dsl() -> DSLInput:
    """A definition as it comes back from JSONB: sorted keys plus the order."""
    entrypoint = {"ref": "start", "expects": STORED_EXPECTS}
    return DSLInput.model_validate(
        _dsl_data() | {"entrypoint": entrypoint | {"expects_order": AUTHOR_ORDER}}
    )


def test_apply_and_capture_expects_order() -> None:
    expects = {"a": 1, "b": 2, "c": 3}

    assert apply_expects_order(None, ["a"]) is None
    assert apply_expects_order(expects, None) == expects
    # Names no longer in the schema are ignored; unlisted names keep their place
    # after the listed ones. A malformed stored order is ignored.
    assert list(apply_expects_order(expects, ["c", "gone", "a"]) or {}) == list("cab")
    assert list(apply_expects_order(expects, ["c", 5]) or {}) == list("abc")
    assert expects_key_order(EXPECTS) == AUTHOR_ORDER
    assert expects_key_order({}) is None
    assert expects_key_order(None) is None


@pytest.mark.parametrize(
    "order",
    ["zeta", 5, {"zeta": 0}, ["zeta", 5], [["zeta"]], [{"name": "zeta"}]],
    ids=repr,
)
def test_malformed_stored_order_is_ignored(order: Any) -> None:
    """The order is a hint. A bad one must not stop a run."""
    entrypoint = {"ref": "start", "expects": STORED_EXPECTS, "expects_order": order}

    dsl = DSLInput.model_validate(_dsl_data() | {"entrypoint": entrypoint})

    assert list(dsl.entrypoint.expects or {}) == JSONB_ORDER


def _temporal_payload(dsl: DSLInput) -> bytes:
    args = DSLRunArgs(
        role=Role(
            type="service", service_id="tracecat-runner", workspace_id=uuid.uuid4()
        ),
        dsl=dsl,
        wf_id=WorkflowUUID.new_uuid4(),
    )
    [payload] = PydanticPayloadConverter().to_payloads([args])
    return payload.data


DUMPS: dict[str, Callable[[DSLInput], Any]] = {
    "model_dump": lambda dsl: dsl.model_dump(),
    "json": lambda dsl: dsl.model_dump_json(),
    "model_construct": lambda dsl: DSLEntrypoint.model_construct(
        ref="start", expects=None, expects_order=AUTHOR_ORDER
    ).model_dump_json(),
    # Same call as the workflow export route.
    "export_json": lambda dsl: ExternalWorkflowDefinition(
        definition=dsl
    ).model_dump_json(exclude_none=True),
    "workspace_sync": serialize_yaml_model,
    "temporal_payload": _temporal_payload,
    "json_schema": lambda dsl: [
        DSLInput.model_json_schema(mode=mode)
        for mode in ("validation", "serialization")
    ],
    "openapi": lambda dsl: app.openapi(),
}


@pytest.mark.parametrize("dump", DUMPS.values(), ids=DUMPS.keys())
def test_expects_order_never_leaves_the_model(dump: Callable[[DSLInput], Any]) -> None:
    assert "expects_order" not in str(dump(_stored_dsl()))


@pytest.fixture
async def workflow(session: AsyncSession, svc_role: Role) -> Workflow:
    service = WorkflowsManagementService(session, role=svc_role)
    return await service.create_workflow(WorkflowCreate(title="Input order workflow"))


@pytest.mark.anyio
async def test_definition_routes_return_input_order_without_the_key(
    session: AsyncSession, svc_role: Role, workflow: Workflow
) -> None:
    service = WorkflowDefinitionsService(session, role=svc_role)
    workflow_id = WorkflowUUID.new(workflow.id)
    dsl = DSLInput.model_validate(_dsl_data())
    await service.create_workflow_definition(workflow_id, dsl)
    session.expire_all()

    read = await get_workflow_definition(
        role=svc_role, session=session, workflow_id=workflow_id
    )
    listed = await list_workflow_definitions(
        role=svc_role, session=session, workflow_id=workflow_id
    )

    for definition in [read, *listed]:
        assert definition.content is not None
        entrypoint = definition.content["entrypoint"]
        assert "expects_order" not in entrypoint
        assert list(entrypoint["expects"]) == AUTHOR_ORDER


async def _update_workflow(
    service: WorkflowsManagementService, workflow: Workflow, role: Role
) -> None:
    await service.update_workflow(
        WorkflowUUID.new(workflow.id),
        WorkflowUpdate.model_validate({"expects": EXPECTS}),
    )


async def _replace_from_dsl(
    service: WorkflowsManagementService, workflow: Workflow, role: Role
) -> None:
    await replace_workflow_definition_from_dsl(
        service=service, workflow=workflow, dsl=DSLInput.model_validate(_dsl_data())
    )


async def _save_edit_document(
    service: WorkflowsManagementService, workflow: Workflow, role: Role
) -> None:
    """The chat and MCP workflow editors both save through the edit document.

    With actions it saves through ``replace_workflow_definition_from_dsl``.
    """
    original = build_workflow_edit_document(workflow)
    entrypoint = DSLEntrypoint.model_validate(
        {"ref": original.definition.entrypoint.ref, "expects": EXPECTS}
    )
    definition = original.definition.model_copy(update={"entrypoint": entrypoint})
    await persist_workflow_edit_document(
        role=role,
        service=service,
        workflow=workflow,
        original_document=original,
        updated_document=original.model_copy(update={"definition": definition}),
    )


@pytest.mark.anyio
@pytest.mark.parametrize(
    "write",
    [_update_workflow, _replace_from_dsl, _save_edit_document],
    ids=lambda write: write.__name__,
)
async def test_every_draft_write_keeps_input_order(
    session: AsyncSession,
    svc_role: Role,
    workflow: Workflow,
    write: Callable[[WorkflowsManagementService, Workflow, Role], Awaitable[None]],
) -> None:
    service = WorkflowsManagementService(session, role=svc_role)
    workflow_id = WorkflowUUID.new(workflow.id)

    await write(service, workflow, svc_role)
    await session.commit()
    session.expire_all()

    loaded = await service.get_workflow(workflow_id)
    assert loaded is not None
    assert list(loaded.expects) == JSONB_ORDER
    assert loaded.expects_order == AUTHOR_ORDER
    read = _build_workflow_read(loaded)
    reads = [
        read.expects,
        (read.expects_schema or {})["properties"],
        build_workflow_edit_document(loaded).definition.entrypoint.expects,
    ]
    if loaded.actions:
        reads.append((await service.build_dsl_from_workflow(loaded)).entrypoint.expects)
    for expects in reads:
        assert list(expects or {}) == AUTHOR_ORDER


@pytest.mark.anyio
async def test_reordering_inputs_is_an_edit_that_persists(
    session: AsyncSession, svc_role: Role, workflow: Workflow
) -> None:
    service = WorkflowsManagementService(session, role=svc_role)
    workflow_id = WorkflowUUID.new(workflow.id)
    await _update_workflow(service, workflow, svc_role)
    await session.commit()
    loaded = await service.get_workflow(workflow_id)
    assert loaded is not None
    original = build_workflow_edit_document(loaded)
    reversed_order = list(reversed(AUTHOR_ORDER))
    entrypoint = DSLEntrypoint.model_validate(
        {
            "ref": original.definition.entrypoint.ref,
            "expects": {name: EXPECTS[name] for name in reversed_order},
        }
    )
    definition = original.definition.model_copy(update={"entrypoint": entrypoint})
    updated = original.model_copy(update={"definition": definition})

    # Mapping equality and sorted-key hashing would both miss a reorder.
    assert "definition" in workflow_edit_document_changed_sections(original, updated)
    assert compute_workflow_edit_revision(updated) != compute_workflow_edit_revision(
        original
    )
    await persist_workflow_edit_document(
        role=svc_role,
        service=service,
        workflow=loaded,
        original_document=original,
        updated_document=updated,
    )
    await session.commit()
    session.expire_all()

    reloaded = await service.get_workflow(workflow_id)
    assert reloaded is not None
    assert reloaded.expects_order == reversed_order
