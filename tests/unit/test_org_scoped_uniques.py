"""Tests for organization-scoped uniqueness constraints."""

import uuid

import pytest
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.models import (
    Organization,
    OrganizationSecret,
    OrganizationSetting,
    RegistryAction,
    RegistryRepository,
)

pytestmark = pytest.mark.usefixtures("db")


@pytest.mark.anyio
async def test_org_scoped_uniques_for_settings_and_secrets(
    session: AsyncSession,
) -> None:
    org_a_id = uuid.uuid4()
    org_b_id = uuid.uuid4()
    org_a = Organization(
        id=org_a_id,
        name="Org A",
        slug=f"org-a-{org_a_id.hex[:8]}",
        is_active=True,
    )
    org_b = Organization(
        id=org_b_id,
        name="Org B",
        slug=f"org-b-{org_b_id.hex[:8]}",
        is_active=True,
    )
    session.add_all([org_a, org_b])
    await session.commit()

    secret_name = "shared-secret"
    secret_env = "prod"
    session.add_all(
        [
            OrganizationSecret(
                organization_id=org_a_id,
                name=secret_name,
                environment=secret_env,
                type="custom",
                encrypted_keys=b"secret-a",
            ),
            OrganizationSecret(
                organization_id=org_b_id,
                name=secret_name,
                environment=secret_env,
                type="custom",
                encrypted_keys=b"secret-b",
            ),
        ]
    )
    await session.commit()

    session.add(
        OrganizationSecret(
            organization_id=org_a_id,
            name=secret_name,
            environment=secret_env,
            type="custom",
            encrypted_keys=b"secret-dup",
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    setting_key = "sync.enabled"
    session.add_all(
        [
            OrganizationSetting(
                organization_id=org_a_id,
                key=setting_key,
                value=b"true",
                value_type="bool",
                is_encrypted=False,
            ),
            OrganizationSetting(
                organization_id=org_b_id,
                key=setting_key,
                value=b"false",
                value_type="bool",
                is_encrypted=False,
            ),
        ]
    )
    await session.commit()

    session.add(
        OrganizationSetting(
            organization_id=org_a_id,
            key=setting_key,
            value=b"false",
            value_type="bool",
            is_encrypted=False,
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()


@pytest.mark.anyio
async def test_org_scoped_uniques_for_registry(session: AsyncSession) -> None:
    org_a_id = uuid.uuid4()
    org_b_id = uuid.uuid4()
    org_a = Organization(
        id=org_a_id,
        name="Org A",
        slug=f"org-a-{org_a_id.hex[:8]}",
        is_active=True,
    )
    org_b = Organization(
        id=org_b_id,
        name="Org B",
        slug=f"org-b-{org_b_id.hex[:8]}",
        is_active=True,
    )
    session.add_all([org_a, org_b])
    await session.commit()

    origin = "git://example.com/registry.git"
    session.add_all(
        [
            RegistryRepository(organization_id=org_a_id, origin=origin),
            RegistryRepository(organization_id=org_b_id, origin=origin),
        ]
    )
    await session.commit()

    session.add(RegistryRepository(organization_id=org_a_id, origin=origin))
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()

    action_namespace = "tools.core"
    action_name = "do_thing"
    session.add_all(
        [
            RegistryAction(
                organization_id=org_a_id,
                name=action_name,
                description="Does a thing",
                namespace=action_namespace,
                origin=origin,
                type="action",
                interface={},
            ),
            RegistryAction(
                organization_id=org_b_id,
                name=action_name,
                description="Does a thing",
                namespace=action_namespace,
                origin=origin,
                type="action",
                interface={},
            ),
        ]
    )
    await session.commit()

    session.add(
        RegistryAction(
            organization_id=org_a_id,
            name=action_name,
            description="Does a thing",
            namespace=action_namespace,
            origin=origin,
            type="action",
            interface={},
        )
    )
    with pytest.raises(IntegrityError):
        await session.commit()
    await session.rollback()
