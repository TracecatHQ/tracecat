from __future__ import annotations

from unittest.mock import AsyncMock, Mock

import pytest
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.api.workflowservice.v1 import StartWorkflowExecutionResponse
from temporalio.client import Client
from temporalio.service import ConnectConfig, ServiceClient

from tests._forms_utils import (
    READER_SCOPES,
    FakeBlobStore,
    FakeTemporal,
    create_org_user,
)
from tracecat import config
from tracecat.auth.types import Role
from tracecat.authz.scopes import ADMIN_SCOPES, INTAKE_SCOPES
from tracecat.db.models import ServiceAccount, User, Workspace
from tracecat.dsl._converter import get_data_converter
from tracecat.storage import blob
from tracecat.workflow.executions.service import WorkflowExecutionsService


@pytest.fixture
def temporal_start_client() -> tuple[Client, AsyncMock]:
    """Exercise Temporal request encoding while mocking only the start RPC."""
    service = Mock(spec=ServiceClient)
    service.config = ConnectConfig(target_host="localhost:7233", identity="test-client")
    rpc = AsyncMock(return_value=StartWorkflowExecutionResponse(run_id="run-1"))
    service._rpc_call = rpc
    return Client(service, data_converter=get_data_converter()), rpc


@pytest.fixture
def smtp_configured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__SMTP_HOST", "smtp.example.com")
    monkeypatch.setattr(config, "TRACECAT__SMTP_PORT", 587)
    monkeypatch.setattr(config, "TRACECAT__SMTP_USER", "relay")
    monkeypatch.setattr(config, "TRACECAT__SMTP_PASSWORD", "secret")
    monkeypatch.setattr(
        config, "TRACECAT__EMAIL_FROM", "Tracecat <no-reply@example.com>"
    )


@pytest.fixture
def smtp_unconfigured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__SMTP_HOST", None)
    monkeypatch.setattr(config, "TRACECAT__SMTP_USER", None)
    monkeypatch.setattr(config, "TRACECAT__SMTP_PASSWORD", None)
    monkeypatch.setattr(config, "TRACECAT__EMAIL_FROM", None)


# ----- Forms -----


@pytest.fixture
def temporal(monkeypatch: pytest.MonkeyPatch) -> FakeTemporal:
    """Replace the Temporal connection that form submissions start runs with."""
    service = AsyncMock(spec=WorkflowExecutionsService)
    service.query_executions.return_value = []
    connect = AsyncMock(return_value=service)
    monkeypatch.setattr(WorkflowExecutionsService, "connect", connect)
    return FakeTemporal(connect=connect, service=service)


@pytest.fixture
def blob_store(monkeypatch: pytest.MonkeyPatch) -> FakeBlobStore:
    store = FakeBlobStore()
    for name in (
        "upload_file",
        "download_file",
        "delete_file",
        "generate_presigned_download_url",
    ):
        monkeypatch.setattr(blob, name, getattr(store, name))
    return store


@pytest.fixture
async def requester(session: AsyncSession, svc_workspace: Workspace) -> User:
    return await create_org_user(
        session,
        svc_workspace.organization_id,
        email="maya.okafor@example.com",
        first_name="Maya",
        last_name="Okafor",
    )


@pytest.fixture
def intake_role(svc_workspace: Workspace, requester: User) -> Role:
    """The requester, holding nothing but the Intake only preset's scopes."""
    return Role(
        type="user",
        workspace_id=svc_workspace.id,
        organization_id=svc_workspace.organization_id,
        user_id=requester.id,
        service_id="tracecat-api",
        scopes=INTAKE_SCOPES,
    )


@pytest.fixture
def builder_role(intake_role: Role) -> Role:
    """The requester as a workspace admin."""
    return intake_role.model_copy(update={"scopes": ADMIN_SCOPES})


@pytest.fixture
async def colleague_role(
    session: AsyncSession, svc_workspace: Workspace, intake_role: Role
) -> Role:
    """Another requester of the workspace."""
    colleague = await create_org_user(
        session, svc_workspace.organization_id, email="arjun.rao@example.com"
    )
    return intake_role.model_copy(update={"user_id": colleague.id})


@pytest.fixture
def reader_role(colleague_role: Role) -> Role:
    """The colleague, who can also read submissions."""
    return colleague_role.model_copy(update={"scopes": READER_SCOPES})


@pytest.fixture
async def api_key_role(session: AsyncSession, svc_workspace: Workspace) -> Role:
    account = ServiceAccount(
        organization_id=svc_workspace.organization_id,
        workspace_id=svc_workspace.id,
        name="Intake bot",
    )
    session.add(account)
    await session.commit()
    return Role(
        type="service_account",
        workspace_id=svc_workspace.id,
        organization_id=svc_workspace.organization_id,
        service_account_id=account.id,
        service_id="tracecat-api",
        scopes=INTAKE_SCOPES,
    )
