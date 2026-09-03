"""Tests for AgentCustomProviderService."""

from __future__ import annotations

import uuid
from unittest.mock import patch

import orjson
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config as tracecat_config
from tracecat.agent.provider import service as provider_service_module
from tracecat.agent.provider.schemas import (
    AgentCustomProviderCreate,
    AgentCustomProviderUpdate,
)
from tracecat.agent.provider.service import AgentCustomProviderService
from tracecat.auth.types import Role
from tracecat.db.models import (
    AgentCatalog,
    AgentCustomProvider,
    Organization,
)
from tracecat.exceptions import TracecatNotFoundError
from tracecat.pagination import CursorPaginationParams
from tracecat.secrets.encryption import decrypt_value, encrypt_keyvalues
from tracecat.secrets.schemas import SecretKeyValue

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def set_db_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        tracecat_config,
        "TRACECAT__DB_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )


def _role(org: Organization) -> Role:
    return Role(
        type="user",
        user_id=uuid.uuid4(),
        organization_id=org.id,
        service_id="tracecat-api",
        scopes=frozenset({"*"}),
    )


@pytest.mark.anyio
async def test_create_provider_minimal(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    result = await service.create_provider(
        AgentCustomProviderCreate(display_name="Test Provider")
    )

    assert result.id is not None
    assert result.display_name == "Test Provider"
    assert result.passthrough is False


@pytest.mark.anyio
async def test_get_provider_not_found(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    with pytest.raises(TracecatNotFoundError):
        await service.get_provider(uuid.uuid4())


@pytest.mark.anyio
async def test_list_providers_paginated(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    for i in range(3):
        await service.create_provider(
            AgentCustomProviderCreate(display_name=f"Provider {i}")
        )

    first_page = await service.list_providers(CursorPaginationParams(limit=2))
    second_page = await service.list_providers(
        CursorPaginationParams(limit=2, cursor=first_page.next_cursor)
    )

    assert len(first_page.items) == 2
    assert first_page.has_more is True
    assert len(second_page.items) == 1


@pytest.mark.anyio
async def test_update_provider(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    created = await service.create_provider(
        AgentCustomProviderCreate(display_name="Original", passthrough=False)
    )

    updated = await service.update_provider(
        created.id,
        AgentCustomProviderUpdate(
            display_name="Updated",
            base_url="https://api.example.com",
            passthrough=True,
        ),
    )

    assert updated.display_name == "Updated"
    assert updated.base_url == "https://api.example.com"
    assert updated.passthrough is True


@pytest.mark.anyio
async def test_delete_provider(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    created = await service.create_provider(
        AgentCustomProviderCreate(display_name="Delete me")
    )

    await service.delete_provider(created.id)

    with pytest.raises(TracecatNotFoundError):
        await service.get_provider(created.id)


@pytest.mark.anyio
async def test_refresh_provider_catalog_upserts_models(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    provider = await service.create_provider(
        AgentCustomProviderCreate(
            display_name="Refreshable",
            base_url="https://api.example.com",
        )
    )

    with patch.object(
        service,
        "_discover_models",
        return_value=[{"id": "model-a"}, {"id": "model-b"}],
    ):
        await service.refresh_provider_catalog(provider.id)

    catalog_rows = (
        (
            await session.execute(
                select(AgentCatalog).where(
                    AgentCatalog.custom_provider_id == provider.id
                )
            )
        )
        .scalars()
        .all()
    )

    assert {row.model_name for row in catalog_rows} == {"model-a", "model-b"}


@pytest.mark.anyio
async def test_refresh_provider_catalog_uses_migrated_encrypted_base_url_fallback(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    encrypted_config = encrypt_keyvalues(
        [
            SecretKeyValue(
                key="CUSTOM_MODEL_PROVIDER_BASE_URL",
                value=SecretStr("https://migrated.example.com/v1"),
            ),
            SecretKeyValue(
                key="CUSTOM_MODEL_PROVIDER_API_KEY",
                value=SecretStr("sk-migrated"),
            ),
        ],
        key=tracecat_config.TRACECAT__DB_ENCRYPTION_KEY or "",
    )
    provider = AgentCustomProvider(
        organization_id=svc_organization.id,
        display_name="Migrated",
        base_url=None,
        encrypted_config=encrypted_config,
        api_key_header="Authorization",
    )
    session.add(provider)
    await session.commit()

    with patch.object(
        service,
        "_discover_models",
        return_value=[{"id": "migrated-model"}],
    ) as discover:
        await service.refresh_provider_catalog(provider.id)

    discover.assert_awaited_once_with(
        "https://migrated.example.com/v1",
        api_key="sk-migrated",
        custom_headers=None,
        api_key_header="Authorization",
    )


@pytest.mark.anyio
async def test_validate_provider_success(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))

    class _Response:
        status_code = 200

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, headers: dict[str, str]):
            assert url == "https://api.example.com/models"
            assert headers == {"Authorization": "secret"}
            return _Response()

    with patch.object(
        provider_service_module.httpx, "AsyncClient", return_value=_Client()
    ):
        result = await service.validate_provider(
            base_url="https://api.example.com",
            api_key="secret",
            api_key_header="Authorization",
        )

    assert result is True


@pytest.mark.anyio
async def test_validate_provider_defaults_to_bearer_authorization(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))

    class _Response:
        status_code = 200

    class _Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, exc_type, exc, tb):
            return False

        async def get(self, url: str, headers: dict[str, str]):
            assert headers == {"Authorization": "Bearer secret"}
            return _Response()

    with patch.object(
        provider_service_module.httpx, "AsyncClient", return_value=_Client()
    ):
        result = await service.validate_provider(
            base_url="https://api.example.com",
            api_key="secret",
        )

    assert result is True


async def _load_raw_provider(
    session: AsyncSession, provider_id: uuid.UUID
) -> AgentCustomProvider:
    row = (
        await session.execute(
            select(AgentCustomProvider).where(AgentCustomProvider.id == provider_id)
        )
    ).scalar_one()
    return row


def _decrypted_secrets(raw: AgentCustomProvider) -> dict[str, object]:
    assert raw.encrypted_config is not None
    decrypted = decrypt_value(
        raw.encrypted_config,
        key=tracecat_config.TRACECAT__DB_ENCRYPTION_KEY or "",
    )
    return orjson.loads(decrypted)


@pytest.mark.anyio
async def test_update_provider_clears_api_key_on_null(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    created = await service.create_provider(
        AgentCustomProviderCreate(
            display_name="ClearApiKey",
            api_key="secret",
            custom_headers={"x-trace": "abc"},
        )
    )

    await service.update_provider(
        created.id,
        AgentCustomProviderUpdate(api_key=None),
    )

    raw = await _load_raw_provider(session, created.id)
    secrets = _decrypted_secrets(raw)
    assert "api_key" not in secrets
    assert secrets.get("custom_headers") == {"x-trace": "abc"}


@pytest.mark.anyio
async def test_update_provider_clears_custom_headers_on_empty_dict(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    created = await service.create_provider(
        AgentCustomProviderCreate(
            display_name="ClearHeaders",
            api_key="secret",
            custom_headers={"x-trace": "abc"},
        )
    )

    await service.update_provider(
        created.id,
        AgentCustomProviderUpdate(custom_headers={}),
    )

    raw = await _load_raw_provider(session, created.id)
    secrets = _decrypted_secrets(raw)
    assert "custom_headers" not in secrets
    assert secrets.get("api_key") == "secret"


@pytest.mark.anyio
async def test_update_provider_clears_all_secrets_sets_encrypted_config_null(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    created = await service.create_provider(
        AgentCustomProviderCreate(
            display_name="ClearAll",
            api_key="secret",
            custom_headers={"x-trace": "abc"},
        )
    )

    await service.update_provider(
        created.id,
        AgentCustomProviderUpdate(api_key=None, custom_headers={}),
    )

    raw = await _load_raw_provider(session, created.id)
    assert raw.encrypted_config is None
