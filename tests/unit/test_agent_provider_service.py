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
from tracecat.agent.catalog.service import DiscoveredModel
from tracecat.agent.provider import service as provider_service_module
from tracecat.agent.provider.schemas import (
    AgentCustomProviderCreate,
    AgentCustomProviderUpdate,
)
from tracecat.agent.provider.service import AgentCustomProviderService
from tracecat.agent.provider.types import CustomProviderType
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
        "_discover_openai_models",
        return_value=[
            DiscoveredModel(model_name="model-a", metadata={"id": "model-a"}),
            DiscoveredModel(model_name="model-b", metadata={"id": "model-b"}),
        ],
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
        "_discover_openai_models",
        return_value=[
            DiscoveredModel(
                model_name="migrated-model", metadata={"id": "migrated-model"}
            )
        ],
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


def _recording_client(captured: list[str]) -> type:
    """Build an httpx.AsyncClient stand-in that records the requested URL."""

    class _Response:
        status_code = 200

    class _Client:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        async def __aenter__(self) -> _Client:
            return self

        async def __aexit__(self, exc_type: object, exc: object, tb: object) -> bool:
            return False

        async def get(self, url: str, headers: dict[str, str]) -> _Response:
            captured.append(url)
            return _Response()

    return _Client


@pytest.mark.anyio
@pytest.mark.parametrize(
    "base_url",
    ["http://localhost:11434", "http://localhost:11434/v1"],
)
async def test_validate_provider_ollama_probes_api_tags(
    session: AsyncSession,
    svc_organization: Organization,
    base_url: str,
) -> None:
    """Ollama validation hits ``/api/tags`` off the server root for bare and
    ``/v1``-suffixed URLs alike (strip is idempotent)."""
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    captured: list[str] = []

    with patch.object(
        provider_service_module.httpx, "AsyncClient", _recording_client(captured)
    ):
        result = await service.validate_provider(
            base_url=base_url,
            provider_type=CustomProviderType.OLLAMA,
        )

    assert result is True
    assert captured == ["http://localhost:11434/api/tags"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    "provider_type",
    [CustomProviderType.GENERIC_OPENAI_COMPATIBLE, CustomProviderType.LITELLM],
)
async def test_validate_provider_openai_types_probe_models(
    session: AsyncSession,
    svc_organization: Organization,
    provider_type: CustomProviderType,
) -> None:
    """Generic and LiteLLM validation still probe ``{base_url}/models``."""
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    captured: list[str] = []

    with patch.object(
        provider_service_module.httpx, "AsyncClient", _recording_client(captured)
    ):
        result = await service.validate_provider(
            base_url="https://gateway.example.com",
            provider_type=provider_type,
        )

    assert result is True
    assert captured == ["https://gateway.example.com/models"]


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


class _JSONResponse:
    """Minimal httpx.Response stand-in for discovery tests."""

    def __init__(self, payload: object) -> None:
        self._payload = payload

    def raise_for_status(self) -> None:
        return None

    def json(self) -> object:
        return self._payload


class _RecordingClient:
    """httpx.AsyncClient stub that records GET URLs and serves a payload map."""

    def __init__(self, payloads: dict[str, object]) -> None:
        self._payloads = payloads
        self.requested_urls: list[str] = []

    async def __aenter__(self) -> _RecordingClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def get(self, url: str, headers: dict[str, str]) -> _JSONResponse:
        self.requested_urls.append(url)
        return _JSONResponse(self._payloads[url])


def _patch_httpx(client: _RecordingClient):
    return patch.object(
        provider_service_module.httpx, "AsyncClient", return_value=client
    )


async def _catalog_row(
    session: AsyncSession, provider_id: uuid.UUID, model_name: str
) -> AgentCatalog:
    return (
        await session.execute(
            select(AgentCatalog).where(
                AgentCatalog.custom_provider_id == provider_id,
                AgentCatalog.model_name == model_name,
            )
        )
    ).scalar_one()


@pytest.mark.anyio
async def test_generic_refresh_only_calls_models_endpoint(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    provider = await service.create_provider(
        AgentCustomProviderCreate(
            display_name="Generic",
            base_url="https://api.example.com",
            type=CustomProviderType.GENERIC_OPENAI_COMPATIBLE,
        )
    )

    client = _RecordingClient(
        {"https://api.example.com/models": {"data": [{"id": "gpt-x"}]}}
    )
    with _patch_httpx(client):
        await service.refresh_provider_catalog(provider.id)

    assert client.requested_urls == ["https://api.example.com/models"]
    forbidden = ("/api/tags", "/api/show", "/v1/model/info", "/model_group/info")
    assert not any(any(f in url for f in forbidden) for url in client.requested_urls)


@pytest.mark.anyio
async def test_ollama_refresh_calls_tags_and_stores_digest(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    # base_url includes a trailing /v1 that must be stripped for the gateway root.
    provider = await service.create_provider(
        AgentCustomProviderCreate(
            display_name="Ollama",
            base_url="http://host:11434/v1",
            type=CustomProviderType.OLLAMA,
        )
    )

    client = _RecordingClient(
        {
            "http://host:11434/api/tags": {
                "models": [{"name": "llama3", "digest": "sha256:aaa"}]
            }
        }
    )
    with _patch_httpx(client):
        await service.refresh_provider_catalog(provider.id)

    assert client.requested_urls == ["http://host:11434/api/tags"]
    row = await _catalog_row(session, provider.id, "llama3")
    assert (row.model_metadata or {}).get("digest") == "sha256:aaa"


@pytest.mark.anyio
async def test_ollama_refresh_no_v1_suffix_unchanged(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    provider = await service.create_provider(
        AgentCustomProviderCreate(
            display_name="OllamaBare",
            base_url="http://host:11434",
            type=CustomProviderType.OLLAMA,
        )
    )

    client = _RecordingClient(
        {"http://host:11434/api/tags": {"models": [{"name": "llama3", "digest": "d1"}]}}
    )
    with _patch_httpx(client):
        await service.refresh_provider_catalog(provider.id)

    assert client.requested_urls == ["http://host:11434/api/tags"]


@pytest.mark.anyio
async def test_create_ollama_passthrough_true_succeeds(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    created = await service.create_provider(
        AgentCustomProviderCreate(
            display_name="Ollama",
            type=CustomProviderType.OLLAMA,
            passthrough=True,
        )
    )
    assert created.type is CustomProviderType.OLLAMA
    assert created.passthrough is True


@pytest.mark.anyio
async def test_update_ollama_passthrough_true_succeeds(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    created = await service.create_provider(
        AgentCustomProviderCreate(display_name="Prov")
    )

    updated = await service.update_provider(
        created.id,
        AgentCustomProviderUpdate(type=CustomProviderType.OLLAMA, passthrough=True),
    )

    assert updated.type is CustomProviderType.OLLAMA
    assert updated.passthrough is True


@pytest.mark.anyio
async def test_create_litellm_passthrough_false_accepted() -> None:
    provider = AgentCustomProviderCreate(
        display_name="LL",
        type=CustomProviderType.LITELLM,
        passthrough=False,
    )
    assert provider.passthrough is False


@pytest.mark.anyio
async def test_create_litellm_default_passthrough_false() -> None:
    # The schema default stays False for all types; the wizard supplies the
    # litellm-on prefill.
    provider = AgentCustomProviderCreate(
        display_name="LL", type=CustomProviderType.LITELLM
    )
    assert provider.passthrough is False


@pytest.mark.anyio
async def test_update_litellm_passthrough_false_succeeds(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    created = await service.create_provider(
        AgentCustomProviderCreate(display_name="Prov")
    )

    updated = await service.update_provider(
        created.id,
        AgentCustomProviderUpdate(type=CustomProviderType.LITELLM, passthrough=False),
    )

    assert updated.type is CustomProviderType.LITELLM
    assert updated.passthrough is False


@pytest.mark.anyio
async def test_update_flip_to_ollama_preserves_stored_passthrough(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    created = await service.create_provider(
        AgentCustomProviderCreate(display_name="Prov", passthrough=True)
    )

    updated = await service.update_provider(
        created.id,
        AgentCustomProviderUpdate(type=CustomProviderType.OLLAMA),
    )

    assert updated.type is CustomProviderType.OLLAMA
    # Type flips do not mutate stored passthrough.
    assert updated.passthrough is True


@pytest.mark.anyio
async def test_update_flip_to_litellm_preserves_stored_passthrough(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    created = await service.create_provider(
        AgentCustomProviderCreate(display_name="Prov", passthrough=False)
    )

    updated = await service.update_provider(
        created.id,
        AgentCustomProviderUpdate(type=CustomProviderType.LITELLM),
    )

    assert updated.type is CustomProviderType.LITELLM
    # Type flips do not mutate stored passthrough.
    assert updated.passthrough is False


@pytest.mark.anyio
async def test_update_flip_litellm_to_generic_preserves_stored_passthrough(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    created = await service.create_provider(
        AgentCustomProviderCreate(
            display_name="Prov",
            type=CustomProviderType.LITELLM,
            passthrough=True,
        )
    )
    assert created.passthrough is True

    updated = await service.update_provider(
        created.id,
        AgentCustomProviderUpdate(type=CustomProviderType.GENERIC_OPENAI_COMPATIBLE),
    )

    assert updated.type is CustomProviderType.GENERIC_OPENAI_COMPATIBLE
    # No type-driven mutation of stored passthrough.
    assert updated.passthrough is True


@pytest.mark.anyio
async def test_type_defaults_to_generic() -> None:
    provider = AgentCustomProviderCreate(display_name="Default")
    assert provider.type is CustomProviderType.GENERIC_OPENAI_COMPATIBLE
