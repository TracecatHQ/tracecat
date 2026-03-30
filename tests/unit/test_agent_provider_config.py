from __future__ import annotations

import uuid
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr

from tracecat.agent.catalog.service import AgentCatalogService
from tracecat.agent.provider_config import deserialize_secret_keyvalues
from tracecat.agent.runtime.service import AgentRuntimeService
from tracecat.agent.selections.service import AgentSelectionsService
from tracecat.auth.types import Role
from tracecat.secrets.encryption import encrypt_keyvalues
from tracecat.secrets.schemas import SecretKeyValue


@pytest.fixture
def role() -> Role:
    return Role(
        type="service",
        service_id="tracecat-api",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=uuid.uuid4(),
        scopes=frozenset(
            {
                "agent:read",
                "agent:update",
                "org:secret:read",
                "workspace:read",
                "workspace:update",
            }
        ),
    )


def _encrypted_provider_payload(monkeypatch: pytest.MonkeyPatch) -> bytes:
    key = Fernet.generate_key().decode()
    monkeypatch.setattr(
        "tracecat.agent.provider_config.config.TRACECAT__DB_ENCRYPTION_KEY",
        key,
    )
    return encrypt_keyvalues(
        [
            SecretKeyValue(key="ANTHROPIC_API_KEY", value=SecretStr("test")),
            SecretKeyValue(
                key="ANTHROPIC_BASE_URL",
                value=SecretStr("https://anthropic.example"),
            ),
        ],
        key=key,
    )


def test_deserialize_secret_keyvalues_round_trips(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _encrypted_provider_payload(monkeypatch)

    assert deserialize_secret_keyvalues(payload) == {
        "ANTHROPIC_API_KEY": "test",
        "ANTHROPIC_BASE_URL": "https://anthropic.example",
    }


@pytest.mark.anyio
@pytest.mark.parametrize(
    "service_cls",
    [AgentCatalogService, AgentRuntimeService, AgentSelectionsService],
    ids=["catalog", "runtime", "selections"],
)
async def test_services_load_provider_credentials_from_encrypted_org_secret(
    service_cls: type[AgentCatalogService]
    | type[AgentRuntimeService]
    | type[AgentSelectionsService],
    role: Role,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = _encrypted_provider_payload(monkeypatch)
    execute_result = Mock()
    execute_result.scalar_one_or_none.return_value = SimpleNamespace(
        encrypted_keys=payload
    )
    session = AsyncMock()
    session.execute = AsyncMock(return_value=execute_result)

    service = service_cls(session, role=role)

    credentials = await service._load_provider_credentials("anthropic")

    assert credentials == {
        "ANTHROPIC_API_KEY": "test",
        "ANTHROPIC_BASE_URL": "https://anthropic.example",
    }
