"""Helpers for forms tests."""

import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.db.models import (
    Form,
    Organization,
    OrganizationMembership,
    User,
    Workflow,
    Workspace,
)
from tracecat.dsl.common import DSLInput
from tracecat.forms.enums import FormStatus
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.workflow.management.definitions import WorkflowDefinitionsService


def dsl_with_inputs(expects: dict[str, Any] | None) -> DSLInput:
    entrypoint: dict[str, Any] = {"ref": "start"}
    if expects:
        entrypoint["expects"] = expects
    return DSLInput.model_validate(
        {
            "title": "Provision laptop",
            "description": "Test workflow",
            "entrypoint": entrypoint,
            "actions": [{"ref": "start", "action": "core.transform.reshape"}],
        }
    )


async def create_workflow(
    session: AsyncSession,
    workspace_id: uuid.UUID,
    *,
    title: str = "Provision laptop",
    description: str = "Request a new laptop from IT.",
    draft_expects: dict[str, Any] | None = None,
) -> Workflow:
    """Create an unpublished workflow."""
    workflow = Workflow(
        title=title,
        description=description,
        status="offline",
        workspace_id=workspace_id,
        config={},
        expects=draft_expects or {},
        expects_order=list(draft_expects) if draft_expects else None,
    )
    session.add(workflow)
    await session.commit()
    return workflow


async def publish_workflow(
    session: AsyncSession, role: Role, workflow: Workflow, expects: dict[str, Any]
) -> int:
    """Commit a workflow definition with these inputs. Returns its version."""
    service = WorkflowDefinitionsService(
        session, role=role.model_copy(update={"scopes": frozenset({"*"})})
    )
    definition = await service.create_workflow_definition(
        WorkflowUUID.new(workflow.id), dsl_with_inputs(expects)
    )
    return definition.version


async def create_form(
    session: AsyncSession,
    workflow: Workflow,
    *,
    status: FormStatus = FormStatus.ONLINE,
    title: str | None = None,
    layout: list[dict[str, Any]] | None = None,
) -> Form:
    form = Form(
        workspace_id=workflow.workspace_id,
        workflow_id=workflow.id,
        status=status.value,
        title=title,
        layout=layout,
    )
    session.add(form)
    await session.commit()
    return form


async def create_org_user(
    session: AsyncSession,
    organization_id: uuid.UUID,
    *,
    email: str,
    first_name: str | None = None,
    last_name: str | None = None,
    is_active: bool = True,
) -> User:
    user = User(
        id=uuid.uuid4(),
        email=email,
        hashed_password="unused",
        first_name=first_name,
        last_name=last_name,
        is_active=is_active,
    )
    session.add(user)
    await session.flush()
    session.add(
        OrganizationMembership(user_id=user.id, organization_id=organization_id)
    )
    await session.commit()
    return user


async def create_organization_with_workspace(
    session: AsyncSession, *, name: str
) -> Workspace:
    organization = Organization(
        id=uuid.uuid4(), name=name, slug=f"{name}-{uuid.uuid4().hex[:8]}"
    )
    session.add(organization)
    await session.flush()
    workspace = Workspace(name=f"{name}-workspace", organization_id=organization.id)
    session.add(workspace)
    await session.commit()
    return workspace


def use_session(
    session: AsyncSession,
) -> Callable[[], AbstractAsyncContextManager[AsyncSession]]:
    """Stand in for a session factory so code under test sees the test's rows."""

    @asynccontextmanager
    async def manager() -> AsyncIterator[AsyncSession]:
        yield session

    return manager
