from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from tracecat.auth.credentials import authenticate_user, authenticate_user_or_service
from tracecat.db.engine import get_session
from tracecat.db.schemas import Secret
from tracecat.secrets.service import SecretsService
from tracecat.types.api import (
    CreateSecretParams,
    SearchSecretsParams,
    SecretResponse,
    UpdateSecretParams,
)
from tracecat.types.auth import Role

router = APIRouter(prefix="/secrets")


@router.get("", tags=["secrets"])
def list_secrets(
    role: Annotated[Role, Depends(authenticate_user)],
    session: Session = Depends(get_session),
) -> list[SecretResponse]:
    """List user secrets."""
    service = SecretsService(session, role)
    secrets = service.list_secrets()
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
def get_secret(
    # NOTE(auth): Worker service can also access secrets
    role: Annotated[Role, Depends(authenticate_user_or_service)],
    secret_name: str,
    session: Session = Depends(get_session),
) -> Secret:
    """Get a secret."""

    # Check if secret exists
    statement = (
        select(Secret)
        .where(Secret.owner_id == role.user_id, Secret.name == secret_name)
        .limit(1)
    )
    result = session.exec(statement)
    secret = result.one_or_none()
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret not found"
        )
    # NOTE: IMPLICIT TYPE COERCION
    # Encrypted keys as bytes gets cast a string as to be JSON serializable
    return secret


@router.post("", status_code=status.HTTP_201_CREATED, tags=["secrets"])
def create_secret(
    role: Annotated[Role, Depends(authenticate_user)],
    params: CreateSecretParams,
    session: Session = Depends(get_session),
) -> None:
    """Create a secret."""
    service = SecretsService(session, role)
    secret = service.get_secret_by_name(params.name)
    if secret is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Secret already exists"
        )

    new_secret = Secret(
        owner_id=role.user_id,
        name=params.name,
        type=params.type,
        description=params.description,
        tags=params.tags,
        encrypted_keys=service.encrypt_keys(params.keys),
    )
    service.create_secret(new_secret)


@router.post("/{secret_name}", status_code=status.HTTP_201_CREATED, tags=["secrets"])
def update_secret(
    role: Annotated[Role, Depends(authenticate_user)],
    secret_name: str,
    params: UpdateSecretParams,
    session: Session = Depends(get_session),
) -> Secret:
    """Update a secret"""
    service = SecretsService(session, role)
    secret = service.get_secret_by_name(secret_name)
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret does not exist"
        )
    maybe_clashing_secret = service.get_secret_by_name(params.name)
    if maybe_clashing_secret is not None and maybe_clashing_secret.id != secret.id:
        name = maybe_clashing_secret.name
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Secret with name {name} already exists",
        )
    return service.update_secret(secret, params)


@router.delete(
    "/{secret_name}", status_code=status.HTTP_204_NO_CONTENT, tags=["secrets"]
)
def delete_secret(
    role: Annotated[Role, Depends(authenticate_user)],
    secret_name: str,
    session: Session = Depends(get_session),
) -> None:
    """Delete a secret."""
    service = SecretsService(session, role)
    secret = service.get_secret_by_name(secret_name)
    if secret is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret does not exist"
        )
    service.delete_secret(secret)


@router.post("/search", tags=["secrets"])
def search_secrets(
    role: Annotated[Role, Depends(authenticate_user)],
    params: SearchSecretsParams,
    session: Session = Depends(get_session),
) -> list[Secret]:
    """**[WORK IN PROGRESS]**   Get a secret by ID."""
    statement = (
        select(Secret)
        .where(Secret.owner_id == role.user_id)
        .filter(*[Secret.name == name for name in params.names])
    )
    result = session.exec(statement)
    secrets = result.all()
    return secrets
