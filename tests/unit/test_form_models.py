"""The form tables: workspace RLS and one form per workflow.

Cascades on delete are covered by the deletion tests in test_forms_files.py.
"""

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tests._forms_utils import create_form, create_workflow
from tracecat.db.models import (
    Form,
    FormFile,
    FormSubmission,
    FormView,
    Workspace,
    WorkspaceModel,
)
from tracecat.db.tenant_rls import POST_RLS_WORKSPACE_SCOPED_TABLES


@pytest.mark.parametrize("model", [Form, FormSubmission, FormView, FormFile])
def test_form_tables_are_workspace_scoped_and_rls_registered(
    model: type[WorkspaceModel],
) -> None:
    assert issubclass(model, WorkspaceModel)
    assert model.__tablename__ in POST_RLS_WORKSPACE_SCOPED_TABLES


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
async def test_one_form_per_workflow(
    session: AsyncSession, svc_workspace: Workspace
) -> None:
    form = await create_form(session, await create_workflow(session, svc_workspace.id))
    session.add(Form(workspace_id=form.workspace_id, workflow_id=form.workflow_id))

    with pytest.raises(IntegrityError):
        await session.commit()
