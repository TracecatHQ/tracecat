"""Forms are listed in the Triggers tab from the forms API, never as triggers.

No trigger path accepts a form: there is no ``form`` trigger kind, and a
form's ID is not a trigger ID, so every route answers 422 or 404, never 500.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import Form, Trigger, TriggerBinding, Workflow, Workspace
from tracecat.forms.enums import FormStatus
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.pagination import PageParams
from tracecat.triggers.enums import (
    TriggerErrorCode,
    TriggerKind,
    TriggerStatus,
    TriggerTargetType,
)
from tracecat.triggers.exceptions import TriggerError
from tracecat.triggers.schemas import (
    TriggerBindingCreate,
    TriggerBindingUpdate,
    TriggerCreate,
    TriggerUpdate,
    WorkflowBindingTarget,
)
from tracecat.triggers.service import TriggersService

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]


@pytest.fixture(autouse=True)
def entitled(monkeypatch: pytest.MonkeyPatch) -> None:
    async def _entitled(self: TriggersService, entitlement: object) -> bool:
        return True

    monkeypatch.setattr(TriggersService, "has_entitlement", _entitled)


@pytest.fixture
def service(session: AsyncSession, svc_role: Role) -> TriggersService:
    return TriggersService(session, svc_role)


@pytest.fixture
async def form(session: AsyncSession, svc_workspace: Workspace) -> Form:
    workflow = Workflow(
        workspace_id=svc_workspace.id, title="Phishing triage", description=""
    )
    session.add(workflow)
    await session.flush()
    form = Form(
        workspace_id=svc_workspace.id,
        workflow_id=workflow.id,
        status=FormStatus.ONLINE.value,
        title="Report phishing",
    )
    session.add(form)
    await session.commit()
    return form


async def _trigger_rows(
    session: AsyncSession, workspace_id: uuid.UUID
) -> tuple[int, int]:
    triggers = await session.scalar(
        select(func.count()).where(Trigger.workspace_id == workspace_id)
    )
    bindings = await session.scalar(
        select(func.count()).where(TriggerBinding.workspace_id == workspace_id)
    )
    return triggers or 0, bindings or 0


def test_there_is_no_form_trigger_kind() -> None:
    assert "form" not in {kind.value for kind in TriggerKind}
    with pytest.raises(ValueError):
        TriggerKind("form")


@pytest.mark.parametrize(
    "model",
    [TriggerCreate, TriggerUpdate],
    ids=["create", "update"],
)
def test_a_form_config_is_refused_as_invalid_input(model: type[Any]) -> None:
    """FastAPI answers these with 422 before any service code runs."""
    config = {"kind": "form", "workflow_id": str(uuid.uuid4())}
    with pytest.raises(ValidationError):
        model.model_validate({"name": "Report phishing", "config": config})


async def test_the_trigger_list_does_not_list_forms(
    service: TriggersService, session: AsyncSession, form: Form
) -> None:
    page = await service.list_triggers(PageParams(limit=50))

    assert page.items == []
    assert page.locked == []
    assert await _trigger_rows(session, form.workspace_id) == (0, 0)


async def test_no_trigger_path_finds_a_form_by_its_id(
    service: TriggersService, session: AsyncSession, form: Form
) -> None:
    binding_id = uuid.uuid4()
    calls = [
        service.get_trigger(form.id),
        service.update_trigger(form.id, TriggerUpdate(status=TriggerStatus.OFFLINE)),
        service.delete_trigger(form.id),
        service.create_binding(
            form.id,
            TriggerBindingCreate(
                target=WorkflowBindingTarget(
                    type=TriggerTargetType.WORKFLOW,
                    workflow_id=WorkflowUUID.new(form.workflow_id),
                )
            ),
        ),
        service.update_binding(
            form.id, binding_id, TriggerBindingUpdate(status=TriggerStatus.OFFLINE)
        ),
        service.delete_binding(form.id, binding_id),
    ]
    for call in calls:
        with pytest.raises(TriggerError) as excinfo:
            await call
        assert excinfo.value.code is TriggerErrorCode.NOT_FOUND

    # The form is untouched and no trigger rows were made for it.
    await session.refresh(form)
    assert form.status == FormStatus.ONLINE.value
    assert await _trigger_rows(session, form.workspace_id) == (0, 0)
