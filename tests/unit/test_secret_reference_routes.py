"""The deprecated ``/secrets/aws`` routes and the generic ``/secrets/references``
routes behave the same for an AWS store.

Route handlers run against the test database. The reference check runs the
executor activity in-process instead of on Temporal, with AWS stubbed.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any, Literal
from unittest.mock import AsyncMock, MagicMock

import aioboto3
import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from tracecat_ee.secrets.references import router, workflows
from tracecat_ee.secrets.references.service import SecretReferencesService
from tracecat_ee.secrets.stores.service import SecretStoresService

from tests.support.managed_resources import mark_managed
from tests.unit.test_aws_secrets_manager import _FakeSession
from tracecat.api.app import create_app
from tracecat.auth.types import Role
from tracecat.db.models import OrganizationSecretStore, Secret, Workspace
from tracecat.identifiers import SecretUUID
from tracecat.managed_resources.guard import ManagedResourceLockedError
from tracecat.secrets.enums import SecretResolutionErrorCode, SecretSource
from tracecat.secrets.schemas import (
    AwsSecretReferenceCreate,
    AwsSecretReferenceUpdate,
    AwsSecretsManagerStoreCreate,
    SecretKeyMapping,
    SecretReferenceCheckResult,
    SecretReferenceCreate,
    SecretReferenceUpdate,
    SecretStoreCreate,
)
from tracecat.workspace_sync.enums import SyncResourceType

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]

ROLE_ARN = "arn:aws:iam::123456789012:role/tracecat-secrets-reader"
REGION = "us-east-1"
SECRET_ARN = f"arn:aws:secretsmanager:{REGION}:123456789012:secret:app/api-AbCdEf"
OTHER_REGION_ARN = "arn:aws:secretsmanager:eu-west-1:123456789012:secret:app/api-AbCdEf"
MAPPING = {"mode": "whole_string", "keys": ["API_TOKEN"]}

# What the routes returned before this change, for the same requests (main at
# 2b0818025; unchanged since 1.2.2).
REGION_ERROR_BEFORE = "Secret ARN region must match the store region 'us-east-1'."
REGION_MISMATCH_CHECK_BEFORE = SecretReferenceCheckResult(
    ok=False,
    error_code=SecretResolutionErrorCode.REGION_MISMATCH,
    message="Reference check failed: region_mismatch",
    resolved_keys=[],
)
OK_CHECK_BEFORE = SecretReferenceCheckResult(ok=True, resolved_keys=["API_TOKEN"])


@pytest.fixture
async def store(
    session: AsyncSession, svc_admin_role: Role, svc_workspace: Workspace
) -> OrganizationSecretStore:
    stores = SecretStoresService(session=session, role=svc_admin_role)
    created = await stores.create_store(
        SecretStoreCreate(
            name="prod",
            config=AwsSecretsManagerStoreCreate(role_arn=ROLE_ARN, region=REGION),
        )
    )
    await stores.authorize_workspace(created, svc_workspace.id)
    return created


@pytest.fixture
def executor_check(
    monkeypatch: pytest.MonkeyPatch, session: AsyncSession
) -> type[_FakeSession]:
    """Run the check workflow's activity in-process, with AWS stubbed."""

    async def execute_workflow(_: Any, request: Any, **__: Any) -> Any:
        return await workflows.check_secret_reference_activity(request)

    @asynccontextmanager
    async def with_session(*, role: Role) -> AsyncIterator[SecretReferencesService]:
        # The test session's rows are invisible to a second session.
        yield SecretReferencesService(session=session, role=role)

    monkeypatch.setattr(SecretReferencesService, "with_session", with_session)

    client = MagicMock(execute_workflow=AsyncMock(side_effect=execute_workflow))
    monkeypatch.setattr(router, "get_temporal_client", AsyncMock(return_value=client))
    monkeypatch.setattr(workflows, "check_entitlement", AsyncMock())
    _FakeSession.reset()
    monkeypatch.setattr(aioboto3, "Session", _FakeSession)
    return _FakeSession


async def _create(
    route: Literal["old", "new"],
    role: Role,
    session: AsyncSession,
    *,
    name: str,
    store_id: uuid.UUID,
    reference: str,
) -> None:
    """Create a reference through the deprecated or the generic route."""
    mapping = SecretKeyMapping.model_validate(MAPPING)
    if route == "old":
        await router.create_aws_secret_reference(
            role=role,
            session=session,
            params=AwsSecretReferenceCreate(
                name=name,
                store_id=store_id,
                remote_reference=reference,
                key_mapping=mapping,
            ),
        )
    else:
        await router.create_secret_reference(
            role=role,
            session=session,
            params=SecretReferenceCreate(
                name=name,
                store_id=store_id,
                remote_reference=reference,
                key_mapping=mapping,
            ),
        )


async def _secret(session: AsyncSession, name: str) -> Secret:
    secret = await session.scalar(select(Secret).where(Secret.name == name))
    assert secret is not None
    await session.refresh(secret)
    return secret


async def test_old_and_generic_routes_match_for_an_aws_store(
    session: AsyncSession,
    svc_admin_role: Role,
    store: OrganizationSecretStore,
    executor_check: type[_FakeSession],
) -> None:
    role = svc_admin_role
    # Given an existing client: create, update and check through /secrets/aws
    assert (
        await router.create_aws_secret_reference(
            role=role,
            session=session,
            params=AwsSecretReferenceCreate(
                name="old_route",
                store_id=store.id,
                remote_reference="app/api",
                key_mapping=SecretKeyMapping.model_validate(MAPPING),
            ),
        )
        is None
    )
    # and the same operations through /secrets/references
    assert (
        await router.create_secret_reference(
            role=role,
            session=session,
            params=SecretReferenceCreate(
                name="new_route",
                store_id=store.id,
                remote_reference="app/api",
                key_mapping=SecretKeyMapping.model_validate(MAPPING),
            ),
        )
        is None
    )
    old = await _secret(session, "old_route")
    new = await _secret(session, "new_route")
    for secret in (old, new):
        assert secret.source == SecretSource.AWS_SECRETS_MANAGER
        assert secret.store_id == store.id
    old_id, new_id = SecretUUID.new(old.id), SecretUUID.new(new.id)

    # Then an ARN in another region is refused with the previous message on both
    for route in ("old", "new"):
        with pytest.raises(HTTPException) as exc_info:
            await _create(
                route,
                role,
                session,
                name=f"bad_{uuid.uuid4().hex[:8]}",
                store_id=store.id,
                reference=OTHER_REGION_ARN,
            )
        assert (exc_info.value.status_code, exc_info.value.detail) == (
            422,
            REGION_ERROR_BEFORE,
        )

    # and updates return nothing (204) on both
    await router.update_aws_secret_reference(
        role=role,
        session=session,
        secret_id=old_id,
        params=AwsSecretReferenceUpdate(remote_reference=SECRET_ARN),
    )
    await router.update_secret_reference(
        role=role,
        session=session,
        secret_id=new_id,
        params=SecretReferenceUpdate(remote_reference=SECRET_ARN),
    )
    assert (await _secret(session, "old_route")).remote_reference == SECRET_ARN
    assert (await _secret(session, "new_route")).remote_reference == SECRET_ARN

    # and the check returns the previous result shape on both
    executor_check.sm_response = {"SecretString": "synthetic-token"}
    assert (
        await router.check_aws_secret_reference(
            role=role, session=session, secret_id=old_id
        )
        == OK_CHECK_BEFORE
    )
    assert (
        await router.check_secret_reference(
            role=role, session=session, secret_id=new_id
        )
        == OK_CHECK_BEFORE
    )

    # and a saved ARN that disagrees with the store reads region_mismatch
    await session.execute(
        update(Secret)
        .where(Secret.id.in_([old_id, new_id]))
        .values(remote_reference=OTHER_REGION_ARN)
        .execution_options(synchronize_session=False)
    )
    await session.commit()
    session.expire_all()
    for check, secret_id in (
        (router.check_aws_secret_reference, old_id),
        (router.check_secret_reference, new_id),
    ):
        result = await check(role=role, session=session, secret_id=secret_id)
        assert result == REGION_MISMATCH_CHECK_BEFORE


async def test_managed_resource_guards_hold_on_both_routes(
    session: AsyncSession,
    svc_admin_role: Role,
    store: OrganizationSecretStore,
) -> None:
    role = svc_admin_role
    routes: tuple[tuple[Literal["old", "new"], str], ...] = (
        ("old", "old_managed"),
        ("new", "new_managed"),
    )
    for route, name in routes:
        await _create(
            route, role, session, name=name, store_id=store.id, reference="app/api"
        )
        secret = await _secret(session, name)
        await mark_managed(session, role, SyncResourceType.SECRET_METADATA, secret.id)

    old_id = SecretUUID.new((await _secret(session, "old_managed")).id)
    new_id = SecretUUID.new((await _secret(session, "new_managed")).id)
    # A managed secret only takes a new remote reference...
    await router.update_aws_secret_reference(
        role=role,
        session=session,
        secret_id=old_id,
        params=AwsSecretReferenceUpdate(remote_reference="app/other"),
    )
    await router.update_secret_reference(
        role=role,
        session=session,
        secret_id=new_id,
        params=SecretReferenceUpdate(remote_reference="app/other"),
    )
    # ...and refuses any other change with the previous 403, on either route.
    with pytest.raises(HTTPException) as old_refusal:
        await router.update_aws_secret_reference(
            role=role,
            session=session,
            secret_id=old_id,
            params=AwsSecretReferenceUpdate(description="changed"),
        )
    await session.rollback()
    with pytest.raises(HTTPException) as new_refusal:
        await router.update_secret_reference(
            role=role,
            session=session,
            secret_id=new_id,
            params=SecretReferenceUpdate(description="changed"),
        )
    for refusal in (old_refusal, new_refusal):
        assert isinstance(refusal.value.__cause__, ManagedResourceLockedError)
        assert refusal.value.status_code == 403


def test_aws_routes_stay_published_as_deprecated_aliases() -> None:
    paths = create_app().openapi()["paths"]
    for path in (
        "/workspaces/{workspace_id}/secrets/aws",
        "/workspaces/{workspace_id}/secrets/aws/{secret_id}",
        "/workspaces/{workspace_id}/secrets/aws/{secret_id}/check",
    ):
        assert paths[path]["post"]["deprecated"] is True
    for path in (
        "/workspaces/{workspace_id}/secrets/references",
        "/workspaces/{workspace_id}/secrets/references/{secret_id}",
        "/workspaces/{workspace_id}/secrets/references/{secret_id}/check",
    ):
        assert paths[path]["post"].get("deprecated") is not True


@pytest.mark.parametrize("bad_name", ["bad name with spaces", "app/api;rm", "ärger"])
async def test_generic_route_keeps_the_aws_secret_name_format(
    session: AsyncSession,
    svc_admin_role: Role,
    store: OrganizationSecretStore,
    bad_name: str,
) -> None:
    # /secrets/aws refused these names before this change (schema pattern).
    with pytest.raises(ValidationError):
        AwsSecretReferenceCreate(
            name="bad_ref",
            store_id=store.id,
            remote_reference=bad_name,
            key_mapping=SecretKeyMapping.model_validate(MAPPING),
        )
    # The generic route refuses them for an AWS store too.
    with pytest.raises(HTTPException) as exc_info:
        await _create(
            "new",
            svc_admin_role,
            session,
            name="bad_ref",
            store_id=store.id,
            reference=bad_name,
        )
    assert exc_info.value.status_code == 422
