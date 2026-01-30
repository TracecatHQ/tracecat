"""Platform settings service."""

from __future__ import annotations

import os
from typing import Any

import orjson
from pydantic import SecretStr
from pydantic_core import to_jsonable_python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.auth.types import PlatformRole
from tracecat.db.models import PlatformSetting
from tracecat.secrets.encryption import decrypt_value, encrypt_value
from tracecat.service import BasePlatformService
from tracecat_ee.admin.settings.schemas import (
    PlatformRegistrySettingsRead,
    PlatformRegistrySettingsUpdate,
)

# Platform settings keys that should be encrypted
SENSITIVE_PLATFORM_KEYS: set[str] = set()

# Registry-related platform settings
REGISTRY_SETTINGS_KEYS = {
    "git_repo_url",
    "git_repo_package_name",
    "git_allowed_domains",
}


class AdminSettingsService(BasePlatformService):
    """Platform-level settings management."""

    service_name = "admin_settings"

    def __init__(self, session: AsyncSession, role: PlatformRole):
        super().__init__(session, role)
        try:
            self._encryption_key = SecretStr(os.environ["TRACECAT__DB_ENCRYPTION_KEY"])
        except KeyError as e:
            raise KeyError("TRACECAT__DB_ENCRYPTION_KEY is not set") from e

    def _serialize_value(self, value: Any) -> bytes:
        return orjson.dumps(
            value, default=to_jsonable_python, option=orjson.OPT_SORT_KEYS
        )

    def _deserialize_value(self, value: bytes) -> Any:
        return orjson.loads(value)

    def _get_value(self, setting: PlatformSetting) -> Any:
        value_bytes = setting.value
        if setting.is_encrypted:
            value_bytes = decrypt_value(
                value_bytes, key=self._encryption_key.get_secret_value()
            )
        return self._deserialize_value(value_bytes)

    async def _get_settings(self, keys: set[str]) -> dict[str, Any]:
        """Get multiple platform settings."""
        stmt = select(PlatformSetting).where(PlatformSetting.key.in_(keys))
        result = await self.session.execute(stmt)
        return {s.key: self._get_value(s) for s in result.scalars().all()}

    async def _upsert_setting(self, key: str, value: Any) -> None:
        """Create or update a platform setting."""
        stmt = select(PlatformSetting).where(PlatformSetting.key == key)
        result = await self.session.execute(stmt)
        setting = result.scalar_one_or_none()

        is_sensitive = key in SENSITIVE_PLATFORM_KEYS
        value_bytes = self._serialize_value(value)
        if is_sensitive:
            value_bytes = encrypt_value(
                value_bytes, key=self._encryption_key.get_secret_value()
            )

        if setting:
            setting.value = value_bytes
            setting.is_encrypted = is_sensitive
        else:
            setting = PlatformSetting(
                key=key,
                value=value_bytes,
                value_type="json",
                is_encrypted=is_sensitive,
            )
            self.session.add(setting)

    async def get_registry_settings(self) -> PlatformRegistrySettingsRead:
        """Get platform registry settings."""
        settings = await self._get_settings(REGISTRY_SETTINGS_KEYS)
        return PlatformRegistrySettingsRead(
            git_repo_url=settings.get("git_repo_url"),
            git_repo_package_name=settings.get("git_repo_package_name"),
            git_allowed_domains=settings.get("git_allowed_domains"),
        )

    async def update_registry_settings(
        self, params: PlatformRegistrySettingsUpdate
    ) -> PlatformRegistrySettingsRead:
        """Update platform registry settings."""
        for key, value in params.model_dump(exclude_unset=True).items():
            if value is not None:
                # Convert set to list for JSON serialization
                if isinstance(value, set):
                    value = list(value)
                await self._upsert_setting(key, value)
        await self.session.commit()
        return await self.get_registry_settings()
