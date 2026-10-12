"""Tests for the form, form_submission and form_event tables."""

import uuid

import pytest
from sqlalchemy import Table, delete, func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import (
    Form,
    FormEvent,
    FormSubmission,
    Workflow,
    Workspace,
    WorkspaceModel,
)
from tracecat.db.tenant_rls import POST_RLS_WORKSPACE_SCOPED_TABLES
from tracecat.forms.enums import FormEventType, FormStatus

pytestmark = pytest.mark.usefixtures("db")

FORM_MODELS = (Form, FormSubmission, FormEvent)


def test_form_enum_values() -> None:
    assert [status.value for status in FormStatus] == ["online", "offline"]
    assert [event.value for event in FormEventType] == [
        "form_viewed",
        "form_submitted",
    ]


@pytest.mark.parametrize("model", FORM_MODELS)
def test_form_tables_are_workspace_scoped_and_rls_registered(
    model: type[WorkspaceModel],
) -> None:
    assert issubclass(model, WorkspaceModel)
    assert model.__tablename__ in POST_RLS_WORKSPACE_SCOPED_TABLES


def test_workflow_has_no_form_relationship() -> None:
    # A form can carry inline images; it must not load with every workflow.
    assert "form" not in Workflow.__mapper__.relationships
    for model in FORM_MODELS:
        assert not model.__mapper__.relationships


def test_form_indexes() -> None:
    def index_columns(model: type[WorkspaceModel]) -> dict[str, list[str]]:
        table = model.__table__
        assert isinstance(table, Table)
        return {
            str(index.name): [column.name for column in index.columns]
            for index in table.indexes
        }

    assert index_columns(Form) == {
        "ix_form_id": ["id"],
        "ix_form_workspace_status": ["workspace_id", "status"],
    }
    assert index_columns(FormSubmission) == {
        "ix_form_submission_id": ["id"],
        "ix_form_submission_form_created": [
            "workspace_id",
            "form_id",
            "created_at",
            "surrogate_id",
        ],
        "ix_form_submission_user_created": [
            "submitted_by_user_id",
            "created_at",
            "surrogate_id",
        ],
    }
    assert index_columns(FormEvent) == {
        "ix_form_event_id": ["id"],
        "ix_form_event_form_created": [
            "workspace_id",
            "form_id",
            "created_at",
            "surrogate_id",
        ],
        "ix_form_event_view_dedupe": [
            "workspace_id",
            "form_id",
            "type",
            "user_id",
            "created_at",
        ],
    }


def test_form_event_type_is_a_plain_string_column() -> None:
    # Not a native enum, so a new event type needs no migration.
    assert FormEvent.__table__.c.type.type.python_type is str


@pytest.fixture
async def workflow(session: AsyncSession, svc_workspace: Workspace) -> Workflow:
    workflow = Workflow(
        title="Form workflow",
        description="Test workflow",
        status="offline",
        workspace_id=svc_workspace.id,
        config={},
    )
    session.add(workflow)
    await session.commit()
    return workflow


@pytest.fixture
async def form(session: AsyncSession, workflow: Workflow) -> Form:
    form = Form(workspace_id=workflow.workspace_id, workflow_id=workflow.id)
    session.add(form)
    await session.commit()
    return form


async def _count(session: AsyncSession, model: type[WorkspaceModel]) -> int:
    result = await session.execute(select(func.count()).select_from(model))
    return result.scalar_one()


@pytest.mark.anyio
async def test_form_defaults(session: AsyncSession, form: Form) -> None:
    await session.refresh(form)

    assert isinstance(form.id, uuid.UUID)
    assert form.status == FormStatus.OFFLINE
    assert form.title is None
    assert form.description is None
    assert form.success_message is None
    assert form.layout is None


@pytest.mark.anyio
async def test_one_form_per_workflow(session: AsyncSession, form: Form) -> None:
    session.add(Form(workspace_id=form.workspace_id, workflow_id=form.workflow_id))

    with pytest.raises(IntegrityError):
        await session.commit()


@pytest.mark.anyio
async def test_layout_round_trips_in_order(session: AsyncSession, form: Form) -> None:
    layout = [
        {"notice": "Reviewed within 2 business days.", "tone": "info"},
        {"field": "model", "widget": "cards"},
        {"heading": "Delivery"},
    ]
    form.layout = layout
    await session.commit()
    await session.refresh(form)

    assert form.layout == layout


@pytest.mark.anyio
async def test_deleting_a_submission_keeps_its_event(
    session: AsyncSession, form: Form
) -> None:
    submission = FormSubmission(
        workspace_id=form.workspace_id,
        form_id=form.id,
        answers={"model": "mba"},
        definition_version=1,
        wf_exec_id="wf_synthetic/exec_synthetic",
    )
    session.add(submission)
    await session.flush()
    event = FormEvent(
        workspace_id=form.workspace_id,
        form_id=form.id,
        type=FormEventType.FORM_SUBMITTED.value,
        submission_id=submission.id,
    )
    session.add(event)
    await session.commit()

    await session.execute(
        delete(FormSubmission).where(FormSubmission.id == submission.id)
    )
    await session.commit()
    await session.refresh(event)

    assert event.submission_id is None
    assert event.type == FormEventType.FORM_SUBMITTED


@pytest.mark.anyio
async def test_deleting_the_workflow_removes_the_form_and_its_rows(
    session: AsyncSession, workflow: Workflow, form: Form
) -> None:
    submission = FormSubmission(
        workspace_id=form.workspace_id,
        form_id=form.id,
        answers={},
        definition_version=1,
        wf_exec_id="wf_synthetic/exec_synthetic",
    )
    session.add(submission)
    session.add(
        FormEvent(
            workspace_id=form.workspace_id,
            form_id=form.id,
            type=FormEventType.FORM_VIEWED.value,
        )
    )
    await session.commit()

    await session.execute(delete(Workflow).where(Workflow.id == workflow.id))
    await session.commit()

    for model in FORM_MODELS:
        assert await _count(session, model) == 0
