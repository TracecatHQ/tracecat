"""The form detail route returns raw config, so requesters must not reach it."""

import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tests._forms_utils import create_form, create_workflow
from tracecat.auth.types import Role
from tracecat.authz.scopes import EDITOR_SCOPES, INTAKE_SCOPES, VIEWER_SCOPES
from tracecat.db.models import Form, FormEvent, Workspace
from tracecat.forms import router as forms_router
from tracecat.forms.enums import FormStatus
from tracecat.forms.schemas import FormFieldBlock

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]


def _role(workspace: Workspace, scopes: frozenset[str]) -> Role:
    return Role(
        type="user",
        workspace_id=workspace.id,
        organization_id=workspace.organization_id,
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
        scopes=scopes,
    )


async def _form(
    session: AsyncSession, workspace: Workspace, status: FormStatus
) -> Form:
    workflow = await create_workflow(session, workspace.id)
    return await create_form(session, workflow, status=status, title="Laptop request")


@pytest.mark.parametrize("status", [FormStatus.ONLINE, FormStatus.OFFLINE])
async def test_intake_only_caller_gets_404(
    session: AsyncSession, svc_workspace: Workspace, status: FormStatus
) -> None:
    form = await _form(session, svc_workspace, status)

    with pytest.raises(HTTPException) as exc_info:
        await forms_router.get_form(
            role=_role(svc_workspace, INTAKE_SCOPES), session=session, form_id=form.id
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Form not found"


async def test_intake_only_caller_cannot_probe_for_form_ids(
    session: AsyncSession, svc_workspace: Workspace
) -> None:
    """An unknown ID answers exactly like a real one."""
    with pytest.raises(HTTPException) as exc_info:
        await forms_router.get_form(
            role=_role(svc_workspace, INTAKE_SCOPES),
            session=session,
            form_id=uuid.uuid4(),
        )

    assert exc_info.value.status_code == 404
    assert exc_info.value.detail == "Form not found"


@pytest.mark.parametrize(
    "scopes", [VIEWER_SCOPES, EDITOR_SCOPES], ids=["viewer", "editor"]
)
async def test_viewer_and_editor_read_the_config(
    session: AsyncSession, svc_workspace: Workspace, scopes: frozenset[str]
) -> None:
    form = await _form(session, svc_workspace, FormStatus.OFFLINE)

    read = await forms_router.get_form(
        role=_role(svc_workspace, scopes), session=session, form_id=form.id
    )

    assert read.id == form.id
    assert read.config.title == "Laptop request"


@pytest.mark.parametrize(
    "scopes", [VIEWER_SCOPES, EDITOR_SCOPES], ids=["viewer", "editor"]
)
async def test_preview_resolves_an_offline_form_without_recording_a_view(
    session: AsyncSession, svc_workspace: Workspace, scopes: frozenset[str]
) -> None:
    """A viewer cannot run /validate, so the saved config has its own preview."""
    workflow = await create_workflow(
        session,
        svc_workspace.id,
        draft_expects={"model": {"type": "str"}, "notes": {"type": "str"}},
    )
    form = await create_form(
        session,
        workflow,
        status=FormStatus.OFFLINE,
        title="Laptop request",
        layout=[{"field": "notes", "widget": "long_text"}],
    )

    preview = await forms_router.preview_form(
        role=_role(svc_workspace, scopes), session=session, form_id=form.id
    )

    assert preview.title == "Laptop request"
    assert preview.definition_version is None
    fields = [
        block.name for block in preview.blocks if isinstance(block, FormFieldBlock)
    ]
    assert fields == ["notes", "model"]
    events = await session.execute(
        select(FormEvent).where(FormEvent.form_id == form.id)
    )
    assert events.scalars().all() == []


async def test_preview_is_hidden_from_intake_only_callers(
    session: AsyncSession, svc_workspace: Workspace
) -> None:
    form = await _form(session, svc_workspace, FormStatus.ONLINE)

    for form_id in (form.id, uuid.uuid4()):
        with pytest.raises(HTTPException) as exc_info:
            await forms_router.preview_form(
                role=_role(svc_workspace, INTAKE_SCOPES),
                session=session,
                form_id=form_id,
            )

        assert exc_info.value.status_code == 404
        assert exc_info.value.detail == "Form not found"
