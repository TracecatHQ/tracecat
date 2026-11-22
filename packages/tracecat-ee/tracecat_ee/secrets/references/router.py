"""Workspace secret reference API: secrets whose values live in an external store."""

from __future__ import annotations

from collections.abc import Awaitable
from datetime import timedelta
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.exc import IntegrityError
from temporalio.client import WorkflowFailureError

from tracecat import config
from tracecat.auth.dependencies import WorkspaceActorRouteRole
from tracecat.authz.controls import require_scope
from tracecat.db.dependencies import AsyncDBSession
from tracecat.db.errors import is_unique_violation
from tracecat.dsl.client import get_temporal_client
from tracecat.exceptions import TracecatAuthorizationError, TracecatNotFoundError
from tracecat.identifiers import SecretID
from tracecat.pagination import Page, PageParams, PaginationError
from tracecat.secrets.dependencies import AnySecretIDPath
from tracecat.secrets.enums import SecretStoreProvider
from tracecat.secrets.schemas import (
    AwsSecretReferenceCreate,
    AwsSecretReferenceUpdate,
    SecretReferenceCheckRequest,
    SecretReferenceCheckResult,
    SecretReferenceCreate,
    SecretReferenceUpdate,
    WorkspaceSecretStoreRead,
)
from tracecat.secrets.service import is_external_reference
from tracecat.temporal.fairness import org_priority
from tracecat.tiers.entitlements import check_entitlement
from tracecat.tiers.enums import Entitlement
from tracecat_ee.secrets.references.service import (
    NOT_AWS_MESSAGE,
    NOT_EXTERNAL_MESSAGE,
    SecretReferencesService,
)
from tracecat_ee.secrets.references.workflows import SecretReferenceCheckWorkflow


async def _require_entitlement(
    role: WorkspaceActorRouteRole, session: AsyncDBSession
) -> None:
    await check_entitlement(session, role, Entitlement.EXTERNAL_SECRET_STORES)


router = APIRouter(
    prefix="/secrets",
    tags=["secrets"],
    dependencies=[Depends(_require_entitlement)],
)
stores_router = APIRouter(
    prefix="/secret-stores",
    tags=["secrets"],
    dependencies=[Depends(_require_entitlement)],
)


@stores_router.get("", response_model=Page[WorkspaceSecretStoreRead])
@require_scope("secret:read")
async def list_authorized_secret_stores(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    limit: int = Query(
        config.TRACECAT__LIMIT_DEFAULT,
        ge=config.TRACECAT__LIMIT_MIN,
        le=config.TRACECAT__LIMIT_CURSOR_MAX,
    ),
    cursor: str | None = Query(None, max_length=8192),
) -> Page[WorkspaceSecretStoreRead]:
    """List external secret stores this workspace is authorized to reference."""
    service = SecretReferencesService(session, role=role)
    try:
        stores = await service.list_authorized_stores(
            PageParams(limit=limit, cursor=cursor)
        )
    except PaginationError as exc:
        raise HTTPException(status_code=400, detail=exc.detail) from exc
    return Page(
        items=[WorkspaceSecretStoreRead.from_database(store) for store in stores.items],
        next_cursor=stores.next_cursor,
        prev_cursor=stores.prev_cursor,
    )


_CHECK_TIMEOUT = timedelta(seconds=60)


@router.post("/references", status_code=status.HTTP_201_CREATED)
@require_scope("secret:create")
async def create_secret_reference(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    params: SecretReferenceCreate,
) -> None:
    """Create a custom secret whose values live in an external secret store."""
    service = SecretReferencesService(session, role=role)
    await _create(service.create_secret_reference(params))


@router.post("/references/{secret_id}", status_code=status.HTTP_204_NO_CONTENT)
@require_scope("secret:update")
async def update_secret_reference(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    secret_id: AnySecretIDPath,
    params: SecretReferenceUpdate,
) -> None:
    """Update the reference or key mapping of an externally backed secret."""
    service = SecretReferencesService(session, role=role)
    await _update(service, secret_id, params, provider=None)


@router.post("/references/{secret_id}/check", response_model=SecretReferenceCheckResult)
@require_scope("secret:read")
async def check_secret_reference(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    secret_id: AnySecretIDPath,
) -> SecretReferenceCheckResult:
    """Verify a saved external reference resolves. Values are never returned."""
    return await _check(role, session, secret_id, not_external=NOT_EXTERNAL_MESSAGE)


@router.post("/aws", status_code=status.HTTP_201_CREATED, deprecated=True)
@require_scope("secret:create")
async def create_aws_secret_reference(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    params: AwsSecretReferenceCreate,
) -> None:
    """Create a custom secret whose values live in AWS Secrets Manager.

    Deprecated: use ``POST /secrets/references``.
    """
    service = SecretReferencesService(session, role=role)
    await _create(service.create_aws_secret_reference(params))


@router.post(
    "/aws/{secret_id}", status_code=status.HTTP_204_NO_CONTENT, deprecated=True
)
@require_scope("secret:update")
async def update_aws_secret_reference(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    secret_id: AnySecretIDPath,
    params: AwsSecretReferenceUpdate,
) -> None:
    """Update the reference or key mapping of an AWS-backed secret.

    Deprecated: use ``POST /secrets/references/{secret_id}``.
    """
    service = SecretReferencesService(session, role=role)
    await _update(
        service, secret_id, params, provider=SecretStoreProvider.AWS_SECRETS_MANAGER
    )


@router.post(
    "/aws/{secret_id}/check",
    response_model=SecretReferenceCheckResult,
    deprecated=True,
)
@require_scope("secret:read")
async def check_aws_secret_reference(
    *,
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    secret_id: AnySecretIDPath,
) -> SecretReferenceCheckResult:
    """Verify a saved AWS-backed reference resolves. Values are never returned.

    Deprecated: use ``POST /secrets/references/{secret_id}/check``.
    """
    return await _check(
        role,
        session,
        secret_id,
        not_external=NOT_AWS_MESSAGE,
        provider=SecretStoreProvider.AWS_SECRETS_MANAGER,
    )


async def _create(operation: Awaitable[object]) -> None:
    try:
        await operation
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    except TracecatAuthorizationError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e)) from e
    except IntegrityError as e:
        if not is_unique_violation(e, "uq_secret_name_environment_workspace_id"):
            raise
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Secret already exists"
        ) from e


async def _update(
    service: SecretReferencesService,
    secret_id: SecretID,
    params: SecretReferenceUpdate,
    *,
    provider: SecretStoreProvider | None,
) -> None:
    try:
        secret = await service.get_secret(secret_id)
        await service.update_secret_reference(secret, params, provider=provider)
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
        if not is_unique_violation(e, "uq_secret_name_environment_workspace_id"):
            raise
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Secret already exists"
        ) from e


async def _check(
    role: WorkspaceActorRouteRole,
    session: AsyncDBSession,
    secret_id: SecretID,
    *,
    not_external: str,
    provider: SecretStoreProvider | None = None,
) -> SecretReferenceCheckResult:
    service = SecretReferencesService(session, role=role)
    try:
        secret = await service.get_secret(secret_id)
        # A provider-specific alias refuses other providers' references as it
        # refuses local secrets.
        if not is_external_reference(secret) or (
            provider is not None and secret.source != provider
        ):
            raise ValueError(not_external)
        # Only identifiers and actor context cross Temporal; values stay on the executor.
        await session.commit()
        client = await get_temporal_client()
        return await client.execute_workflow(
            SecretReferenceCheckWorkflow.run,
            SecretReferenceCheckRequest(role=role, secret_id=secret_id),
            id=f"secret-reference-check-{uuid4()}",
            task_queue=config.TRACECAT__EXECUTOR_QUEUE,
            priority=org_priority(role.organization_id),
            execution_timeout=_CHECK_TIMEOUT,
        )
    except WorkflowFailureError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Secret reference check could not complete. Try again.",
        ) from exc
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e)
        ) from e
    except TracecatNotFoundError as e:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Secret does not exist"
        ) from e
