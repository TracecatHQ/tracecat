"""Checking a Bitwarden reference runs on the executor through Temporal.

A worker on the test Temporal server runs the check workflow and its activity;
only the Bitwarden SDK is replaced by a fake client.
"""

from __future__ import annotations

import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock

import orjson
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncSession
from temporalio.worker import Worker
from tracecat_ee.secrets.providers import bitwarden_secrets_manager as bw
from tracecat_ee.secrets.providers import bitwarden_sm_child as child
from tracecat_ee.secrets.references import router, workflows
from tracecat_ee.secrets.references.service import SecretReferencesService
from tracecat_ee.secrets.stores.service import SecretStoresService

from tests.unit.test_bitwarden_secrets_manager import RecordingClient, use_client
from tracecat import config
from tracecat.auth.types import Role
from tracecat.db.models import Workspace
from tracecat.dsl.client import get_temporal_client
from tracecat.dsl.worker import new_sandbox_runner
from tracecat.identifiers import SecretUUID
from tracecat.secrets.enums import SecretMappingMode, SecretStoreProvider
from tracecat.secrets.schemas import (
    BitwardenSecretsManagerStoreCreate,
    SecretKeyMapping,
    SecretReferenceCreate,
    SecretStoreCreate,
)
from tracecat.secrets.service import SecretsService

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio, pytest.mark.temporal]

BITWARDEN = SecretStoreProvider.BITWARDEN_SECRETS_MANAGER
SECRET_VALUE = "checked-but-never-returned"


@pytest.fixture
async def executor_queue(
    monkeypatch: pytest.MonkeyPatch,
    session: AsyncSession,
) -> AsyncIterator[str]:
    """A worker running the check workflow and activity on its own queue."""
    queue = f"secret-check-{uuid.uuid4().hex[:8]}"
    client = await get_temporal_client()
    monkeypatch.setattr(config, "TRACECAT__EXECUTOR_QUEUE", queue)
    monkeypatch.setattr(workflows, "check_entitlement", AsyncMock())

    # The test session's rows are invisible to a second session.
    @asynccontextmanager
    async def references_session(
        *, role: Role
    ) -> AsyncIterator[SecretReferencesService]:
        yield SecretReferencesService(session=session, role=role)

    @asynccontextmanager
    async def secrets_session(*, role: Role | None) -> AsyncIterator[SecretsService]:
        yield SecretsService(session=session, role=role)

    monkeypatch.setattr(SecretReferencesService, "with_session", references_session)
    monkeypatch.setattr(SecretsService, "with_session", secrets_session)
    async with Worker(
        client,
        task_queue=queue,
        workflows=[workflows.SecretReferenceCheckWorkflow],
        activities=[workflows.check_secret_reference_activity],
        workflow_runner=new_sandbox_runner(),
    ):
        yield queue


def fake_bitwarden(monkeypatch: pytest.MonkeyPatch, client: RecordingClient) -> None:
    use_client(monkeypatch, client)

    async def run_child(payload: bytes) -> tuple[bytes, int]:
        return orjson.dumps(child.run(orjson.loads(payload))), 0

    monkeypatch.setattr(bw, "_run_child", run_child)


async def _reference(
    session: AsyncSession,
    role: Role,
    workspace: Workspace,
    organization_id: uuid.UUID,
    secret_id: str,
) -> uuid.UUID:
    stores = SecretStoresService(session=session, role=role)
    store = await stores.create_store(
        SecretStoreCreate(
            name="bitwarden",
            provider=BITWARDEN,
            config=BitwardenSecretsManagerStoreCreate(
                provider=BITWARDEN, server="eu", organization_id=organization_id
            ),
            access_token=SecretStr("synthetic"),
        )
    )
    await stores.authorize_workspace(store, workspace.id)
    secret = await SecretReferencesService(
        session=session, role=role
    ).create_secret_reference(
        SecretReferenceCreate(
            name="bw_ref",
            store_id=store.id,
            remote_reference=secret_id,
            key_mapping=SecretKeyMapping(
                mode=SecretMappingMode.WHOLE_STRING, keys=["TOKEN"]
            ),
        )
    )
    return secret.id


async def test_check_runs_on_the_executor_and_returns_key_names_only(
    monkeypatch: pytest.MonkeyPatch,
    executor_queue: str,
    session: AsyncSession,
    svc_admin_role: Role,
    svc_workspace: Workspace,
) -> None:
    organization_id = uuid.uuid4()
    secret_id = str(uuid.uuid4())
    client = RecordingClient(
        values={secret_id: SECRET_VALUE}, organization_id=organization_id
    )
    fake_bitwarden(monkeypatch, client)
    reference_id = await _reference(
        session, svc_admin_role, svc_workspace, organization_id, secret_id
    )

    # When a workspace user presses Check
    result = await router.check_secret_reference(
        role=svc_admin_role, session=session, secret_id=SecretUUID.new(reference_id)
    )

    # Then the check ran (through the executor queue) and returns ok and key names only
    # with the token stored on the store
    assert client.logins == ["synthetic"]
    assert client.batches == [[secret_id]]
    assert result.ok is True
    assert result.resolved_keys == ["TOKEN"]
    assert SECRET_VALUE not in result.model_dump_json()


async def test_secret_from_another_organization_is_access_denied(
    monkeypatch: pytest.MonkeyPatch,
    executor_queue: str,
    session: AsyncSession,
    svc_admin_role: Role,
    svc_workspace: Workspace,
) -> None:
    secret_id = str(uuid.uuid4())
    # The SDK hands back a secret owned by an organization the store does not name.
    client = RecordingClient(
        values={secret_id: SECRET_VALUE}, organization_id=uuid.uuid4()
    )
    fake_bitwarden(monkeypatch, client)
    reference_id = await _reference(
        session, svc_admin_role, svc_workspace, uuid.uuid4(), secret_id
    )

    result: Any = await router.check_secret_reference(
        role=svc_admin_role, session=session, secret_id=SecretUUID.new(reference_id)
    )

    assert result.ok is False
    assert result.error_code == "access_denied"
    assert SECRET_VALUE not in result.model_dump_json()
