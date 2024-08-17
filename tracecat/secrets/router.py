from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError, NoResultFound
from sqlmodel import select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat.auth.credentials import (
    authenticate_user_for_workspace,
    authenticate_user_or_service_for_workspace,
)
from tracecat.db.engine import get_async_session
from tracecat.db.schemas import Secret
from tracecat.identifiers import SecretID
from tracecat.logging import logger
from tracecat.secrets.models import (
    CreateSecretParams,
    SearchSecretsParams,
    SecretResponse,
    UpdateSecretParams,
)
from tracecat.secrets.service import SecretsService
from tracecat.types.auth import Role

router = APIRouter(prefix="/secrets")


@router.get("", tags=["secrets"])
async def list_secrets(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    session: AsyncSession = Depends(get_async_session),
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
async def get_secret(
    # NOTE(auth): Worker service can also access secrets
    role: Annotated[Role, Depends(authenticate_user_or_service_for_workspace)],
    secret_name: str,
    session: AsyncSession = Depends(get_async_session),
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
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    params: CreateSecretParams,
    session: AsyncSession = Depends(get_async_session),
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
#     role: Annotated[Role, Depends(authenticate_user_for_workspace)],
#     secret_name: str,
#     params: UpdateSecretParams,
#     session: AsyncSession = Depends(get_async_session),
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
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    secret_id: SecretID,
    params: UpdateSecretParams,
    session: AsyncSession = Depends(get_async_session),
) -> None:
    """Update a secret by ID."""
    service = SecretsService(session, role)
    try:
        await service.update_secret_by_name(secret_id=secret_id, params=params)
    except NoResultFound as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret does not exist"
        ) from e
    except IntegrityError as e:
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
#     role: Annotated[Role, Depends(authenticate_user_for_workspace)],
#     secret_name: str,
#     session: AsyncSession = Depends(get_async_session),
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
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    secret_id: SecretID,
    session: AsyncSession = Depends(get_async_session),
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


@router.post("/search", tags=["secrets"])
async def search_secrets(
    role: Annotated[Role, Depends(authenticate_user_for_workspace)],
    params: SearchSecretsParams,
    session: AsyncSession = Depends(get_async_session),
) -> list[Secret]:
    """**[WORK IN PROGRESS]**   Get a secret by ID."""
    statement = (
        select(Secret)
        .where(Secret.owner_id == role.workspace_id)
        .filter(*[Secret.name == name for name in params.names])
    )
    result = await session.exec(statement)
    secrets = result.all()
    return secrets
