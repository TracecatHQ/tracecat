"""Tests for AgentCustomProviderService."""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from unittest.mock import patch

import httpx
import orjson
import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config as tracecat_config
from tracecat.agent.exceptions import (
    ProviderKeyCheckError,
    ProviderKeyCheckErrorCode,
)
from tracecat.agent.provider import service as provider_service_module
from tracecat.agent.provider.schemas import (
    AgentCustomProviderCreate,
    AgentCustomProviderUpdate,
)
from tracecat.agent.provider.service import (
    AgentCustomProviderService,
    check_provider_key,
)
from tracecat.auth.types import Role
from tracecat.db.models import (
    AgentCatalog,
    AgentCustomProvider,
    Organization,
)
from tracecat.exceptions import TracecatNotFoundError
from tracecat.outbound import OutboundRequestDenied
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
async def test_provider_crud_lifecycle(
    session: AsyncSession,
    svc_organization: Organization,
) -> None:
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    created = await service.create_provider(
        AgentCustomProviderCreate(display_name="Provider 0")
    )
    assert created.id is not None
    assert created.display_name == "Provider 0"
    assert created.passthrough is False
    for i in (1, 2):
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

    await service.delete_provider(created.id)
    with pytest.raises(TracecatNotFoundError):
        await service.get_provider(created.id)
    with pytest.raises(TracecatNotFoundError):
        await service.get_provider(uuid.uuid4())


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
@pytest.mark.parametrize(
    ("update", "expected_secrets"),
    [
        pytest.param(
            AgentCustomProviderUpdate(api_key=None),
            {"custom_headers": {"x-trace": "abc"}},
            id="clear-api-key",
        ),
        pytest.param(
            AgentCustomProviderUpdate(custom_headers={}),
            {"api_key": "secret"},
            id="clear-custom-headers",
        ),
        pytest.param(
            AgentCustomProviderUpdate(api_key=None, custom_headers={}),
            None,
            id="clear-all",
        ),
    ],
)
async def test_update_provider_clears_secrets(
    session: AsyncSession,
    svc_organization: Organization,
    update: AgentCustomProviderUpdate,
    expected_secrets: dict[str, object] | None,
) -> None:
    """Clearing every secret nulls encrypted_config instead of storing ``{}``."""
    service = AgentCustomProviderService(session=session, role=_role(svc_organization))
    created = await service.create_provider(
        AgentCustomProviderCreate(
            display_name="ClearSecrets",
            api_key="secret",
            custom_headers={"x-trace": "abc"},
        )
    )

    await service.update_provider(created.id, update)

    raw = await _load_raw_provider(session, created.id)
    if expected_secrets is None:
        assert raw.encrypted_config is None
    else:
        assert _decrypted_secrets(raw) == expected_secrets


@dataclass
class _FakeFetch:
    response: httpx.Response | None = None
    error: Exception | None = None
    calls: list[dict[str, object]] = field(default_factory=list)

    async def __call__(self, **kwargs: object) -> httpx.Response:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


@pytest.fixture
def fake_fetch(monkeypatch: pytest.MonkeyPatch) -> _FakeFetch:
    fake = _FakeFetch(response=httpx.Response(200, json={"data": []}))
    monkeypatch.setattr(provider_service_module, "fetch_openai_compatible_models", fake)
    return fake


@pytest.mark.anyio
async def test_valid_key_passes(fake_fetch: _FakeFetch) -> None:
    await check_provider_key("openai", {"OPENAI_API_KEY": "sk-valid"})

    assert fake_fetch.calls == [
        {
            "base_url": "https://api.openai.com/v1",
            "api_key": "sk-valid",
            "api_key_header": None,
            "custom_headers": {},
            "timeout": 10.0,
        }
    ]


@pytest.mark.anyio
async def test_anthropic_uses_its_auth_headers(fake_fetch: _FakeFetch) -> None:
    await check_provider_key("anthropic", {"ANTHROPIC_API_KEY": "sk-ant"})

    [call] = fake_fetch.calls
    assert call["base_url"] == "https://api.anthropic.com/v1"
    assert call["api_key_header"] == "x-api-key"
    assert call["custom_headers"] == {"anthropic-version": "2023-06-01"}


# Real failure bodies captured from each provider's GET /models with a fake key.
_OPENAI_INVALID = {
    "error": {
        "message": "Incorrect API key provided: sk-inval****robe. You can find "
        "your API key at https://platform.openai.com/account/api-keys.",
        "type": "invalid_request_error",
        "param": None,
        "code": "invalid_api_key",
    }
}
_ANTHROPIC_INVALID = {
    "type": "error",
    "error": {"type": "authentication_error", "message": "invalid x-api-key"},
}
_GEMINI_INVALID = {
    "error": {
        "code": 400,
        "message": "API key not valid. Please pass a valid API key.",
        "status": "INVALID_ARGUMENT",
    }
}
_MISTRAL_INVALID = {"detail": "Invalid API Key"}
_PROVIDER_PHRASES = (
    "Incorrect API key",
    "x-api-key",
    "API key not valid",
    "Invalid API Key",
    "platform.openai.com",
    "PERMISSION_DENIED",
    "HTTP",
    "403",
)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "credentials", "status_code", "body", "expected"),
    [
        pytest.param(
            "openai",
            {"OPENAI_API_KEY": "sk-bad"},
            401,
            _OPENAI_INVALID,
            (
                ProviderKeyCheckErrorCode.INVALID_KEY,
                "OpenAI didn't accept this API key. Please check the key and "
                "try again.",
                "OPENAI_API_KEY",
            ),
            id="openai-invalid",
        ),
        pytest.param(
            "anthropic",
            {"ANTHROPIC_API_KEY": "bad"},
            401,
            _ANTHROPIC_INVALID,
            (
                ProviderKeyCheckErrorCode.INVALID_KEY,
                "Anthropic didn't accept this API key. Please check the key and "
                "try again.",
                "ANTHROPIC_API_KEY",
            ),
            id="anthropic-invalid",
        ),
        pytest.param(
            "gemini",
            {"GEMINI_API_KEY": "bad"},
            400,
            _GEMINI_INVALID,
            (
                ProviderKeyCheckErrorCode.INVALID_KEY,
                "Gemini API didn't accept this API key. Please check the key and "
                "try again.",
                "GEMINI_API_KEY",
            ),
            id="gemini-invalid",
        ),
        pytest.param(
            "mistral",
            {"MISTRAL_API_KEY": "bad"},
            401,
            _MISTRAL_INVALID,
            (
                ProviderKeyCheckErrorCode.INVALID_KEY,
                "Mistral AI didn't accept this API key. Please check the key and "
                "try again.",
                "MISTRAL_API_KEY",
            ),
            id="mistral-invalid",
        ),
        pytest.param(
            "gemini",
            {"GEMINI_API_KEY": "restricted"},
            403,
            {"error": {"code": 403, "status": "PERMISSION_DENIED"}},
            (
                ProviderKeyCheckErrorCode.ACCESS_DENIED,
                "This API key can't list Gemini API models. Please check the key's "
                "permissions and try again.",
                "GEMINI_API_KEY",
            ),
            id="gemini-forbidden",
        ),
        pytest.param(
            "anthropic",
            {"ANTHROPIC_API_KEY": "bad"},
            401,
            None,
            (
                ProviderKeyCheckErrorCode.INVALID_KEY,
                "Anthropic didn't accept this API key. Please check the key and "
                "try again.",
                "ANTHROPIC_API_KEY",
            ),
            id="no-body",
        ),
    ],
)
async def test_rejected_key_uses_owned_copy(
    fake_fetch: _FakeFetch,
    provider: str,
    credentials: dict[str, str],
    status_code: int,
    body: dict[str, object] | None,
    expected: tuple[ProviderKeyCheckErrorCode, str, str],
) -> None:
    fake_fetch.response = httpx.Response(status_code, json=body)

    with pytest.raises(ProviderKeyCheckError) as exc_info:
        await check_provider_key(provider, credentials)

    code, message, field = expected
    assert exc_info.value.detail == {"code": code, "message": message, "field": field}
    rendered = str(exc_info.value.detail)
    assert not any(phrase in rendered for phrase in _PROVIDER_PHRASES)


@pytest.mark.anyio
@pytest.mark.parametrize(
    "error",
    [
        pytest.param(httpx.ConnectTimeout("timed out"), id="timeout"),
        pytest.param(httpx.ConnectError(""), id="connect-error"),
        pytest.param(OutboundRequestDenied("Host is not allowed"), id="egress-denied"),
    ],
)
async def test_unreachable_provider_uses_owned_copy(
    fake_fetch: _FakeFetch, error: Exception
) -> None:
    fake_fetch.error = error

    with pytest.raises(ProviderKeyCheckError) as exc_info:
        await check_provider_key("openai", {"OPENAI_API_KEY": "sk-valid"})

    assert exc_info.value.detail == {
        "code": ProviderKeyCheckErrorCode.UNREACHABLE,
        "message": "Couldn't reach OpenAI to check this API key. Please check your "
        "network settings and try again.",
        "field": "OPENAI_API_KEY",
    }


@pytest.mark.anyio
async def test_openai_restricted_key_without_model_read_is_not_rejected(
    fake_fetch: _FakeFetch,
) -> None:
    fake_fetch.response = httpx.Response(
        401,
        json={
            "error": {
                "message": "You have insufficient permissions for this operation.",
                "code": None,
            }
        },
    )

    await check_provider_key("openai", {"OPENAI_API_KEY": "sk-restricted"})


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "credentials", "status_code"),
    [
        pytest.param("openai", {"OPENAI_API_KEY": "sk-valid"}, 429, id="429"),
        pytest.param("openai", {"OPENAI_API_KEY": "sk-valid"}, 503, id="503"),
        pytest.param(
            "anthropic", {"ANTHROPIC_API_KEY": "sk-ant"}, 400, id="non-gemini-400"
        ),
    ],
)
async def test_inconclusive_status_does_not_reject(
    fake_fetch: _FakeFetch,
    provider: str,
    credentials: dict[str, str],
    status_code: int,
) -> None:
    fake_fetch.response = httpx.Response(status_code)

    await check_provider_key(provider, credentials)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("provider", "credentials"),
    [
        pytest.param(
            "openai",
            {"OPENAI_API_KEY": "sk", "OPENAI_BASE_URL": "http://proxy.internal/v1"},
            id="custom-base-url",
        ),
        pytest.param("bedrock", {"AWS_ACCESS_KEY_ID": "id"}, id="unchecked-provider"),
    ],
)
async def test_skipped_without_provider_call(
    fake_fetch: _FakeFetch, provider: str, credentials: dict[str, str]
) -> None:
    await check_provider_key(provider, credentials)

    assert fake_fetch.calls == []
