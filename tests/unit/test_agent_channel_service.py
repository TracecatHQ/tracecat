from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.agent.channels import service as channel_service_module
from tracecat.agent.channels.schemas import (
    AgentChannelTokenCreate,
    AgentChannelTokenUpdate,
    ChannelType,
    SlackChannelTokenConfig,
)
from tracecat.agent.channels.service import (
    ENCRYPTED_CONFIG_VALUE_PREFIX,
    PENDING_SLACK_BOT_TOKEN,
    REDACTED_SLACK_SECRET,
    REDACTED_SLACK_SIGNING_SECRET_PREFIX,
    SLACK_OAUTH_STATE_TTL_SECONDS,
    AgentChannelService,
)
from tracecat.auth.types import Role
from tracecat.db.models import AgentPreset, Workspace
from tracecat.exceptions import TracecatValidationError

pytestmark = pytest.mark.usefixtures("db")


@pytest.fixture(autouse=True)
def set_db_encryption_key(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        channel_service_module.config,
        "TRACECAT__DB_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )


@pytest.fixture
async def agent_channel_service(
    session: AsyncSession, svc_role: Role
) -> AgentChannelService:
    return AgentChannelService(session=session, role=svc_role)


@pytest.fixture
async def agent_preset(session: AsyncSession, svc_workspace: Workspace) -> AgentPreset:
    preset = AgentPreset(
        workspace_id=svc_workspace.id,
        name="Slack Agent",
        slug=f"slack-agent-{uuid.uuid4().hex[:8]}",
        model_name="gpt-4o-mini",
        model_provider="openai",
    )
    session.add(preset)
    await session.commit()
    await session.refresh(preset)
    return preset


@pytest.mark.anyio
async def test_to_read_redacts_slack_secrets(
    agent_channel_service: AgentChannelService,
    agent_preset: AgentPreset,
) -> None:
    token = await agent_channel_service.create_token(
        AgentChannelTokenCreate(
            agent_preset_id=agent_preset.id,
            channel_type=ChannelType.SLACK,
            config=SlackChannelTokenConfig(
                slack_bot_token="xoxb-real-secret",
                slack_client_id="12345.67890",
                slack_client_secret="client-secret",
                slack_signing_secret="signing-secret",
            ),
            is_active=False,
        )
    )

    read_token = agent_channel_service.to_read(token)

    assert read_token.config.slack_bot_token == REDACTED_SLACK_SECRET
    assert read_token.config.slack_client_id == "12345.67890"
    assert read_token.config.slack_client_secret is None
    assert (
        read_token.config.slack_signing_secret
        == f"{REDACTED_SLACK_SIGNING_SECRET_PREFIX}redacted"
    )


@pytest.mark.anyio
async def test_to_read_preserves_pending_bot_token_placeholder(
    agent_channel_service: AgentChannelService,
    agent_preset: AgentPreset,
) -> None:
    token = await agent_channel_service.create_token(
        AgentChannelTokenCreate(
            agent_preset_id=agent_preset.id,
            channel_type=ChannelType.SLACK,
            config=SlackChannelTokenConfig(
                slack_bot_token=PENDING_SLACK_BOT_TOKEN,
                slack_client_id="12345.67890",
                slack_client_secret="client-secret",
                slack_signing_secret="__tracecat_pending_signing_secret__placeholder",
            ),
            is_active=False,
        )
    )

    read_token = agent_channel_service.to_read(token)

    assert read_token.config.slack_bot_token == PENDING_SLACK_BOT_TOKEN
    assert (
        read_token.config.slack_signing_secret
        == f"{REDACTED_SLACK_SIGNING_SECRET_PREFIX}redacted"
    )


@pytest.mark.anyio
async def test_to_read_redacts_prefix_colliding_slack_signing_secret(
    agent_channel_service: AgentChannelService,
    agent_preset: AgentPreset,
) -> None:
    token = await agent_channel_service.create_token(
        AgentChannelTokenCreate(
            agent_preset_id=agent_preset.id,
            channel_type=ChannelType.SLACK,
            config=SlackChannelTokenConfig(
                slack_bot_token="xoxb-real-secret",
                slack_client_id="12345.67890",
                slack_client_secret="client-secret",
                slack_signing_secret=(
                    f"{REDACTED_SLACK_SIGNING_SECRET_PREFIX}looks-like-real"
                ),
            ),
            is_active=False,
        )
    )

    read_token = agent_channel_service.to_read(token)

    assert (
        read_token.config.slack_signing_secret
        == f"{REDACTED_SLACK_SIGNING_SECRET_PREFIX}redacted"
    )


@pytest.mark.anyio
async def test_update_token_rejects_null_is_active(
    agent_channel_service: AgentChannelService,
    agent_preset: AgentPreset,
) -> None:
    token = await agent_channel_service.create_token(
        AgentChannelTokenCreate(
            agent_preset_id=agent_preset.id,
            channel_type=ChannelType.SLACK,
            config=SlackChannelTokenConfig(
                slack_bot_token="xoxb-real-secret",
                slack_client_id="12345.67890",
                slack_client_secret="client-secret",
                slack_signing_secret="signing-secret",
            ),
            is_active=True,
        )
    )

    with pytest.raises(TracecatValidationError, match="is_active cannot be null"):
        await agent_channel_service.update_token(
            token,
            AgentChannelTokenUpdate(is_active=None),
        )


@pytest.mark.anyio
async def test_create_token_encrypts_sensitive_slack_config_at_rest(
    agent_channel_service: AgentChannelService,
    agent_preset: AgentPreset,
) -> None:
    token = await agent_channel_service.create_token(
        AgentChannelTokenCreate(
            agent_preset_id=agent_preset.id,
            channel_type=ChannelType.SLACK,
            config=SlackChannelTokenConfig(
                slack_bot_token="xoxb-real-secret",
                slack_client_id="12345.67890",
                slack_client_secret="client-secret",
                slack_signing_secret="signing-secret",
            ),
            is_active=False,
        )
    )

    assert token.config["slack_bot_token"].startswith(ENCRYPTED_CONFIG_VALUE_PREFIX)
    assert token.config["slack_client_secret"].startswith(ENCRYPTED_CONFIG_VALUE_PREFIX)
    assert token.config["slack_signing_secret"].startswith(
        ENCRYPTED_CONFIG_VALUE_PREFIX
    )
    assert token.config["slack_client_id"] == "12345.67890"


@pytest.mark.anyio
async def test_update_token_rejects_pending_bot_token_when_token_remains_active(
    agent_channel_service: AgentChannelService,
    agent_preset: AgentPreset,
) -> None:
    token = await agent_channel_service.create_token(
        AgentChannelTokenCreate(
            agent_preset_id=agent_preset.id,
            channel_type=ChannelType.SLACK,
            config=SlackChannelTokenConfig(
                slack_bot_token="xoxb-real-secret",
                slack_client_id="12345.67890",
                slack_client_secret="client-secret",
                slack_signing_secret="signing-secret",
            ),
            is_active=True,
        )
    )

    with pytest.raises(
        TracecatValidationError,
        match="Cannot activate token without Slack bot token",
    ):
        await agent_channel_service.update_token(
            token,
            AgentChannelTokenUpdate(
                config=SlackChannelTokenConfig(
                    slack_bot_token=PENDING_SLACK_BOT_TOKEN,
                    slack_client_id="12345.67890",
                    slack_client_secret="client-secret",
                    slack_signing_secret="signing-secret",
                )
            ),
        )


def _set_signing_secret(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(config, "TRACECAT__SIGNING_SECRET", "test-signing-secret")


def _build_legacy_state(
    *,
    token_id: uuid.UUID,
    workspace_id: uuid.UUID,
    return_url: str,
    exp: int,
    signing_secret: str,
) -> str:
    payload_json = json.dumps(
        {
            "token_id": str(token_id),
            "workspace_id": str(workspace_id),
            "return_url": return_url,
            "exp": exp,
        },
        separators=(",", ":"),
    )
    payload_b64 = base64.urlsafe_b64encode(payload_json.encode()).decode().rstrip("=")
    signature = hmac.new(
        signing_secret.encode(),
        payload_b64.encode(),
        hashlib.sha256,
    ).hexdigest()
    return f"{payload_b64}.{signature}"


def _b64url_encode(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(value + padding)


def test_create_and_parse_slack_oauth_state_v2(monkeypatch: pytest.MonkeyPatch) -> None:
    _set_signing_secret(monkeypatch)
    monkeypatch.setattr("tracecat.agent.channels.service.time.time", lambda: 1_000_000)

    token_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    return_url = "https://app.example.com/workspaces/abc/agents/def?builderPrompt=test"
    state = AgentChannelService.create_slack_oauth_state(
        token_id=token_id,
        workspace_id=workspace_id,
        return_url=return_url,
    )

    assert state.startswith("v2.")
    assert str(token_id) not in state
    payload = AgentChannelService.parse_slack_oauth_state(state)
    assert payload == {
        "token_id": str(token_id),
        "workspace_id": str(workspace_id),
        "return_url": return_url,
    }


def test_parse_slack_oauth_state_v2_rejects_tampering(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_signing_secret(monkeypatch)
    monkeypatch.setattr("tracecat.agent.channels.service.time.time", lambda: 1_000_000)

    state = AgentChannelService.create_slack_oauth_state(
        token_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        return_url="https://app.example.com/workspaces/abc/agents/def",
    )
    version, nonce_b64, ciphertext_b64 = state.split(".")
    ciphertext = bytearray(_b64url_decode(ciphertext_b64))
    ciphertext[0] ^= 0x01
    tampered_state = f"{version}.{nonce_b64}.{_b64url_encode(bytes(ciphertext))}"

    with pytest.raises(TracecatValidationError, match="Invalid OAuth state"):
        AgentChannelService.parse_slack_oauth_state(tampered_state)


def test_parse_slack_oauth_state_v2_rejects_expired_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_signing_secret(monkeypatch)
    monkeypatch.setattr("tracecat.agent.channels.service.time.time", lambda: 1_000_000)
    state = AgentChannelService.create_slack_oauth_state(
        token_id=uuid.uuid4(),
        workspace_id=uuid.uuid4(),
        return_url="https://app.example.com/workspaces/abc/agents/def",
    )
    monkeypatch.setattr(
        "tracecat.agent.channels.service.time.time",
        lambda: 1_000_000 + SLACK_OAUTH_STATE_TTL_SECONDS + 1,
    )

    with pytest.raises(TracecatValidationError, match="OAuth state expired"):
        AgentChannelService.parse_slack_oauth_state(state)


def test_parse_slack_oauth_state_accepts_legacy_signed_state(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signing_secret = "test-signing-secret"
    monkeypatch.setattr(config, "TRACECAT__SIGNING_SECRET", signing_secret)
    monkeypatch.setattr("tracecat.agent.channels.service.time.time", lambda: 1_000_000)

    token_id = uuid.uuid4()
    workspace_id = uuid.uuid4()
    return_url = "https://app.example.com/workspaces/abc/agents/def"
    legacy_state = _build_legacy_state(
        token_id=token_id,
        workspace_id=workspace_id,
        return_url=return_url,
        exp=1_000_000 + SLACK_OAUTH_STATE_TTL_SECONDS,
        signing_secret=signing_secret,
    )

    payload = AgentChannelService.parse_slack_oauth_state(legacy_state)
    assert payload == {
        "token_id": str(token_id),
        "workspace_id": str(workspace_id),
        "return_url": return_url,
    }
