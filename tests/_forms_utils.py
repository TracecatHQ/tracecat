"""Helpers for forms tests. Fixtures built on them live in tests/unit/conftest.py."""

import hashlib
import uuid
from collections.abc import AsyncIterator, Callable
from contextlib import AbstractAsyncContextManager, asynccontextmanager
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import AsyncMock

from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import Role
from tracecat.authz.scopes import INTAKE_SCOPES
from tracecat.db.models import (
    Form,
    FormFile,
    FormSubmission,
    Organization,
    OrganizationMembership,
    User,
    Workflow,
    Workspace,
)
from tracecat.dsl.common import DSLInput
from tracecat.forms.enums import FormStatus
from tracecat.identifiers.workflow import WorkflowUUID
from tracecat.storage import blob
from tracecat.workflow.management.definitions import WorkflowDefinitionsService

READER_SCOPES = INTAKE_SCOPES | {"form:submission:read"}
PDF = b"%PDF-1.4\n1 0 obj\n<<>>\nendobj\ntrailer\n<<>>\n%%EOF\n"


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
        session,
        role=role.model_copy(
            update={"scopes": frozenset({"*"}), "workspace_id": workflow.workspace_id}
        ),
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


async def create_submission(
    session: AsyncSession,
    form: Form,
    *,
    user_id: uuid.UUID | None = None,
    answers: dict[str, Any] | None = None,
) -> FormSubmission:
    submission = FormSubmission(
        workspace_id=form.workspace_id,
        form_id=form.id,
        submitted_by_user_id=user_id,
        answers=answers or {},
        definition_version=1,
        wf_exec_id=f"{WorkflowUUID.new(form.workflow_id).short()}/exec_synthetic",
    )
    session.add(submission)
    await session.commit()
    return submission


async def create_file(
    session: AsyncSession,
    form: Form,
    submission: FormSubmission | None = None,
    *,
    content: bytes = PDF,
) -> FormFile:
    file = FormFile(
        workspace_id=form.workspace_id,
        form_id=form.id,
        submission_id=submission.id if submission is not None else None,
        field="quote",
        name="quote.pdf",
        content_type="application/pdf",
        size=len(content),
        sha256=hashlib.sha256(content).hexdigest(),
    )
    session.add(file)
    await session.commit()
    return file


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


@dataclass
class FakeTemporal:
    """Stands in for ``WorkflowExecutionsService.connect``."""

    connect: AsyncMock
    service: AsyncMock

    @property
    def start(self) -> AsyncMock:
        return self.service.create_workflow_execution_wait_for_start

    @property
    def run_role(self) -> Role:
        self.connect.assert_awaited_once()
        assert self.connect.await_args is not None
        return self.connect.await_args.kwargs["role"]

    @property
    def start_kwargs(self) -> dict[str, Any]:
        self.start.assert_awaited_once()
        assert self.start.await_args is not None
        return dict(self.start.await_args.kwargs)


@dataclass
class FakeBlobStore:
    """In-memory stand-in for the functions of ``tracecat.storage.blob``."""

    objects: dict[str, bytes] = field(default_factory=dict)
    content_types: dict[str, str | None] = field(default_factory=dict)
    presigned: list[dict[str, Any]] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    fail_uploads: bool = False

    async def upload_file(
        self,
        content: bytes,
        key: str,
        bucket: str,
        content_type: str | None = None,
        *,
        redact_log_identifiers: bool = False,
    ) -> None:
        if self.fail_uploads:
            raise blob.StorageUploadError(error_code="SlowDown")
        self.objects[key] = content
        self.content_types[key] = content_type

    async def download_file(
        self, key: str, bucket: str, *, redact_log_identifiers: bool = False
    ) -> bytes:
        if key not in self.objects:
            raise FileNotFoundError
        return self.objects[key]

    async def delete_file(
        self, key: str, bucket: str, *, redact_log_identifiers: bool = False
    ) -> None:
        """A key holding ``fail`` raises, after it is recorded."""
        self.deleted.append(key)
        if "fail" in key:
            raise blob.StorageDeleteError(error_code="SlowDown")
        self.objects.pop(key, None)

    async def generate_presigned_download_url(self, **kwargs: Any) -> str:
        self.presigned.append(kwargs)
        return f"https://storage.example.com/{kwargs['key']}?signature=synthetic"
