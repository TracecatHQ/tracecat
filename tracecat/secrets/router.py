from typing import Any
from uuid import UUID

from cryptography.fernet import InvalidToken
from fastapi import APIRouter, HTTPException, Query, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError

from tracecat import config
from tracecat.auth.dependencies import OrgActorRole, WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.models import OrganizationSecretStore
from tracecat.exceptions import (
    TracecatAuthorizationError,
    TracecatConflictError,
    TracecatNotFoundError,
)
from tracecat.identifiers import SecretID, WorkspaceID
from tracecat.integrations.aws_assume_role import (
    build_workspace_external_id,
    get_tracecat_aws_account_id,
    get_tracecat_aws_principal_arn,
)
from tracecat.logger import logger
from tracecat.registry.actions.service import RegistryActionsService
from tracecat.secrets.dependencies import AnySecretIDPath
from tracecat.secrets.enums import SecretSource, SecretType
from tracecat.secrets.schemas import (
    AwsAssumeRoleAccessRead,
    AwsSecretReferenceCreate,
    AwsSecretReferenceUpdate,
    OrganizationSecretRead,
    SecretCreate,
    SecretDefinition,
    SecretRead,
    SecretReadMinimal,
    SecretReferenceCheckResult,
    SecretSearch,
    SecretStoreAuthorizationCreate,
    SecretStoreAuthorizationRead,
    SecretStoreCreate,
    SecretStoreRead,
    SecretStoreUpdate,
    SecretUpdate,
    WorkspaceSecretStoreRead,
)
from tracecat.secrets.service import (
    SecretsService,
    is_external_reference,
    secret_key_names,
)
from tracecat.secrets.store_service import SecretStoresService

router = APIRouter(prefix="/secrets", tags=["secrets"])
org_router = APIRouter(prefix="/organization/secrets", tags=["organization-secrets"])
org_store_router = APIRouter(
    prefix="/organization/secret-stores", tags=["organization-secret-stores"]
)


def _serialize_secret_read_minimal(
    *,
    service: SecretsService,
    secret: Any,
) -> SecretReadMinimal:
    source = SecretSource.LOCAL
    store_id = None
    store_name = None
    remote_reference = None
    if is_external_reference(secret):
        source = SecretSource.AWS_SECRETS_MANAGER
        store_id = secret.store_id
        store_name = secret.store.name if secret.store is not None else None
        remote_reference = secret.remote_reference
    try:
        keys = secret_key_names(service, secret)
        is_corrupted = False
    except (InvalidToken, ValidationError, ValueError) as e:
        keys = []
        is_corrupted = True
        logger.warning(
            "Failed to decrypt secret keys; returning secret without keys",
            secret_id=str(secret.id),
            secret_name=secret.name,
            secret_type=secret.type,
            error=str(e),
        )

    return SecretReadMinimal(
        id=secret.id,
        type=SecretType(secret.type),
        name=secret.name,
        description=secret.description,
        keys=keys,
        environment=secret.environment,
        is_corrupted=is_corrupted,
        source=source,
        store_id=store_id,
        store_name=store_name,
        remote_reference=remote_reference,
    )


async def _serialize_store_read(
    service: SecretStoresService, store: OrganizationSecretStore
) -> SecretStoreRead:
    counts = await service.count_references([store.id])
    return SecretStoreRead.from_database(
        store,
        authorized_workspace_ids=[a.workspace_id for a in store.authorizations],
        reference_count=counts.get(store.id, 0),
        tracecat_aws_account_id=config.TRACECAT__AWS_ASSUME_ROLE_ACCOUNT_ID or None,
        tracecat_aws_principal_arn=config.TRACECAT__AWS_ASSUME_ROLE_PRINCIPAL_ARN
        or None,
    )


@router.get("/search", response_model=list[SecretRead])
@require_scope("secret:read")
async def search_secrets(
    *,
    role: WorkspaceActorRouteRole,
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
    return [SecretRead.from_database(secret) for secret in secrets]


@router.get("")
@require_scope("secret:read")
async def list_secrets(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    types: set[SecretType] | None = Query(
        None, alias="type", description="Filter by secret type"
    ),
) -> list[SecretReadMinimal]:
    """List user secrets."""
    service = SecretsService(session, role=role)
    secrets = await service.list_secrets(types=types)
    return [
        _serialize_secret_read_minimal(service=service, secret=secret)
        for secret in secrets
    ]


@router.get("/definitions", response_model=list[SecretDefinition])
@require_scope("secret:read")
async def list_secret_definitions(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
) -> list[SecretDefinition]:
    """List aggregated secret definitions from the registry."""
    service = RegistryActionsService(session, role=role)
    definitions = await service.get_aggregated_secrets()
    return sorted(
        definitions,
        key=lambda definition: (-definition.action_count, definition.name),
    )


@router.get("/aws-assume-role", response_model=AwsAssumeRoleAccessRead)
@require_scope("secret:read")
async def get_aws_assume_role_access(
    *,
    role: WorkspaceActorRouteRole,
) -> AwsAssumeRoleAccessRead:
    """Get workspace-scoped AWS AssumeRole details for credential setup."""
    workspace_id = role.workspace_id
    if workspace_id is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Workspace context is required",
        )

    try:
        return AwsAssumeRoleAccessRead(
            tracecat_aws_account_id=get_tracecat_aws_account_id(),
            tracecat_aws_principal_arn=get_tracecat_aws_principal_arn(),
            external_id=build_workspace_external_id(workspace_id),
        )
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="AWS AssumeRole access is not available right now.",
        ) from exc


@router.get("/stores", response_model=list[WorkspaceSecretStoreRead])
@require_scope("secret:read")
async def list_authorized_secret_stores(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
) -> list[WorkspaceSecretStoreRead]:
    """List external secret stores this workspace is authorized to reference."""
    service = SecretsService(session, role=role)
    stores = await service.list_authorized_stores()
    return [WorkspaceSecretStoreRead.from_database(store) for store in stores]


@router.post("/aws", status_code=status.HTTP_201_CREATED)
@require_scope("secret:create")
async def create_aws_secret_reference(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    params: AwsSecretReferenceCreate,
) -> None:
    """Create a custom secret whose values live in AWS Secrets Manager."""
    service = SecretsService(session, role=role)
    try:
        await service.create_aws_secret_reference(params)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    except TracecatAuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Secret already exists"
        ) from e


@router.post("/aws/{secret_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("secret:update")
async def update_aws_secret_reference(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    secret_id: AnySecretIDPath,
    params: AwsSecretReferenceUpdate,
) -> None:
    """Update the reference or key mapping of an AWS-backed secret."""
    service = SecretsService(session, role=role)
    try:
        secret = await service.get_secret(secret_id)
        await service.update_aws_secret_reference(secret, params)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    except TracecatAuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret does not exist"
        ) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Secret already exists"
        ) from e


@router.post("/aws/{secret_id}/check", response_model=SecretReferenceCheckResult)
@require_scope("secret:read")
async def check_aws_secret_reference(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    secret_id: AnySecretIDPath,
) -> SecretReferenceCheckResult:
    """Verify a saved AWS-backed reference resolves. Values are never returned."""
    service = SecretsService(session, role=role)
    try:
        secret = await service.get_secret(secret_id)
        return await service.check_aws_secret_reference(secret)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret does not exist"
        ) from e


@router.get("/{secret_name}")
@require_scope("secret:read")
async def get_secret_by_name(
    *,
    role: WorkspaceActorRouteRole,
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
@require_scope("secret:create")
async def create_secret(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    params: SecretCreate,
) -> None:
    """Create a secret."""
    service = SecretsService(session, role=role)
    try:
        await service.create_secret(params)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    except IntegrityError as e:
        logger.error("Secret integrity error", e=str(e))
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Secret creation integrity error: {e!r}",
        ) from e


@router.post("/{secret_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("secret:update")
async def update_secret_by_id(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    secret_id: AnySecretIDPath,
    params: SecretUpdate,
) -> None:
    """Update a secret by ID."""
    service = SecretsService(session, role)
    try:
        secret = await service.get_secret(secret_id)
        await service.update_secret(secret, params)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
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
@require_scope("secret:delete")
async def delete_secret_by_id(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    secret_id: AnySecretIDPath,
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
@require_scope("org:secret:read")
async def list_org_secrets(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    types: set[SecretType] | None = Query(
        None, alias="type", description="Filter by secret type"
    ),
) -> list[SecretReadMinimal]:
    """List organization secrets."""
    service = SecretsService(session, role=role)
    secrets = await service.list_org_secrets(types=types)
    return [
        _serialize_secret_read_minimal(service=service, secret=secret)
        for secret in secrets
    ]


@org_router.get("/{secret_name}")
@require_scope("org:secret:read")
async def get_org_secret_by_name(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    secret_name: str,
    environment: str | None = Query(None),
) -> OrganizationSecretRead:
    """Get an organization secret by name."""
    service = SecretsService(session, role=role)
    try:
        secret = await service.get_org_secret_by_name(secret_name, environment)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Organization secret not found",
        ) from e
    return OrganizationSecretRead.from_database(secret)


@org_router.post("", status_code=status.HTTP_201_CREATED)
@require_scope("org:secret:create")
async def create_org_secret(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    params: SecretCreate,
) -> None:
    """Create an organization secret."""
    service = SecretsService(session, role=role)
    try:
        await service.create_org_secret(params)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
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
@require_scope("org:secret:update")
async def update_org_secret_by_id(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    secret_id: AnySecretIDPath,
    params: SecretUpdate,
) -> None:
    """Update an organization secret by ID."""
    service = SecretsService(session, role)
    try:
        secret = await service.get_org_secret(secret_id)
        await service.update_org_secret(secret, params)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
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
@require_scope("org:secret:delete")
async def delete_org_secret_by_id(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    secret_id: AnySecretIDPath,
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


# === Organization secret stores ===


@org_store_router.get("")
@require_scope("org:secret:read")
async def list_secret_stores(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
) -> list[SecretStoreRead]:
    """List external secret stores owned by the organization."""
    service = SecretStoresService(session, role=role)
    stores = await service.list_stores()
    counts = await service.count_references([store.id for store in stores])
    return [
        SecretStoreRead.from_database(
            store,
            authorized_workspace_ids=[a.workspace_id for a in store.authorizations],
            reference_count=counts.get(store.id, 0),
            tracecat_aws_account_id=config.TRACECAT__AWS_ASSUME_ROLE_ACCOUNT_ID or None,
            tracecat_aws_principal_arn=config.TRACECAT__AWS_ASSUME_ROLE_PRINCIPAL_ARN
            or None,
        )
        for store in stores
    ]


@org_store_router.post("", status_code=status.HTTP_201_CREATED)
@require_scope("org:secret:create")
async def create_secret_store(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    params: SecretStoreCreate,
) -> SecretStoreRead:
    """Create a store. The AssumeRole external ID is generated server-side."""
    service = SecretStoresService(session, role=role)
    try:
        store = await service.create_store(params)
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A secret store with this name already exists",
        ) from e
    return await _serialize_store_read(service, store)


@org_store_router.get("/{store_id}")
@require_scope("org:secret:read")
async def get_secret_store(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    store_id: UUID,
) -> SecretStoreRead:
    """Get a store, including its persisted trust-policy inputs."""
    service = SecretStoresService(session, role=role)
    try:
        store = await service.get_store(store_id)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret store not found"
        ) from e
    return await _serialize_store_read(service, store)


@org_store_router.patch("/{store_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:secret:update")
async def update_secret_store(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    store_id: UUID,
    params: SecretStoreUpdate,
) -> None:
    """Update store metadata. The external ID never changes."""
    service = SecretStoresService(session, role=role)
    try:
        store = await service.get_store(store_id)
        await service.update_store(store, params)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret store not found"
        ) from e
    except IntegrityError as e:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="A secret store with this name already exists",
        ) from e


@org_store_router.delete("/{store_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("org:secret:delete")
async def delete_secret_store(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    store_id: UUID,
) -> None:
    """Delete a store. Rejected while workspace secrets still reference it."""
    service = SecretStoresService(session, role=role)
    try:
        store = await service.get_store(store_id)
        await service.delete_store(store)
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret store not found"
        ) from e
    except TracecatConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e


@org_store_router.post(
    "/{store_id}/authorizations", status_code=status.HTTP_201_CREATED
)
@require_scope("org:secret:update")
async def authorize_secret_store_workspace(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    store_id: UUID,
    params: SecretStoreAuthorizationCreate,
) -> SecretStoreAuthorizationRead:
    """Authorize a workspace to reference this store."""
    service = SecretStoresService(session, role=role)
    try:
        store = await service.get_store(store_id)
        authorization = await service.authorize_workspace(store, params.workspace_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    return SecretStoreAuthorizationRead.from_database(authorization)


@org_store_router.delete(
    "/{store_id}/authorizations/{workspace_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
@require_scope("org:secret:update")
async def revoke_secret_store_workspace(
    *,
    role: OrgActorRole,
    session: AsyncDBSession,
    store_id: UUID,
    workspace_id: WorkspaceID,
) -> None:
    """Revoke a workspace authorization. Rejected while references remain."""
    service = SecretStoresService(session, role=role)
    try:
        store = await service.get_store(store_id)
        await service.revoke_workspace(store, workspace_id)
    except TracecatNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e)) from e
    except TracecatConflictError as e:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(e)) from e
