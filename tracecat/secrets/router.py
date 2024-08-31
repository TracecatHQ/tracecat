from fastapi import APIRouter, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError, NoResultFound

from tracecat.auth.dependencies import WorkspaceUserOrServiceRole, WorkspaceUserRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.schemas import Secret
from tracecat.identifiers import SecretID
from tracecat.logging import logger
from tracecat.secrets.models import (
    CreateSecretParams,
    SecretResponse,
    UpdateSecretParams,
)
from tracecat.secrets.service import SecretsService

router = APIRouter(prefix="/secrets")


@router.get("/search", tags=["secrets"], response_model=list[Secret])
async def search_secrets(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    name: list[str] | None = Query(None),
    id: list[SecretID] | None = Query(None),
    env: list[str] | None = Query(None),
) -> list[Secret]:
    """Search secrets."""
    service = SecretsService(session, role=role)
    secrets = await service.search_secrets(names=name, ids=id, environment=env)
    return secrets


@router.get("", tags=["secrets"])
async def list_secrets(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
) -> list[SecretResponse]:
    """List user secrets."""
    service = SecretsService(session, role)
    secrets = await service.list_secrets()
    return [
        SecretResponse(
            id=secret.id,
            type=secret.type,
            name=secret.name,
            description=secret.description,
            keys=[kv.key for kv in service.decrypt_keys(secret.encrypted_keys)],
        )
        for secret in secrets
    ]


@router.get("/{secret_name}", tags=["secrets"])
async def get_secret_by_name(
    # NOTE(auth): Worker service can also access secrets
    role: WorkspaceUserOrServiceRole,
    session: AsyncDBSession,
    secret_name: str,
) -> Secret:
    """Get a secret."""

    service = SecretsService(session, role=role)
    secret = await service.get_secret_by_name(secret_name)
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret not found"
        )
    # NOTE: IMPLICIT TYPE COERCION
    # Encrypted keys as bytes gets cast a string as to be JSON serializable
    return secret


@router.post("", status_code=status.HTTP_201_CREATED, tags=["secrets"])
async def create_secret(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    params: CreateSecretParams,
) -> None:
    """Create a secret."""
    service = SecretsService(session, role)
    try:
        await service.create_secret(params)
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Secret already exists"
        ) from e


# @router.post("/{secret_name}", status_code=status.HTTP_201_CREATED, tags=["secrets"])
# async def update_secret(
#     role: WorkspaceUserRole,
#     session: AsyncDBSession,
#     secret_name: str,
#     params: UpdateSecretParams,
# ) -> Secret:
#     """Update a secret by name."""
#     service = SecretsService(session, role)
#     try:
#         await service.update_secret_by_name(secret_name=secret_name, params=params)
#     except NoResultFound as e:
#         raise HTTPException(
#             status_code=status.HTTP_404_NOT_FOUND, detail="Secret does not exist"
#         ) from e
#     except IntegrityError as e:
#         raise HTTPException(
#             status_code=status.HTTP_409_CONFLICT, detail="Secret already exists"
#         ) from e


@router.post("/{secret_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["secrets"])
async def update_secret_by_id(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    secret_id: SecretID,
    params: UpdateSecretParams,
) -> None:
    """Update a secret by ID."""
    service = SecretsService(session, role)
    try:
        await service.update_secret_by_id(secret_id=secret_id, params=params)
    except NoResultFound as e:
        logger.error("Secret not found", secret_id=secret_id)
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret does not exist"
        ) from e
    except IntegrityError as e:
        logger.info("Secret already exists", secret_id=secret_id)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Secret already exists"
        ) from e


# XXX: If we are to support this, it could have the following behavior:
# - If tags passed, match tags
# - If no tags passed, tries to delete all secrets with this name
# @router.delete(
#     "/{secret_name}", status_code=status.HTTP_204_NO_CONTENT, tags=["secrets"]
# )
# async def delete_secret_by_name(
#     role: WorkspaceUserRole,
#     session: AsyncDBSession,
#     secret_name: str,
# ) -> None:
#     """Delete a secret."""
#     service = SecretsService(session, role=role)
#     try:
#         await service.delete_secret_by_name(secret_name)
#     except NoResultFound as e:
#         logger.error(f"Secret {secret_name=} not found")
#         raise HTTPException(
#             status_code=status.HTTP_404_NOT_FOUND, detail="Secret does not exist"
#         ) from e


@router.delete("/{secret_id}", status_code=status.HTTP_204_NO_CONTENT, tags=["secrets"])
async def delete_secret_by_id(
    role: WorkspaceUserRole,
    session: AsyncDBSession,
    secret_id: SecretID,
) -> None:
    """Delete a secret by ID."""
    service = SecretsService(session, role=role)
    try:
        await service.delete_secret_by_id(secret_id)
    except NoResultFound as e:
        logger.info(f"Secret {secret_id=} not found")
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret does not exist"
        ) from e
