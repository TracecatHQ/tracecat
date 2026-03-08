from typing import Annotated

from fastapi import APIRouter, HTTPException, status

from tracecat.auth.credentials import RoleACL
from tracecat.auth.dependencies import OrgUserRole
from tracecat.auth.types import Role
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.secrets.sync.schemas import (
    AwsCredentialSyncConfigRead,
    AwsCredentialSyncConfigUpdate,
    CredentialSyncResult,
)
from tracecat.secrets.sync.service import CredentialSyncService

org_router = APIRouter(
    prefix="/organization/secrets/sync/aws",
    tags=["secrets"],
)
workspace_router = APIRouter(
    prefix="/workspaces/{workspace_id}/secrets/sync/aws",
    tags=["secrets"],
)

WorkspaceUserInPath = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
        workspace_id_in_path=True,
    ),
]


@org_router.get("", response_model=AwsCredentialSyncConfigRead)
@require_scope("org:credential-sync:manage")
async def get_aws_credential_sync_config(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
) -> AwsCredentialSyncConfigRead:
    service = CredentialSyncService(session, role=role)
    return await service.get_aws_config()


@org_router.patch("", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:credential-sync:manage")
async def update_aws_credential_sync_config(
    *,
    role: OrgUserRole,
    session: AsyncDBSession,
    params: AwsCredentialSyncConfigUpdate,
) -> None:
    service = CredentialSyncService(session, role=role)
    try:
        await service.update_aws_config(params)
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=str(exc),
        ) from exc


@workspace_router.post("/push", response_model=CredentialSyncResult)
@require_scope("org:credential-sync:manage")
async def push_aws_credential_sync(
    *,
    role: WorkspaceUserInPath,
    session: AsyncDBSession,
) -> CredentialSyncResult:
    service = CredentialSyncService(session, role=role)
    try:
        return await service.push_aws_credentials()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc


@workspace_router.post("/pull", response_model=CredentialSyncResult)
@require_scope("org:credential-sync:manage")
async def pull_aws_credential_sync(
    *,
    role: WorkspaceUserInPath,
    session: AsyncDBSession,
) -> CredentialSyncResult:
    service = CredentialSyncService(session, role=role)
    try:
        return await service.pull_aws_credentials()
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=str(exc),
        ) from exc
