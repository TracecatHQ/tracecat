"""Workspace sync round-trips Bitwarden and AWS references through Git."""

from __future__ import annotations

import uuid
from unittest.mock import AsyncMock

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from tracecat.auth.types import Role
from tracecat.db.models import (
    OrganizationSecretStore,
    Secret,
    Workspace,
    WorkspaceSecretStoreAuthorization,
)
from tracecat.secrets.enums import SecretSource
from tracecat.secrets.router import _serialize_secret_read_minimal
from tracecat.secrets.service import SecretsService
from tracecat.workspace_sync.adapters import SECRET_METADATA_RESOURCE_ADAPTER
from tracecat.workspace_sync.importer import WorkspaceResourceImportService
from tracecat.workspace_sync.schemas import (
    MANIFEST_FILENAME,
    SECRET_METADATA_ROOT,
    SecretMetadataResourceSpec,
    WorkspaceManifest,
)
from tracecat.workspace_sync.serialization import (
    canonical_json_text,
    serialize_yaml_model,
)
from tracecat.workspace_sync.service import WorkspaceSyncService

pytestmark = [pytest.mark.usefixtures("db"), pytest.mark.anyio]

AWS_ARN = "arn:aws:secretsmanager:us-east-1:123456789012:secret:app-AbCdEf"
BW_SECRET_ID = str(uuid.uuid4())
AWS_MAPPING = {"mode": "whole_string", "keys": ["TOKEN"], "fields": []}
BW_MAPPING = {
    "mode": "json",
    "keys": [],
    "fields": [
        {"key": "USER", "field": "username"},
        {"key": "PASS", "field": "password"},
    ],
}

# A reference as Tracecat 1.2.2 exported it (workspace_sync/schemas.py at 1.2.2).
YAML_1_2_2 = f"""\
version: 1
type: secret_metadata
id: default/legacy_api
name: legacy_api
environment: default
secret_type: custom
keys:
- TOKEN
tags: []
source: aws_secrets_manager
store: prod
remote_reference: {AWS_ARN}
key_mapping:
  mode: whole_string
  keys:
  - TOKEN
  fields: []
"""


@pytest.fixture(autouse=True)
def entitled(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "tracecat.workspace_sync.adapters.secret_metadata.check_entitlement",
        AsyncMock(return_value=None),
    )


async def _stores(session: AsyncSession, role: Role) -> tuple[uuid.UUID, uuid.UUID]:
    assert role.organization_id is not None
    aws = OrganizationSecretStore(
        organization_id=role.organization_id,
        name="prod",
        provider="aws_secrets_manager",
        config={
            "provider": "aws_secrets_manager",
            "role_arn": "arn:aws:iam::123456789012:role/reader",
            "region": "us-east-1",
            "external_id": "external-id",
        },
        all_workspaces=True,
    )
    bitwarden = OrganizationSecretStore(
        organization_id=role.organization_id,
        name="bitwarden",
        provider="bitwarden_secrets_manager",
        config={
            "provider": "bitwarden_secrets_manager",
            "server": "eu",
            "organization_id": str(uuid.uuid4()),
        },
        all_workspaces=True,
    )
    session.add_all([aws, bitwarden])
    await session.flush()
    session.add_all(
        [
            WorkspaceSecretStoreAuthorization(
                organization_id=role.organization_id,
                workspace_id=role.workspace_id,
                store_id=store.id,
            )
            for store in (aws, bitwarden)
        ]
    )
    await session.flush()
    return aws.id, bitwarden.id


async def _import(session: AsyncSession, role: Role, files: dict[str, str]) -> None:
    service = WorkspaceSyncService(session=session, role=role)
    snapshot, diagnostics = await service.parse_files(
        {MANIFEST_FILENAME: canonical_json_text(WorkspaceManifest()), **files},
        commit_sha="s" * 40,
    )
    assert diagnostics == []
    await WorkspaceResourceImportService(
        session=session, role=role
    ).import_non_workflow_resources(snapshot.spec)
    await session.flush()


async def _secrets(session: AsyncSession, workspace_id: uuid.UUID) -> dict[str, Secret]:
    rows = await session.scalars(
        select(Secret)
        .where(Secret.workspace_id == workspace_id)
        .options(selectinload(Secret.store))
    )
    return {secret.name: secret for secret in rows}


async def test_references_round_trip_and_name_their_provider(
    session: AsyncSession, svc_role: Role, svc_workspace: Workspace
) -> None:
    # Given a workspace with a Bitwarden reference and an AWS reference
    aws_store, bw_store = await _stores(session, svc_role)
    service = SecretsService(session=session, role=svc_role)
    session.add_all(
        [
            Secret(
                workspace_id=svc_role.workspace_id,
                name="vendor_api",
                environment="default",
                source=SecretSource.AWS_SECRETS_MANAGER,
                store_id=aws_store,
                encrypted_keys=service.encrypt_keys([]),
                remote_reference=AWS_ARN,
                remote_key_mapping=AWS_MAPPING,
            ),
            Secret(
                workspace_id=svc_role.workspace_id,
                name="bw_api",
                environment="default",
                source=SecretSource.BITWARDEN_SECRETS_MANAGER,
                store_id=bw_store,
                encrypted_keys=service.encrypt_keys([]),
                remote_reference=BW_SECRET_ID,
                remote_key_mapping=BW_MAPPING,
            ),
        ]
    )
    await session.flush()

    # When workspace sync exports to Git
    projection = await SECRET_METADATA_RESOURCE_ADAPTER.project(
        WorkspaceSyncService(session=session, role=svc_role)
    )
    specs = [
        spec
        for spec in projection.specs.values()
        if isinstance(spec, SecretMetadataResourceSpec)
    ]
    files = {
        f"{SECRET_METADATA_ROOT}/{spec.environment}/{spec.name}.yml": (
            serialize_yaml_model(spec)
        )
        for spec in specs
    }
    bw_yaml = files[f"{SECRET_METADATA_ROOT}/default/bw_api.yml"]
    # Then the exported YAML names the provider
    assert "source: bitwarden_secrets_manager" in bw_yaml
    assert f"remote_reference: {BW_SECRET_ID}" in bw_yaml
    assert "store: bitwarden" in bw_yaml

    # When it imports into a fresh workspace
    fresh = Workspace(
        name=f"fresh-{uuid.uuid4().hex[:8]}",
        organization_id=svc_workspace.organization_id,
    )
    session.add(fresh)
    await session.flush()
    fresh_role = svc_role.model_copy(update={"workspace_id": fresh.id})
    await _import(session, fresh_role, files)

    # Then both references come back with the same store, reference and mapping
    imported = await _secrets(session, fresh.id)
    assert (
        imported["vendor_api"].source,
        imported["vendor_api"].store_id,
        imported["vendor_api"].remote_reference,
        imported["vendor_api"].remote_key_mapping,
    ) == (SecretSource.AWS_SECRETS_MANAGER, aws_store, AWS_ARN, AWS_MAPPING)
    assert (
        imported["bw_api"].source,
        imported["bw_api"].store_id,
        imported["bw_api"].remote_reference,
        imported["bw_api"].remote_key_mapping,
    ) == (SecretSource.BITWARDEN_SECRETS_MANAGER, bw_store, BW_SECRET_ID, BW_MAPPING)
    # and the secrets API shows the provider as the source
    read = _serialize_secret_read_minimal(
        service=SecretsService(session=session, role=fresh_role),
        secret=imported["bw_api"],
    )
    assert read.source == SecretSource.BITWARDEN_SECRETS_MANAGER
    assert read.store_name == "bitwarden"


async def test_yaml_exported_by_1_2_2_still_imports(
    session: AsyncSession, svc_role: Role
) -> None:
    aws_store, _ = await _stores(session, svc_role)

    await _import(
        session,
        svc_role,
        {f"{SECRET_METADATA_ROOT}/default/legacy_api.yml": YAML_1_2_2},
    )

    assert svc_role.workspace_id is not None
    secret = (await _secrets(session, svc_role.workspace_id))["legacy_api"]
    assert secret.source == SecretSource.AWS_SECRETS_MANAGER
    assert secret.store_id == aws_store
    assert secret.remote_reference == AWS_ARN
