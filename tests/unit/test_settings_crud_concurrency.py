"""PostgreSQL regressions for first-write settings races."""

import asyncio
import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest
from asyncpg import ForeignKeyViolationError
from cryptography.fernet import Fernet
from sqlalchemy import delete, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG
from tracecat.auth.types import Role
from tracecat.contexts import ctx_role
from tracecat.db.models import Organization, OrganizationSetting
from tracecat.exceptions import TracecatConflictError
from tracecat.settings.constants import SENSITIVE_SETTINGS_KEYS
from tracecat.settings.schemas import (
    AgentOtelSettingsUpdate,
    AgentSettingsUpdate,
    AppSettingsUpdate,
    AuditSettingsUpdate,
    BaseSettingsGroup,
    GitSettingsUpdate,
    SAMLSettingsUpdate,
    SecuritySettingsUpdate,
    SettingCreate,
    ValueType,
)
from tracecat.settings.service import SettingsService

pytestmark = [pytest.mark.anyio, pytest.mark.usefixtures("db")]


@pytest.fixture(autouse=True)
def encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("TRACECAT__DB_ENCRYPTION_KEY", Fernet.generate_key().decode())


@pytest.fixture
async def settings_database() -> AsyncIterator[
    tuple[async_sessionmaker[AsyncSession], Role]
]:
    engine = create_async_engine(TEST_DB_CONFIG.test_url, poolclass=NullPool)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    org_id = uuid.uuid4()
    role = Role(
        type="user",
        service_id="tracecat-api",
        organization_id=org_id,
        scopes=frozenset({"*"}),
    )
    async with factory() as session:
        session.add(
            Organization(
                id=org_id, name="Synthetic organization", slug=uuid.uuid4().hex
            )
        )
        await session.commit()
    token = ctx_role.set(role)
    try:
        yield factory, role
    finally:
        ctx_role.reset(token)
        async with factory() as session:
            await session.execute(
                delete(OrganizationSetting).where(
                    OrganizationSetting.organization_id == org_id
                )
            )
            await session.execute(delete(Organization).where(Organization.id == org_id))
            await session.commit()
        await engine.dispose()


@pytest.mark.parametrize(
    ("first", "second"),
    [
        (
            GitSettingsUpdate(git_repo_package_name="first"),
            GitSettingsUpdate(git_repo_package_name="second"),
        ),
        (
            SAMLSettingsUpdate(saml_idp_metadata_url="https://first.example.com"),
            SAMLSettingsUpdate(saml_idp_metadata_url="https://second.example.com"),
        ),
        (
            AppSettingsUpdate(app_executions_query_limit=100),
            AppSettingsUpdate(app_executions_query_limit=200),
        ),
        (
            AuditSettingsUpdate(audit_webhook_custom_headers={"x-synthetic": "first"}),
            AuditSettingsUpdate(audit_webhook_custom_headers={"x-synthetic": "second"}),
        ),
        (
            SecuritySettingsUpdate(ip_allowlist_enabled=True),
            SecuritySettingsUpdate(ip_allowlist_enabled=False),
        ),
        (
            AgentSettingsUpdate(agent_case_chat_prompt="first"),
            AgentSettingsUpdate(agent_case_chat_prompt="second"),
        ),
        (
            AgentOtelSettingsUpdate(agent_otel_headers={"x-synthetic": "first"}),
            AgentOtelSettingsUpdate(agent_otel_headers={"x-synthetic": "second"}),
        ),
    ],
)
async def test_concurrent_absent_grouped_setting_is_upserted(
    settings_database: tuple[async_sessionmaker[AsyncSession], Role],
    first: BaseSettingsGroup,
    second: BaseSettingsGroup,
) -> None:
    factory, role = settings_database
    barrier = asyncio.Barrier(2)
    key = next(iter(first.model_fields_set))

    async def update(params: BaseSettingsGroup) -> None:
        async with factory() as session:
            await session.execute(text("SET LOCAL lock_timeout = '10s'"))
            service = SettingsService(session, role=role)
            settings = await service.list_org_settings(keys=[key])
            assert not settings
            await asyncio.wait_for(barrier.wait(), timeout=10)
            await service._update_grouped_settings(settings, params)

    await asyncio.wait_for(asyncio.gather(update(first), update(second)), timeout=30)
    async with factory() as session:
        service = SettingsService(session, role=role)
        settings = await service.list_org_settings()
        assert len(settings) == 1
        setting = settings[0]
        assert setting.key == key
        assert setting.is_encrypted == (key in SENSITIVE_SETTINGS_KEYS)
        assert service.get_value(setting) in (
            first.model_dump()[key],
            second.model_dump()[key],
        )


@pytest.mark.parametrize("encrypted", [True, False])
async def test_stale_absent_read_preserves_winner_metadata_and_omissions(
    settings_database: tuple[async_sessionmaker[AsyncSession], Role],
    encrypted: bool,
) -> None:
    factory, role = settings_database
    async with factory() as session:
        service = SettingsService(session, role=role)
        winner = await service.create_org_setting(
            SettingCreate(
                key="audit_webhook_url",
                value="https://old.example.com",
                is_sensitive=encrypted,
                value_type=ValueType.JSON,
            )
        )
        untouched = await service.create_org_setting(
            SettingCreate(
                key="audit_webhook_custom_headers",
                value={"x-synthetic": "keep"},
                is_sensitive=True,
            )
        )
        untouched_bytes = untouched.value
        old_timestamp = datetime.now(UTC) - timedelta(hours=1)
        winner.updated_at = old_timestamp
        await session.commit()

        # A second request already read an empty list before the winning insert.
        await service._update_grouped_settings(
            [], AuditSettingsUpdate(audit_webhook_url="https://new.example.com")
        )
        await session.refresh(winner)
        await session.refresh(untouched)
        assert winner.is_encrypted is encrypted
        assert winner.value_type == ValueType.JSON
        assert service.get_value(winner) == "https://new.example.com"
        assert winner.updated_at > old_timestamp
        assert untouched.value == untouched_bytes
        assert service.get_value(untouched) == {"x-synthetic": "keep"}


async def test_direct_setting_duplicate_is_typed_conflict(
    session: AsyncSession,
    svc_admin_role: Role,
) -> None:
    service = SettingsService(session, role=svc_admin_role)
    params = SettingCreate(key="synthetic-setting", value="first", is_sensitive=False)
    await service.create_org_setting(params)
    with pytest.raises(TracecatConflictError, match="Setting already exists"):
        await service.create_org_setting(params.model_copy(update={"value": "second"}))
    setting = await service.get_org_setting(params.key)
    assert setting is not None
    assert service.get_value(setting) == "first"


@pytest.mark.parametrize("grouped", [True, False])
async def test_unrelated_setting_integrity_rolls_back_and_propagates(
    session: AsyncSession,
    svc_admin_role: Role,
    monkeypatch: pytest.MonkeyPatch,
    grouped: bool,
) -> None:
    service = SettingsService(session, role=svc_admin_role)
    error = IntegrityError("synthetic", {}, ForeignKeyViolationError("synthetic"))
    rollback = AsyncMock(wraps=session.rollback)
    monkeypatch.setattr(session, "rollback", rollback)
    monkeypatch.setattr(session, "commit", AsyncMock(side_effect=error))
    with pytest.raises(IntegrityError) as raised:
        if grouped:
            await service._update_grouped_settings(
                [], GitSettingsUpdate(git_repo_package_name="synthetic")
            )
        else:
            await service.create_org_setting(
                SettingCreate(
                    key="synthetic-setting", value="first", is_sensitive=False
                )
            )
    assert raised.value is error
    rollback.assert_awaited_once()
    assert (
        await session.scalar(
            select(OrganizationSetting).where(
                OrganizationSetting.key == "synthetic-setting"
            )
        )
        is None
    )
