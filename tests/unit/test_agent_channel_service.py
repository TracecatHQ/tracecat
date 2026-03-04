from __future__ import annotations

import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy.ext.asyncio import AsyncSession

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
