import os
from collections.abc import Sequence
from typing import Any

import orjson
from async_lru import alru_cache
from pydantic import BaseModel, SecretStr
from pydantic_core import to_jsonable_python
from sqlmodel import col, select
from sqlmodel.ext.asyncio.session import AsyncSession

from tracecat import config
from tracecat.authz.controls import require_access_level
from tracecat.contexts import ctx_role
from tracecat.db.schemas import OrganizationSetting
from tracecat.logger import logger
from tracecat.secrets.encryption import decrypt_value, encrypt_value
from tracecat.service import BaseService
from tracecat.settings.constants import PUBLIC_SETTINGS_KEYS, SENSITIVE_SETTINGS_KEYS
from tracecat.settings.models import (
    AuthSettingsUpdate,
    BaseSettingsGroup,
    GitSettingsUpdate,
    OAuthSettingsUpdate,
    SAMLSettingsUpdate,
    SettingCreate,
    SettingUpdate,
)
from tracecat.types.auth import AccessLevel, Role


class SettingsService(BaseService):
    """Service for managing platform settings"""

    service_name = "settings"
    groups: list[type[BaseSettingsGroup]] = [
        GitSettingsUpdate,
        SAMLSettingsUpdate,
        AuthSettingsUpdate,
        OAuthSettingsUpdate,
    ]
    """The set of settings groups that are managed by the service."""

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role=role)
        try:
            self._encryption_key = SecretStr(os.environ["TRACECAT__DB_ENCRYPTION_KEY"])
        except KeyError as e:
            raise KeyError("TRACECAT__DB_ENCRYPTION_KEY is not set") from e

    def _serialize_value_bytes(self, value: Any) -> bytes:
        return orjson.dumps(
            value, default=to_jsonable_python, option=orjson.OPT_SORT_KEYS
        )

    def _deserialize_value_bytes(self, value: bytes) -> Any:
        return orjson.loads(value)

    def _system_keys(self) -> set[str]:
        """The set of keys that are reserved for system settings."""
        return {key for cls in self.groups for key in cls.keys()}

    async def init_default_settings(self):
        for cls in self.groups:
            for key, value in cls():
                if not await self.get_org_setting(key):
                    await self._create_org_setting(
                        SettingCreate(
                            key=key,
                            value=value,
                            is_sensitive=key in SENSITIVE_SETTINGS_KEYS,
                        )
                    )
                    self.logger.info("Created setting", key=key)
                else:
                    self.logger.info("Setting already exists", key=key)
        await self.session.commit()

    def get_value(self, setting: OrganizationSetting) -> Any:
        value_bytes = setting.value
        if setting.is_encrypted:
            value_bytes = decrypt_value(
                value_bytes, key=self._encryption_key.get_secret_value()
            )
        return self._deserialize_value_bytes(value_bytes)

    async def list_org_settings(
        self,
        *,
        keys: set[str] | None = None,
        value_type: str | None = None,
        is_encrypted: bool | None = None,
        limit: int | None = None,
        offset: int | None = None,
    ) -> Sequence[OrganizationSetting]:
        """List organization settings with optional filters.

        Args:
            keys: Filter settings by a set of specific keys
            value_type: Filter settings by their value type
            is_encrypted: Filter settings by their encryption status
            limit: Maximum number of settings to return
            offset: Number of settings to skip for pagination

        Returns:
            Sequence[OrganizationSetting]: List of matching organization settings
        """
        statement = select(OrganizationSetting)

        if keys is not None:
            statement = statement.where(col(OrganizationSetting.key).in_(keys))
        if value_type is not None:
            statement = statement.where(OrganizationSetting.value_type == value_type)
        if is_encrypted is not None:
            statement = statement.where(
                OrganizationSetting.is_encrypted == is_encrypted
            )

        if offset is not None:
            statement = statement.offset(offset)
        if limit is not None:
            statement = statement.limit(limit)

        result = await self.session.exec(statement)
        return result.all()

    async def get_org_setting(self, key: str) -> OrganizationSetting | None:
        """Get the current organization settings.

        Returns:
            Settings: The current organization settings configuration
        """
        if self.role is None and key not in PUBLIC_SETTINGS_KEYS:
            # Block access to private settings
            self.logger.warning("Blocked attempted access to private setting", key=key)
            return None

        statement = select(OrganizationSetting).where(OrganizationSetting.key == key)
        result = await self.session.exec(statement)
        return result.one_or_none()

    async def _create_org_setting(self, params: SettingCreate) -> OrganizationSetting:
        """Create a new organization setting."""

        # Convert to bytes
        value_bytes = self._serialize_value_bytes(params.value)
        # Then optionally encrypt
        if params.is_sensitive:
            value = encrypt_value(
                value_bytes, key=self._encryption_key.get_secret_value()
            )
        else:
            value = value_bytes
        setting = OrganizationSetting(
            owner_id=config.TRACECAT__DEFAULT_ORG_ID,
            key=params.key,
            value_type=params.value_type,
            value=value,
            is_encrypted=params.is_sensitive,
        )
        self.session.add(setting)
        return setting

    @require_access_level(AccessLevel.ADMIN)
    async def create_org_setting(self, params: SettingCreate) -> OrganizationSetting:
        """Create a new organization setting."""
        setting = await self._create_org_setting(params)
        await self.session.commit()
        return setting

    async def _update_setting(
        self, setting: OrganizationSetting, params: SettingUpdate
    ) -> OrganizationSetting:
        """Update a single organization setting but don't commit."""
        set_fields = params.model_dump(exclude_unset=True)

        # Handle value updates
        if "value" in set_fields:
            value_bytes = self._serialize_value_bytes(set_fields["value"])

            # Use existing encryption status
            if setting.is_encrypted:
                setting.value = encrypt_value(
                    value_bytes, key=self._encryption_key.get_secret_value()
                )
            else:
                setting.value = value_bytes

            set_fields.pop("value")

        # Update any remaining fields (only value_type at this point)
        for field, value in set_fields.items():
            setattr(setting, field, value)
        return setting

    @require_access_level(AccessLevel.ADMIN)
    async def update_org_setting(
        self, setting: OrganizationSetting, params: SettingUpdate
    ) -> OrganizationSetting:
        """Update the organization settings.

        Args:
            setting (OrganizationSetting): The existing setting to update
            params (SettingUpdate): The new setting parameters to apply

        Returns:
            OrganizationSetting: The updated settings configuration
        """
        updated_setting = await self._update_setting(setting, params)
        self.session.add(updated_setting)
        await self.session.commit()
        await self.session.refresh(updated_setting)
        return updated_setting

    @require_access_level(AccessLevel.ADMIN)
    async def delete_org_setting(self, setting: OrganizationSetting) -> None:
        """Delete an organization setting."""
        if setting.key in self._system_keys():
            self.logger.warning(
                "Cannot delete system setting", key=setting.key, setting=setting
            )
            return
        await self.session.delete(setting)
        await self.session.commit()

    # Grouped settings

    async def _update_grouped_settings(
        self, settings: Sequence[OrganizationSetting], params: BaseModel
    ) -> None:
        updated_fields = params.model_dump(exclude_unset=True)
        for setting in settings:
            if setting.key in updated_fields:
                params = SettingUpdate(value=updated_fields[setting.key])
                await self._update_setting(setting, params)
        await self.session.commit()

    @require_access_level(AccessLevel.ADMIN)
    async def update_git_settings(self, params: GitSettingsUpdate) -> None:
        self.logger.info(f"Updating Git settings: {params}")
        # Ignore read-only fields
        git_settings = await self.list_org_settings(keys=GitSettingsUpdate.keys())
        await self._update_grouped_settings(git_settings, params)

    @require_access_level(AccessLevel.ADMIN)
    async def update_saml_settings(self, params: SAMLSettingsUpdate) -> None:
        saml_settings = await self.list_org_settings(keys=SAMLSettingsUpdate.keys())
        await self._update_grouped_settings(saml_settings, params)

    @require_access_level(AccessLevel.ADMIN)
    async def update_auth_settings(self, params: AuthSettingsUpdate) -> None:
        auth_settings = await self.list_org_settings(keys=AuthSettingsUpdate.keys())
        await self._update_grouped_settings(auth_settings, params)

    @require_access_level(AccessLevel.ADMIN)
    async def update_oauth_settings(self, params: OAuthSettingsUpdate) -> None:
        oauth_settings = await self.list_org_settings(keys=OAuthSettingsUpdate.keys())
        await self._update_grouped_settings(oauth_settings, params)


async def get_setting(
    key: str,
    *,
    role: Role | None = None,
    session: AsyncSession | None = None,
    default: Any | None = None,
) -> Any | None:
    """Shorthand to get a setting value from the database."""
    role = role or ctx_role.get()

    # If we have an environment override, use it
    if override_val := get_setting_override(key):
        logger.warning(
            "Using environment override for setting. "
            "This is not recommended for production environments.",
            key=key,
            override=override_val,
        )
        match override_val.lower():
            case "true" | "1":
                return True
            case "false" | "0":
                return False
            case _:
                return override_val

    if session:
        service = SettingsService(session=session, role=role)
        setting = await service.get_org_setting(key)
        no_default_val = service.get_value(setting) if setting else None

    else:
        async with SettingsService.with_session(role=role) as service:
            setting = await service.get_org_setting(key)
            no_default_val = service.get_value(setting) if setting else None

    if no_default_val is None and default:
        logger.warning("Setting not found, using default value", key=key)
        return default
    return no_default_val


@alru_cache(ttl=30)
async def get_setting_cached(
    key: str,
    *,
    role: Role | None = None,
    session: AsyncSession | None = None,
    default: Any | None = None,
) -> Any | None:
    """Cached version of get_setting function.

    Args:
        key: The setting key to retrieve
        role: Optional role to use for permissions check
        session: Optional database session to use
        default: Optional default value if setting not found. Must be hashable.

    Returns:
        The setting value or None if not found
    """
    logger.debug("Cache miss", key=key)
    return await get_setting(key, role=role, session=session, default=default)


def get_setting_override(key: str) -> Any | None:
    """Get an environment override for a setting."""
    # Only allow overrides for specific settings
    allowed_override_keys = {
        "saml_enabled",
        "oauth_google_enabled",
        "auth_basic_enabled",
    }

    if key not in allowed_override_keys:
        logger.warning(f"Attempted override of unauthorized setting: {key}")
        return None

    return os.environ.get(f"TRACECAT__SETTING_OVERRIDE_{key.upper()}")
