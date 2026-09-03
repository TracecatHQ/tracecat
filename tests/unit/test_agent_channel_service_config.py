from __future__ import annotations

import pytest
from cryptography.fernet import Fernet

from tracecat.agent.channels import service as channel_service_module
from tracecat.agent.channels.schemas import ChannelType, SlackChannelTokenConfig
from tracecat.agent.channels.service import (
    ENCRYPTED_CONFIG_VALUE_PREFIX,
    AgentChannelService,
)


def test_serialize_and_parse_stored_slack_config_round_trip(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        channel_service_module.config,
        "TRACECAT__DB_ENCRYPTION_KEY",
        Fernet.generate_key().decode(),
    )
    config = SlackChannelTokenConfig(
        slack_bot_token="xoxb-real-secret",
        slack_client_id="12345.67890",
        slack_client_secret="client-secret",
        slack_signing_secret="signing-secret",
    )

    stored = AgentChannelService.serialize_channel_config_for_storage(
        channel_type=ChannelType.SLACK,
        config=config,
    )
    parsed = AgentChannelService.parse_stored_channel_config(
        channel_type=ChannelType.SLACK,
        config_payload=stored,
    )

    assert stored["slack_bot_token"].startswith(ENCRYPTED_CONFIG_VALUE_PREFIX)
    assert stored["slack_client_secret"].startswith(ENCRYPTED_CONFIG_VALUE_PREFIX)
    assert stored["slack_signing_secret"].startswith(ENCRYPTED_CONFIG_VALUE_PREFIX)
    assert parsed == config


def test_parse_stored_slack_config_supports_legacy_plaintext_rows() -> None:
    parsed = AgentChannelService.parse_stored_channel_config(
        channel_type=ChannelType.SLACK,
        config_payload={
            "slack_bot_token": "xoxb-real-secret",
            "slack_client_id": "12345.67890",
            "slack_client_secret": "client-secret",
            "slack_signing_secret": "signing-secret",
        },
    )

    assert parsed.slack_bot_token == "xoxb-real-secret"
    assert parsed.slack_client_id == "12345.67890"
    assert parsed.slack_client_secret == "client-secret"
    assert parsed.slack_signing_secret == "signing-secret"
