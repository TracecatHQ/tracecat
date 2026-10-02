"""Default settings initialization under concurrent PostgreSQL writes."""

import asyncio
import uuid

import pytest
from pydantic_core import to_jsonable_python
from sqlalchemy import delete, select, text
from sqlalchemy.ext.asyncio import AsyncSession, create_async_engine

from tests.database import TEST_DB_CONFIG
from tracecat.auth.types import Role
from tracecat.db.models import Organization, OrganizationSetting
from tracecat.settings.constants import SENSITIVE_SETTINGS_KEYS
from tracecat.settings.schemas import SettingCreate
from tracecat.settings.service import SettingsService

pytestmark = [pytest.mark.anyio, pytest.mark.integration, pytest.mark.usefixtures("db")]


async def test_default_settings_preserve_concurrent_insert() -> None:
    # The normal session fixture uses SERIALIZABLE and an outer transaction.
    # Independent READ COMMITTED transactions exercise production conflict handling.
    engine = create_async_engine(
        TEST_DB_CONFIG.test_url, isolation_level="READ COMMITTED"
    )
    organization_id = uuid.uuid4()
    role = Role(
        type="service",
        service_id="tracecat-api",
        organization_id=organization_id,
    )
    try:
        async with (
            AsyncSession(engine, expire_on_commit=False) as writer,
            AsyncSession(engine, expire_on_commit=False) as initializer,
        ):
            writer.add(
                Organization(
                    id=organization_id,
                    name="Concurrent settings test",
                    slug=f"concurrent-settings-{organization_id.hex}",
                )
            )
            await writer.commit()

            writer_service = SettingsService(writer, role)
            setting = await writer_service._create_org_setting(
                SettingCreate(
                    key="agent_default_model",
                    value="configured-model",
                    is_sensitive=False,
                )
            )
            # Hold the unique key uncommitted so initialization cannot see it.
            await writer.flush()
            initializer_pid = await initializer.scalar(text("SELECT pg_backend_pid()"))
            service = SettingsService(initializer, role)
            async with asyncio.TaskGroup() as tasks:
                tasks.create_task(service.init_default_settings())
                try:
                    # Wait for the actual insert conflict, not a timing assumption.
                    async with asyncio.timeout(10):
                        while not await writer.scalar(
                            text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"),
                            {"pid": initializer_pid},
                        ):
                            await asyncio.sleep(0.01)
                finally:
                    await writer.commit()

            settings = list(await service.list_org_settings())
            expected = {
                key: to_jsonable_python(value)
                for group in service.groups
                for key, value in group()
            }
            expected["agent_default_model"] = "configured-model"
            assert {row.key: service.get_value(row) for row in settings} == expected
            assert {row.key for row in settings if row.is_encrypted} == (
                expected.keys() & SENSITIVE_SETTINGS_KEYS
            )
            persisted = {row.key: (row.id, row.value) for row in settings}
            assert persisted[setting.key] == (setting.id, setting.value)

            # Reinitializing must leave configured values and ciphertext untouched.
            await service.init_default_settings()
            rows = (
                await initializer.scalars(
                    select(OrganizationSetting)
                    .where(OrganizationSetting.organization_id == organization_id)
                    .execution_options(populate_existing=True)
                )
            ).all()
            assert {row.key: (row.id, row.value) for row in rows} == persisted
    finally:
        async with AsyncSession(engine) as cleanup:
            await cleanup.execute(
                delete(Organization).where(Organization.id == organization_id)
            )
            await cleanup.commit()
        await engine.dispose()
