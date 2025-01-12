from typing import Any

from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError

from tracecat.auth.credentials import RoleACL
from tracecat.db.dependencies import AsyncDBSession
from tracecat.identifiers import SecretID
from tracecat.logger import logger
from tracecat.secrets.enums import SecretLevel, SecretType
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

router = APIRouter(prefix="/secrets")


@router.get("/search", tags=["secrets"], response_model=list[SecretRead])
async def search_secrets(
    *,
    # If workspace_id is passed here, it signals that the user wishes to search inside a workspace
    # If this dependency passes, the user is authorized to access the workspace secrets
    # The role returned indicates the scope of resources that the user can access
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="optional",
        min_access_level=AccessLevel.ADMIN,
    ),
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
    levels: set[SecretLevel] | None = Query(
        None, alias="level", description="Filter by secret level"
    ),
) -> list[SecretRead]:
    """Search secrets."""
    if not role.workspace_id and (
        levels and any(lv == SecretLevel.WORKSPACE for lv in levels)
    ):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="This role is cannot access workspace secrets",
        )
    service = SecretsService(session, role=role)
    params: dict[str, Any] = {"environment": environment}
    if names:
        params["names"] = names
    if ids:
        params["ids"] = ids
    if types:
        params["types"] = types
    if levels:
        params["levels"] = levels
    secrets = await service.search_secrets(SecretSearch(**params))
    decrypted = []
    for secret in secrets:
        decrypted.extend(service.decrypt_keys(secret.encrypted_keys))
    return [SecretRead.from_database(secret) for secret in secrets]


@router.get("", tags=["secrets"])
async def list_secrets(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="optional",
        min_access_level=AccessLevel.ADMIN,
    ),
    session: AsyncDBSession,
    # Visibility is determined by the role ACL.
    # Filters on returned secrets. This is separate from the visibility
    types: set[SecretType] | None = Query(
        None, alias="type", description="Filter by secret type"
    ),
    level: SecretLevel = Query(
        SecretLevel.WORKSPACE, description="Filter by secret level"
    ),
) -> list[SecretReadMinimal]:
    """List user secrets."""
    service = SecretsService(session, role=role)

    match level:
        case SecretLevel.WORKSPACE:
            secrets = await service.list_secrets(types=types)
        case SecretLevel.ORGANIZATION:
            secrets = await service.list_org_secrets(types=types)
        case _:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid level"
            )
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


@router.get("/{secret_name}", tags=["secrets"])
async def get_secret_by_name(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="optional",
        min_access_level=AccessLevel.ADMIN,
    ),
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
    # NOTE: IMPLICIT TYPE COERCION
    # Encrypted keys as bytes gets cast a string as to be JSON serializable
    return SecretRead.from_database(secret)


@router.post("", status_code=status.HTTP_201_CREATED, tags=["secrets"])
async def create_secret(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="optional",
        min_access_level=AccessLevel.ADMIN,
    ),
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


@router.post("/{secret_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["secrets"])
async def update_secret_by_id(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="optional",
        min_access_level=AccessLevel.ADMIN,
    ),
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


@router.delete("/{secret_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["secrets"])
async def delete_secret_by_id(
    *,
    role: Role = RoleACL(
        allow_user=True,
        allow_service=False,
        require_workspace="optional",
        min_access_level=AccessLevel.ADMIN,
    ),
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
