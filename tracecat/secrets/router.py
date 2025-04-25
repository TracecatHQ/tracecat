from typing import Annotated, Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError

from tracecat.auth.credentials import RoleACL
from tracecat.authz.models import WorkspaceRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.identifiers import SecretID
from tracecat.logger import logger
from tracecat.secrets.enums import SecretType
from tracecat.secrets.models import (
    SecretCreate,
    SecretRead,
    SecretReadMinimal,
    SecretSearch,
    SecretUpdate,
)
from tracecat.secrets.service import SecretsService
from tracecat.types.auth import AccessLevel, Role
from tracecat.types.exceptions import TracecatNotFoundError

router = APIRouter(prefix="/secrets", tags=["secrets"])
org_router = APIRouter(prefix="/organization/secrets", tags=["organization-secrets"])

WorkspaceUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
        require_workspace_roles=[WorkspaceRole.EDITOR, WorkspaceRole.ADMIN],
    ),
]

WorkspaceAdminUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="yes",
        require_workspace_roles=WorkspaceRole.ADMIN,
    ),
]

OrgAdminUser = Annotated[
    Role,
    RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="no",
        min_access_level=AccessLevel.ADMIN,
    ),
]


@router.get("/search", response_model=list[SecretRead])
async def search_secrets(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    environment: str = Query(...),
    names: set[str] | None = Query(
        None, alias="name", description="Filter by secret name"
    ),
    ids: set[SecretID] | None = Query(
        None, alias="id", description="Filter by secret ID"
    ),
    types: set[SecretType] | None = Query(
        None, alias="type", description="Filter by secret type"
    ),
) -> list[SecretRead]:
    """Search secrets."""
    service = SecretsService(session, role=role)
    params: dict[str, Any] = {"environment": environment}
    if names:
        params["names"] = names
    if ids:
        params["ids"] = ids
    if types:
        params["types"] = types
    secrets = await service.search_secrets(SecretSearch(**params))
    decrypted = []
    for secret in secrets:
        decrypted.extend(service.decrypt_keys(secret.encrypted_keys))
    return [SecretRead.from_database(secret) for secret in secrets]


@router.get("")
async def list_secrets(
    *,
    role: WorkspaceUser,
    session: AsyncDBSession,
    types: set[SecretType] | None = Query(
        None, alias="type", description="Filter by secret type"
    ),
) -> list[SecretReadMinimal]:
    """List user secrets."""
    service = SecretsService(session, role=role)
    secrets = await service.list_secrets(types=types)
    return [
        SecretReadMinimal(
            id=secret.id,
            type=SecretType(secret.type),
            name=secret.name,
            description=secret.description,
            keys=[kv.key for kv in service.decrypt_keys(secret.encrypted_keys)],
            environment=secret.environment,
        )
        for secret in secrets
    ]


@router.get("/{secret_name}")
async def get_secret_by_name(
    *,
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    secret_name: str,
) -> SecretRead:
    """Get a secret."""

    service = SecretsService(session, role=role)
    try:
        secret = await service.get_secret_by_name(secret_name)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret not found"
        ) from e
    return SecretRead.from_database(secret)


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_secret(
    *,
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    params: SecretCreate,
) -> None:
    """Create a secret."""
    service = SecretsService(session, role=role)
    try:
        await service.create_secret(params)
    except IntegrityError as e:
        logger.error("Secret integrity error", e=str(e))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Secret creation integrity error: {e!r}",
        ) from e


@router.post("/{secret_id}", status_code=status.HTTP_204_NO_CONTENT)
async def update_secret_by_id(
    *,
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    secret_id: SecretID,
    params: SecretUpdate,
) -> None:
    """Update a secret by ID."""
    service = SecretsService(session, role)
    try:
        secret = await service.get_secret(secret_id)
        await service.update_secret(secret, params)
    except TracecatNotFoundError as e:
        logger.error("Secret not found", secret_id=secret_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret does not exist"
        ) from e
    except IntegrityError as e:
        logger.info("Secret already exists", secret_id=secret_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Secret already exists"
        ) from e


@router.delete("/{secret_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_secret_by_id(
    *,
    role: WorkspaceAdminUser,
    session: AsyncDBSession,
    secret_id: SecretID,
) -> None:
    """Delete a secret by ID."""
    service = SecretsService(session, role=role)
    try:
        secret = await service.get_secret(secret_id)
        await service.delete_secret(secret)
    except TracecatNotFoundError as e:
        logger.info(f"Secret {secret_id=} not found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret does not exist"
        ) from e


@org_router.get("")
async def list_org_secrets(
    *,
    role: OrgAdminUser,
    session: AsyncDBSession,
    types: set[SecretType] | None = Query(
        None, alias="type", description="Filter by secret type"
    ),
) -> list[SecretReadMinimal]:
    """List organization secrets."""
    service = SecretsService(session, role=role)
    secrets = await service.list_org_secrets(types=types)
    return [
        SecretReadMinimal(
            id=secret.id,
            type=SecretType(secret.type),
            name=secret.name,
            description=secret.description,
            keys=[kv.key for kv in service.decrypt_keys(secret.encrypted_keys)],
            environment=secret.environment,
        )
        for secret in secrets
    ]


@org_router.get("/{secret_name}")
async def get_org_secret_by_name(
    *,
    role: OrgAdminUser,
    session: AsyncDBSession,
    secret_name: str,
    environment: str | None = Query(None),
) -> SecretRead:
    """Get an organization secret by name."""
    service = SecretsService(session, role=role)
    try:
        secret = await service.get_org_secret_by_name(secret_name, environment)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization secret not found",
        ) from e
    return SecretRead.from_database(secret)


@org_router.post("", status_code=status.HTTP_201_CREATED)
async def create_org_secret(
    *,
    role: OrgAdminUser,
    session: AsyncDBSession,
    params: SecretCreate,
) -> None:
    """Create an organization secret."""
    service = SecretsService(session, role=role)
    try:
        await service.create_org_secret(params)
    except IntegrityError as e:
        logger.error("Organization secret integrity error", e=str(e))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Organization secret creation integrity error: {e!r}",
        ) from e


@org_router.post(
    "/{secret_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def update_org_secret_by_id(
    *,
    role: OrgAdminUser,
    session: AsyncDBSession,
    secret_id: SecretID,
    params: SecretUpdate,
) -> None:
    """Update an organization secret by ID."""
    service = SecretsService(session, role)
    try:
        secret = await service.get_org_secret(secret_id)
        await service.update_org_secret(secret, params)
    except TracecatNotFoundError as e:
        logger.error("Organization secret not found", secret_id=secret_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization secret does not exist",
        ) from e
    except IntegrityError as e:
        logger.info("Organization secret already exists", secret_id=secret_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Organization secret already exists",
        ) from e


@org_router.delete(
    "/{secret_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_org_secret_by_id(
    *,
    role: OrgAdminUser,
    session: AsyncDBSession,
    secret_id: SecretID,
) -> None:
    """Delete an organization secret by ID."""
    service = SecretsService(session, role=role)
    try:
        secret = await service.get_org_secret(secret_id)
        await service.delete_org_secret(secret)
    except TracecatNotFoundError as e:
        logger.info(f"Organization secret {secret_id=} not found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization secret does not exist",
        ) from e
