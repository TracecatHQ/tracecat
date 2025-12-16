from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, status

from tracecat.auth.dependencies import ExecutorWorkspaceRole
from tracecat.db.dependencies import AsyncDBSession
from tracecat.exceptions import TracecatNotFoundError
from tracecat.identifiers import SecretID
from tracecat.secrets.enums import SecretType
from tracecat.secrets.schemas import SecretRead, SecretReadMinimal, SecretSearch
from tracecat.secrets.service import SecretsService

router = APIRouter(
    prefix="/internal/secrets", tags=["internal-secrets"], include_in_schema=False
)


@router.get("/search", response_model=list[SecretRead])
async def executor_search_secrets(
    *,
    role: ExecutorWorkspaceRole,
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
    _ = decrypted
    return [SecretRead.from_database(secret) for secret in secrets]


@router.get("", response_model=list[SecretReadMinimal])
async def executor_list_secrets(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    types: set[SecretType] | None = Query(
        None, alias="type", description="Filter by secret type"
    ),
) -> list[SecretReadMinimal]:
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


@router.get("/{secret_name}", response_model=SecretRead)
async def executor_get_secret_by_name(
    *,
    role: ExecutorWorkspaceRole,
    session: AsyncDBSession,
    secret_name: str,
) -> SecretRead:
    service = SecretsService(session, role=role)
    try:
        secret = await service.get_secret_by_name(secret_name)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret not found"
        ) from e
    return SecretRead.from_database(secret)
