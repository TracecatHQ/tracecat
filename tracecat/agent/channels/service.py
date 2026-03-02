"""Service layer for external channel token management."""

from __future__ import annotations

import hashlib
import secrets
import uuid
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError
from sqlalchemy import select
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
from tracecat.service import BaseWorkspaceService

if TYPE_CHECKING:
    from sqlalchemy.ext.asyncio import AsyncSession


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

    async def _require_workspace_preset(self, preset_id: uuid.UUID) -> None:
        stmt = select(AgentPreset.id).where(
            AgentPreset.id == preset_id,
            AgentPreset.workspace_id == self.workspace_id,
        )
        result = await self.session.execute(stmt)
        if result.scalar_one_or_none() is None:
            raise TracecatNotFoundError(
                f"Agent preset with ID '{preset_id}' not found in workspace"
            )

    async def create_token(self, params: AgentChannelTokenCreate) -> AgentChannelToken:
        await self._require_workspace_preset(params.agent_preset_id)

        validated_config = self._validate_channel_config(
            params.channel_type, params.config
        )

        token = AgentChannelToken(
            workspace_id=self.workspace_id,
            agent_preset_id=params.agent_preset_id,
            channel_type=params.channel_type.value,
            config=validated_config.model_dump(),
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

        if "config" in set_fields and set_fields["config"] is not None:
            validated_config = self._validate_channel_config(
                ChannelType(token.channel_type), set_fields["config"]
            )
            token.config = validated_config.model_dump()

        if "is_active" in set_fields:
            token.is_active = set_fields["is_active"]

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
    async def get_active_token_for_public_request(
        cls,
        session: AsyncSession,
        *,
        token_id: uuid.UUID,
        channel_type: ChannelType,
    ) -> AgentChannelToken | None:
        stmt = select(AgentChannelToken).where(
            AgentChannelToken.id == token_id,
            AgentChannelToken.channel_type == channel_type.value,
            AgentChannelToken.is_active.is_(True),
        )
        result = await session.execute(stmt)
        return result.scalar_one_or_none()

    def to_read(self, token: AgentChannelToken) -> AgentChannelTokenRead:
        channel_type = ChannelType(token.channel_type)
        validated_config = self._validate_channel_config(channel_type, token.config)
        public_token = self.create_public_token(token.id)
        endpoint_url = self.build_endpoint_url(channel_type, public_token)
        return AgentChannelTokenRead(
            id=token.id,
            workspace_id=token.workspace_id,
            agent_preset_id=token.agent_preset_id,
            channel_type=channel_type,
            config=validated_config,
            is_active=token.is_active,
            public_token=public_token,
            endpoint_url=endpoint_url,
            created_at=token.created_at,
            updated_at=token.updated_at,
        )
