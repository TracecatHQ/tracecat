"""Service layer for external channel token management."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
import uuid
from typing import TYPE_CHECKING, Any, Literal
from urllib.parse import urlencode

from cryptography.exceptions import InvalidTag
from cryptography.fernet import InvalidToken
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import ValidationError
from slack_sdk.web.async_client import AsyncWebClient
from sqlalchemy import exists, select
from sqlalchemy.exc import IntegrityError

from tracecat import config
from tracecat.agent.channels.schemas import (
    AgentChannelTokenCreate,
    AgentChannelTokenRead,
    AgentChannelTokenUpdate,
    ChannelType,
    SlackChannelTokenConfig,
)
from tracecat.db.models import AgentChannelToken, AgentPreset
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.secrets.encryption import decrypt_value, encrypt_value
from tracecat.service import BaseWorkspaceService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession

PENDING_SLACK_BOT_TOKEN = "__tracecat_pending_bot_token__"
REDACTED_SLACK_SECRET = "********"
REDACTED_SLACK_SIGNING_SECRET_PREFIX = "__tracecat_pending_signing_secret__"
ENCRYPTED_CONFIG_VALUE_PREFIX = "__tracecat_encrypted__:"
SLACK_ENCRYPTED_CONFIG_FIELDS = (
    "slack_bot_token",
    "slack_client_secret",
    "slack_signing_secret",
)
SLACK_OAUTH_STATE_TTL_SECONDS = 10 * 60
SLACK_OAUTH_STATE_VERSION = "v2"
SLACK_OAUTH_STATE_NONCE_BYTES = 12
SLACK_APPROVAL_ACTION_TOKEN_TTL_SECONDS = 24 * 60 * 60
SLACK_OAUTH_SCOPES = (
    "app_mentions:read",
    "channels:history",
    "chat:write",
    "chat:write.customize",
    "groups:history",
    "im:history",
    "mpim:history",
    "reactions:read",
    "reactions:write",
    "users:read",
    "users:read.email",
)


class AgentChannelService(BaseWorkspaceService):
    """CRUD and token utilities for external channel integrations."""

    service_name = "agent_channel"

    @staticmethod
    def _require_signing_secret() -> str:
        signing_secret = config.TRACECAT__SIGNING_SECRET
        if not signing_secret:
            raise TracecatValidationError(
                "TRACECAT__SIGNING_SECRET must be set to use agent channel tokens"
            )
        return signing_secret

    @staticmethod
    def _require_db_encryption_key() -> str:
        encryption_key = (
            os.environ.get("TRACECAT__DB_ENCRYPTION_KEY")
            or config.TRACECAT__DB_ENCRYPTION_KEY
        )
        if not encryption_key:
            raise TracecatValidationError(
                "TRACECAT__DB_ENCRYPTION_KEY must be set to use agent channel tokens"
            )
        return encryption_key

    @classmethod
    def _encrypt_config_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        encrypted = encrypt_value(
            value.encode("utf-8"),
            key=cls._require_db_encryption_key(),
        ).decode("utf-8")
        return f"{ENCRYPTED_CONFIG_VALUE_PREFIX}{encrypted}"

    @classmethod
    def _decrypt_config_value(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if not value.startswith(ENCRYPTED_CONFIG_VALUE_PREFIX):
            # Backward compatibility for existing plaintext DB rows.
            return value
        encrypted = value.removeprefix(ENCRYPTED_CONFIG_VALUE_PREFIX)
        try:
            decrypted = decrypt_value(
                encrypted.encode("utf-8"),
                key=cls._require_db_encryption_key(),
            )
        except InvalidToken as exc:
            raise TracecatValidationError(
                "Failed to decrypt stored Slack channel config"
            ) from exc
        return decrypted.decode("utf-8")

    @staticmethod
    def _validate_hex(value: str, *, expected_len: int, field_name: str) -> str:
        if len(value) != expected_len:
            raise TracecatValidationError(f"Invalid channel token {field_name}")
        try:
            int(value, 16)
        except ValueError as exc:
            raise TracecatValidationError(
                f"Invalid channel token {field_name}"
            ) from exc
        return value.lower()

    @classmethod
    def parse_public_token(cls, token: str) -> tuple[uuid.UUID, str]:
        """Parse token path value in format `{id_hex}.{sig_hex}`."""

        if token.count(".") != 1:
            raise TracecatValidationError("Invalid channel token format")

        id_hex_raw, sig_hex_raw = token.split(".", maxsplit=1)
        id_hex = cls._validate_hex(id_hex_raw, expected_len=32, field_name="id")
        sig_hex = cls._validate_hex(
            sig_hex_raw, expected_len=64, field_name="signature"
        )

        try:
            token_id = uuid.UUID(hex=id_hex)
        except ValueError as exc:
            raise TracecatValidationError("Invalid channel token id") from exc
        return token_id, sig_hex

    @classmethod
    def compute_token_signature(cls, token_id: uuid.UUID) -> str:
        """Compute signature for token ID using locked format."""

        signing_secret = cls._require_signing_secret()
        return hashlib.sha256(
            f"act-{token_id.hex}{signing_secret}".encode()
        ).hexdigest()

    @classmethod
    def create_public_token(cls, token_id: uuid.UUID) -> str:
        signature = cls.compute_token_signature(token_id)
        return f"{token_id.hex}.{signature}"

    @classmethod
    def verify_public_token_signature(cls, token_id: uuid.UUID, sig_hex: str) -> bool:
        expected = cls.compute_token_signature(token_id)
        return secrets.compare_digest(sig_hex, expected)

    @classmethod
    def build_endpoint_url(cls, channel_type: ChannelType, public_token: str) -> str:
        return (
            f"{config.TRACECAT__PUBLIC_API_URL}/agent/channels/"
            f"{channel_type.value}/{public_token}"
        )

    @classmethod
    def build_slack_oauth_redirect_uri(cls) -> str:
        return f"{config.TRACECAT__PUBLIC_API_URL}/agent/channels/slack/oauth/callback"

    @classmethod
    def build_slack_oauth_authorization_url(cls, *, client_id: str, state: str) -> str:
        query = urlencode(
            {
                "client_id": client_id,
                "scope": ",".join(SLACK_OAUTH_SCOPES),
                "state": state,
                "redirect_uri": cls.build_slack_oauth_redirect_uri(),
            }
        )
        return f"https://slack.com/oauth/v2/authorize?{query}"

    @classmethod
    def create_slack_oauth_state(
        cls,
        *,
        token_id: uuid.UUID,
        workspace_id: uuid.UUID,
        return_url: str,
    ) -> str:
        payload_json = json.dumps(
            {
                "token_id": str(token_id),
                "workspace_id": str(workspace_id),
                "return_url": return_url,
                "exp": int(time.time()) + SLACK_OAUTH_STATE_TTL_SECONDS,
            },
            separators=(",", ":"),
        )
        payload_bytes = payload_json.encode()
        nonce = secrets.token_bytes(SLACK_OAUTH_STATE_NONCE_BYTES)
        ciphertext = AESGCM(cls._derive_slack_oauth_state_key()).encrypt(
            nonce, payload_bytes, None
        )
        nonce_b64 = cls._b64url_encode(nonce)
        ciphertext_b64 = cls._b64url_encode(ciphertext)
        return f"{SLACK_OAUTH_STATE_VERSION}.{nonce_b64}.{ciphertext_b64}"

    @classmethod
    def parse_slack_oauth_state(cls, state: str) -> dict[str, str]:
        if state.startswith(f"{SLACK_OAUTH_STATE_VERSION}."):
            payload = cls._parse_slack_oauth_state_v2(state)
        else:
            payload = cls._parse_slack_oauth_state_legacy(state)

        token_id = payload.get("token_id")
        workspace_id = payload.get("workspace_id")
        return_url = payload.get("return_url")
        exp = payload.get("exp")
        if (
            not isinstance(token_id, str)
            or not isinstance(workspace_id, str)
            or not isinstance(return_url, str)
            or not isinstance(exp, int)
        ):
            raise TracecatValidationError("Invalid OAuth state")
        if exp < int(time.time()):
            raise TracecatValidationError("OAuth state expired")
        return {
            "token_id": token_id,
            "workspace_id": workspace_id,
            "return_url": return_url,
        }

    @classmethod
    def _parse_slack_oauth_state_v2(cls, state: str) -> dict[str, Any]:
        parts = state.split(".")
        if len(parts) != 3:
            raise TracecatValidationError("Invalid OAuth state")
        _, nonce_b64, ciphertext_b64 = parts
        try:
            nonce = cls._b64url_decode(nonce_b64)
            if len(nonce) != SLACK_OAUTH_STATE_NONCE_BYTES:
                raise TracecatValidationError("Invalid OAuth state")
            ciphertext = cls._b64url_decode(ciphertext_b64)
            payload_bytes = AESGCM(cls._derive_slack_oauth_state_key()).decrypt(
                nonce, ciphertext, None
            )
            return json.loads(payload_bytes.decode())
        except (InvalidTag, ValueError, json.JSONDecodeError) as exc:
            raise TracecatValidationError("Invalid OAuth state") from exc

    @classmethod
    def _parse_slack_oauth_state_legacy(cls, state: str) -> dict[str, Any]:
        if state.count(".") != 1:
            raise TracecatValidationError("Invalid OAuth state")
        payload_b64, signature = state.split(".", maxsplit=1)
        expected_signature = hmac.new(
            cls._require_signing_secret().encode(),
            payload_b64.encode(),
            hashlib.sha256,
        ).hexdigest()
        if not secrets.compare_digest(signature, expected_signature):
            raise TracecatValidationError("Invalid OAuth state")

        try:
            payload_bytes = cls._b64url_decode(payload_b64)
            return json.loads(payload_bytes.decode())
        except (ValueError, json.JSONDecodeError) as exc:
            raise TracecatValidationError("Invalid OAuth state") from exc

    @classmethod
    def _derive_slack_oauth_state_key(cls) -> bytes:
        signing_secret = cls._require_signing_secret()
        return hmac.new(
            signing_secret.encode(),
            b"tracecat:agent:channels:slack-oauth-state:v2",
            hashlib.sha256,
        ).digest()

    @staticmethod
    def _b64url_encode(value: bytes) -> str:
        return base64.urlsafe_b64encode(value).decode().rstrip("=")

    @staticmethod
    def _b64url_decode(value: str) -> bytes:
        padding = "=" * (-len(value) % 4)
        return base64.urlsafe_b64decode(value + padding)

    @classmethod
    def create_slack_approval_action_token(
        cls,
        *,
        batch_id: str,
        tool_call_id: str,
        action: Literal["approve", "deny"],
    ) -> str:
        payload_json = json.dumps(
            {
                "batch_id": batch_id,
                "tool_call_id": tool_call_id,
                "action": action,
                "exp": int(time.time()) + SLACK_APPROVAL_ACTION_TOKEN_TTL_SECONDS,
            },
            separators=(",", ":"),
        )
        payload_b64 = cls._b64url_encode(payload_json.encode())
        signature = hmac.new(
            cls._require_signing_secret().encode(),
            payload_b64.encode(),
            hashlib.sha256,
        ).hexdigest()
        return f"{payload_b64}.{signature}"

    @classmethod
    def parse_slack_approval_action_token(cls, token: str) -> dict[str, str]:
        if token.count(".") != 1:
            raise TracecatValidationError("Invalid approval action token")

        payload_b64, signature = token.split(".", maxsplit=1)
        expected_signature = hmac.new(
            cls._require_signing_secret().encode(),
            payload_b64.encode(),
            hashlib.sha256,
        ).hexdigest()
        if not secrets.compare_digest(signature, expected_signature):
            raise TracecatValidationError("Invalid approval action token")

        try:
            payload = json.loads(cls._b64url_decode(payload_b64).decode())
        except (ValueError, json.JSONDecodeError) as exc:
            raise TracecatValidationError("Invalid approval action token") from exc

        batch_id = payload.get("batch_id")
        tool_call_id = payload.get("tool_call_id")
        action = payload.get("action")
        exp = payload.get("exp")
        if (
            not isinstance(batch_id, str)
            or not isinstance(tool_call_id, str)
            or action not in {"approve", "deny"}
            or not isinstance(exp, int)
        ):
            raise TracecatValidationError("Invalid approval action token")
        if exp < int(time.time()):
            raise TracecatValidationError("Approval action token expired")

        return {
            "batch_id": batch_id,
            "tool_call_id": tool_call_id,
            "action": action,
        }

    @classmethod
    async def exchange_slack_oauth_code(
        cls,
        *,
        client_id: str,
        client_secret: str,
        code: str,
    ) -> str:
        response = await AsyncWebClient().oauth_v2_access(
            client_id=client_id,
            client_secret=client_secret,
            code=code,
            redirect_uri=cls.build_slack_oauth_redirect_uri(),
        )
        access_token = response.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise TracecatValidationError(
                "Slack OAuth response missing bot access token"
            )
        return access_token

    @staticmethod
    def _validate_channel_config(
        channel_type: ChannelType,
        config_payload: dict[str, Any] | SlackChannelTokenConfig,
    ) -> SlackChannelTokenConfig:
        if channel_type is ChannelType.SLACK:
            if isinstance(config_payload, SlackChannelTokenConfig):
                return config_payload
            try:
                return SlackChannelTokenConfig.model_validate(config_payload)
            except ValidationError as exc:
                raise TracecatValidationError("Invalid Slack channel config") from exc

        raise TracecatValidationError(f"Unsupported channel type: {channel_type.value}")

    @classmethod
    def serialize_channel_config_for_storage(
        cls,
        *,
        channel_type: ChannelType,
        config: SlackChannelTokenConfig,
    ) -> dict[str, Any]:
        if channel_type is ChannelType.SLACK:
            payload = config.model_dump()
            for field in SLACK_ENCRYPTED_CONFIG_FIELDS:
                raw_value = payload.get(field)
                if raw_value is not None and not isinstance(raw_value, str):
                    raise TracecatValidationError("Invalid Slack channel config")
                payload[field] = cls._encrypt_config_value(raw_value)
            return payload

        raise TracecatValidationError(f"Unsupported channel type: {channel_type.value}")

    @classmethod
    def parse_stored_channel_config(
        cls,
        *,
        channel_type: ChannelType,
        config_payload: dict[str, Any] | SlackChannelTokenConfig,
    ) -> SlackChannelTokenConfig:
        if channel_type is ChannelType.SLACK:
            if isinstance(config_payload, SlackChannelTokenConfig):
                return config_payload
            if not isinstance(config_payload, dict):
                raise TracecatValidationError("Invalid Slack channel config")

            payload = dict(config_payload)
            for field in SLACK_ENCRYPTED_CONFIG_FIELDS:
                raw_value = payload.get(field)
                if raw_value is not None and not isinstance(raw_value, str):
                    raise TracecatValidationError("Invalid Slack channel config")
                payload[field] = cls._decrypt_config_value(raw_value)

            return cls._validate_channel_config(channel_type, payload)

        raise TracecatValidationError(f"Unsupported channel type: {channel_type.value}")

    @staticmethod
    def _redact_slack_config(
        config: SlackChannelTokenConfig,
    ) -> SlackChannelTokenConfig:
        redacted_marker = f"{REDACTED_SLACK_SIGNING_SECRET_PREFIX}redacted"
        redacted_signing_secret = (
            config.slack_signing_secret
            if config.slack_signing_secret == redacted_marker
            else redacted_marker
        )
        return SlackChannelTokenConfig(
            slack_bot_token=(
                PENDING_SLACK_BOT_TOKEN
                if config.slack_bot_token == PENDING_SLACK_BOT_TOKEN
                else REDACTED_SLACK_SECRET
            ),
            slack_client_id=config.slack_client_id,
            slack_client_secret=None,
            slack_signing_secret=redacted_signing_secret,
        )

    async def _require_workspace_preset(self, preset_id: uuid.UUID) -> None:
        stmt = select(
            exists().where(
                AgentPreset.id == preset_id,
                AgentPreset.workspace_id == self.workspace_id,
            )
        )
        result = await self.session.execute(stmt)
        if not result.scalar():
            raise TracecatNotFoundError(
                f"Agent preset with ID '{preset_id}' not found in workspace"
            )

    async def create_token(self, params: AgentChannelTokenCreate) -> AgentChannelToken:
        await self._require_workspace_preset(params.agent_preset_id)

        validated_config = self._validate_channel_config(
            params.channel_type, params.config
        )
        if (
            params.is_active
            and validated_config.slack_bot_token == PENDING_SLACK_BOT_TOKEN
        ):
            raise TracecatValidationError(
                "Cannot activate token without Slack bot token"
            )

        token = AgentChannelToken(
            workspace_id=self.workspace_id,
            agent_preset_id=params.agent_preset_id,
            channel_type=params.channel_type.value,
            config=self.serialize_channel_config_for_storage(
                channel_type=params.channel_type,
                config=validated_config,
            ),
            is_active=params.is_active,
        )
        self.session.add(token)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise TracecatValidationError(
                "Active token already exists for this preset and channel type"
            ) from exc
        await self.session.refresh(token)
        return token

    async def list_tokens(
        self,
        *,
        agent_preset_id: uuid.UUID | None = None,
        channel_type: ChannelType | None = None,
    ) -> list[AgentChannelToken]:
        stmt = select(AgentChannelToken).where(
            AgentChannelToken.workspace_id == self.workspace_id
        )
        if agent_preset_id is not None:
            stmt = stmt.where(AgentChannelToken.agent_preset_id == agent_preset_id)
        if channel_type is not None:
            stmt = stmt.where(AgentChannelToken.channel_type == channel_type.value)
        stmt = stmt.order_by(AgentChannelToken.created_at.desc())
        result = await self.session.execute(stmt)
        return list(result.scalars().all())

    async def get_token(self, token_id: uuid.UUID) -> AgentChannelToken | None:
        stmt = select(AgentChannelToken).where(
            AgentChannelToken.id == token_id,
            AgentChannelToken.workspace_id == self.workspace_id,
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def get_active_token_for_preset(
        self, *, agent_preset_id: uuid.UUID, channel_type: ChannelType
    ) -> AgentChannelToken | None:
        stmt = (
            select(AgentChannelToken)
            .where(
                AgentChannelToken.workspace_id == self.workspace_id,
                AgentChannelToken.agent_preset_id == agent_preset_id,
                AgentChannelToken.channel_type == channel_type.value,
                AgentChannelToken.is_active.is_(True),
            )
            .order_by(AgentChannelToken.updated_at.desc())
            .limit(1)
        )
        result = await self.session.execute(stmt)
        return result.scalar_one_or_none()

    async def update_token(
        self, token: AgentChannelToken, params: AgentChannelTokenUpdate
    ) -> AgentChannelToken:
        set_fields = params.model_dump(exclude_unset=True)
        validated_config: SlackChannelTokenConfig | None = None
        channel_type = ChannelType(token.channel_type)

        if "config" in set_fields and set_fields["config"] is not None:
            validated_config = self._validate_channel_config(
                channel_type, set_fields["config"]
            )
            token.config = self.serialize_channel_config_for_storage(
                channel_type=channel_type,
                config=validated_config,
            )

        next_is_active = token.is_active
        if "is_active" in set_fields:
            if set_fields["is_active"] is None:
                raise TracecatValidationError("is_active cannot be null")
            next_is_active = set_fields["is_active"]

        current_config = validated_config
        if current_config is None:
            current_config = self.parse_stored_channel_config(
                channel_type=channel_type,
                config_payload=token.config,
            )
        if next_is_active and current_config.slack_bot_token == PENDING_SLACK_BOT_TOKEN:
            raise TracecatValidationError(
                "Cannot activate token without Slack bot token"
            )

        if "is_active" in set_fields:
            token.is_active = next_is_active

        self.session.add(token)
        try:
            await self.session.commit()
        except IntegrityError as exc:
            await self.session.rollback()
            raise TracecatValidationError(
                "Active token already exists for this preset and channel type"
            ) from exc
        await self.session.refresh(token)
        return token

    async def rotate_token_signature(
        self, token: AgentChannelToken
    ) -> AgentChannelToken:
        token.id = uuid.uuid4()
        self.session.add(token)
        await self.session.commit()
        await self.session.refresh(token)
        return token

    async def delete_token(self, token: AgentChannelToken) -> None:
        await self.session.delete(token)
        await self.session.commit()

    @classmethod
    async def get_token_for_public_request(
        cls,
        session: AsyncSession,
        *,
        token_id: uuid.UUID,
        channel_type: ChannelType,
        require_active: bool = True,
    ) -> AgentChannelToken | None:
        stmt = select(AgentChannelToken).where(
            AgentChannelToken.id == token_id,
            AgentChannelToken.channel_type == channel_type.value,
        )
        if require_active:
            stmt = stmt.where(AgentChannelToken.is_active.is_(True))
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    def to_read(self, token: AgentChannelToken) -> AgentChannelTokenRead:
        channel_type = ChannelType(token.channel_type)
        validated_config = self.parse_stored_channel_config(
            channel_type=channel_type,
            config_payload=token.config,
        )
        redacted_config = self._redact_slack_config(validated_config)
        public_token = self.create_public_token(token.id)
        endpoint_url = self.build_endpoint_url(channel_type, public_token)
        return AgentChannelTokenRead(
            id=token.id,
            workspace_id=token.workspace_id,
            agent_preset_id=token.agent_preset_id,
            channel_type=channel_type,
            config=redacted_config,
            is_active=token.is_active,
            public_token=public_token,
            endpoint_url=endpoint_url,
            created_at=token.created_at,
            updated_at=token.updated_at,
        )
