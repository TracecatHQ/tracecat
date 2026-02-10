import os
from collections.abc import Sequence
from typing import Any

import orjson
from async_lru import alru_cache
from cryptography.fernet import InvalidToken
from pydantic import BaseModel, SecretStr
from pydantic_core import to_jsonable_python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.audit.logger import audit_log
from tracecat.auth.types import Role
from tracecat.common import UNSET
from tracecat.contexts import ctx_role, ctx_session
from tracecat.db.models import OrganizationSetting
from tracecat.identifiers import OrganizationID
from tracecat.logger import logger
from tracecat.secrets.encryption import decrypt_value, encrypt_value
from tracecat.service import BaseOrgService
from tracecat.settings.constants import SENSITIVE_SETTINGS_KEYS
from tracecat.settings.schemas import (
    AgentSettingsUpdate,
    AppSettingsUpdate,
    AuditSettingsUpdate,
    BaseSettingsGroup,
    GitSettingsUpdate,
    SAMLSettingsUpdate,
    SettingCreate,
    SettingUpdate,
)


class SettingsService(BaseOrgService):
    """Service for managing organization settings.

    Requires a role with organization_id (enforced by BaseOrgService).
    """

    service_name = "settings"
    groups: list[type[BaseSettingsGroup]] = [
        AgentSettingsUpdate,
        GitSettingsUpdate,
        SAMLSettingsUpdate,
        AppSettingsUpdate,
        AuditSettingsUpdate,
    ]
    """The set of settings groups that are managed by the service."""

    def __init__(self, session: AsyncSession, role: Role | None = None):
        super().__init__(session, role=role)
        encryption_key = config.TRACECAT__DB_ENCRYPTION_KEY
        if not encryption_key:
            raise KeyError("TRACECAT__DB_ENCRYPTION_KEY is not set")
        self._encryption_key = SecretStr(encryption_key)

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
                    self.logger.debug("Created setting", key=key)
                else:
                    self.logger.debug("Setting already exists", key=key)
        await self.session.commit()

    def get_value(self, setting: OrganizationSetting) -> Any:
        value_bytes = setting.value
        if setting.is_encrypted:
            value_bytes = decrypt_value(
                value_bytes, key=self._encryption_key.get_secret_value()
            )
        return self._deserialize_value_bytes(value_bytes)

    def get_values_with_decryption_fallback(
        self,
        settings: Sequence[OrganizationSetting],
    ) -> tuple[dict[str, Any], list[str]]:
        """Deserialize settings while tolerating encrypted decrypt failures.

        If an encrypted setting cannot be decrypted, return `None` for that key
        and include the key in `decryption_failed_keys` so callers can prompt for
        reconfiguration instead of failing the entire response.
        """
        values: dict[str, Any] = {}
        decryption_failed_keys: list[str] = []
        for setting in settings:
            try:
                values[setting.key] = self.get_value(setting)
            except (InvalidToken, ValueError) as e:
                if not setting.is_encrypted:
                    raise
                values[setting.key] = None
                decryption_failed_keys.append(setting.key)
                self.logger.warning(
                    "Failed to decrypt org setting; returning null and marking for reconfiguration",
                    key=setting.key,
                    error=str(e),
                )
        return values, decryption_failed_keys

    async def list_org_settings(
        self,
        *,
        keys: set[str] | None = None,
        value_type: str | None = None,
        is_encrypted: bool | None = None,
        limit: int | None = None,
    ) -> Sequence[OrganizationSetting]:
        """List organization settings with optional filters.

        Args:
            keys: Filter settings by a set of specific keys
            value_type: Filter settings by their value type
            is_encrypted: Filter settings by their encryption status
            limit: Maximum number of settings to return

        Returns:
            Sequence[OrganizationSetting]: List of matching organization settings
        """
        statement = select(OrganizationSetting).where(
            OrganizationSetting.organization_id == self.organization_id
        )

        if keys is not None:
            statement = statement.where(OrganizationSetting.key.in_(keys))
        if value_type is not None:
            statement = statement.where(OrganizationSetting.value_type == value_type)
        if is_encrypted is not None:
            statement = statement.where(
                OrganizationSetting.is_encrypted == is_encrypted
            )

        if limit is not None:
            statement = statement.limit(limit)

        result = await self.session.execute(statement)
        return result.scalars().all()

    async def get_org_setting(self, key: str) -> OrganizationSetting | None:
        """Get the current organization settings.

        Returns:
            Settings: The current organization settings configuration
        """
        statement = select(OrganizationSetting).where(
            OrganizationSetting.organization_id == self.organization_id,
            OrganizationSetting.key == key,
        )
        result = await self.session.execute(statement)
        return result.scalar_one_or_none()

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
            organization_id=self.organization_id,
            key=params.key,
            value_type=params.value_type,
            value=value,
            is_encrypted=params.is_sensitive,
        )
        self.session.add(setting)
        return setting

    @audit_log(resource_type="organization_setting", action="create")
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

    @audit_log(resource_type="organization_setting", action="update")
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

    @audit_log(resource_type="organization_setting", action="delete")
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
        settings_by_key = {setting.key: setting for setting in settings}
        for key, value in updated_fields.items():
            setting = settings_by_key.get(key)
            if setting is None:
                setting = await self._create_org_setting(
                    SettingCreate(
                        key=key,
                        value=value,
                        is_sensitive=key in SENSITIVE_SETTINGS_KEYS,
                    )
                )
                settings_by_key[key] = setting
            else:
                params = SettingUpdate(value=value)
                await self._update_setting(setting, params)
        await self.session.commit()

    @audit_log(resource_type="organization_setting", action="update")
    async def update_git_settings(self, params: GitSettingsUpdate) -> None:
        self.logger.info(f"Updating Git settings: {params}")
        # Ignore read-only fields
        git_settings = await self.list_org_settings(keys=GitSettingsUpdate.keys())
        await self._update_grouped_settings(git_settings, params)

    @audit_log(resource_type="organization_setting", action="update")
    async def update_saml_settings(self, params: SAMLSettingsUpdate) -> None:
        saml_settings = await self.list_org_settings(keys=SAMLSettingsUpdate.keys())
        await self._update_grouped_settings(saml_settings, params)

    @audit_log(resource_type="organization_setting", action="update")
    async def update_audit_settings(self, params: AuditSettingsUpdate) -> None:
        audit_settings = await self.list_org_settings(keys=AuditSettingsUpdate.keys())
        await self._update_grouped_settings(audit_settings, params)

    @audit_log(resource_type="organization_setting", action="update")
    async def update_app_settings(self, params: AppSettingsUpdate) -> None:
        app_settings = await self.list_org_settings(keys=AppSettingsUpdate.keys())
        await self._update_grouped_settings(app_settings, params)

    @audit_log(resource_type="organization_setting", action="update")
    async def update_agent_settings(self, params: AgentSettingsUpdate) -> None:
        agent_settings = await self.list_org_settings(keys=AgentSettingsUpdate.keys())
        await self._update_grouped_settings(agent_settings, params)


async def get_setting(
    key: str,
    *,
    role: Role | None = None,
    session: AsyncSession | None = None,
    default: Any = UNSET,
) -> Any | None:
    """Shorthand to get a setting value from the database."""
    role = role or ctx_role.get()

    # If no role is available, return default or None
    if role is None:
        return default if default is not UNSET else None

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

    # If role has no organization_id, fetch the default org
    if role is not None and role.organization_id is None:
        from tracecat.api.common import get_default_organization_id
        from tracecat.auth.types import Role as RoleClass

        if session:
            default_org_id = await get_default_organization_id(session)
        else:
            from tracecat.db.engine import get_async_session_context_manager

            async with get_async_session_context_manager() as sess:
                default_org_id = await get_default_organization_id(sess)

        # If no default organization is available, return default
        if default_org_id is None:
            logger.debug(
                "No organization available for setting lookup, using default",
                key=key,
            )
            return default if default is not UNSET else None

        # Create a new role with the default org_id
        role = RoleClass(
            type=role.type,
            service_id=role.service_id,
            user_id=role.user_id,
            workspace_id=role.workspace_id,
            organization_id=default_org_id,
        )

    if session:
        service = SettingsService(session=session, role=role)
        setting = await service.get_org_setting(key)
        no_default_val = service.get_value(setting) if setting else None

    else:
        async with SettingsService.with_session(role=role) as service:
            setting = await service.get_org_setting(key)
            no_default_val = service.get_value(setting) if setting else None

    if no_default_val is None and default is not UNSET:
        logger.debug("Setting not found, using default value", key=key)
        return default
    return no_default_val


async def get_setting_cached(
    key: str,
    *,
    default: Any | None = None,
) -> Any | None:
    """Cached version of get_setting function.

    Cache is keyed by (key, organization_id) to prevent cross-tenant data leakage.
    Uses context role and session - use get_setting() for explicit role/session control.

    Args:
        key: The setting key to retrieve
        default: Optional default value if setting not found. Must be hashable.

    Returns:
        The setting value or None if not found
    """
    # Resolve organization_id from context for cache key
    role = ctx_role.get()
    organization_id = role.organization_id if role else None

    return await _get_setting_cached_by_org(key, organization_id, default)


@alru_cache(ttl=30)
async def _get_setting_cached_by_org(
    key: str,
    organization_id: OrganizationID | None,
    default: Any | None = None,
) -> Any | None:
    """Internal cached implementation keyed by (key, organization_id).

    This ensures different organizations have isolated cache entries.
    """
    logger.debug("Cache miss", key=key, organization_id=organization_id)
    role = ctx_role.get()
    sess = ctx_session.get(None)
    return await get_setting(key, role=role, session=sess, default=default)


def get_setting_override(key: str) -> Any | None:
    """Get an environment override for a setting."""
    # Only allow overrides for specific settings
    allowed_override_keys = {
        "saml_enabled",
    }

    if key not in allowed_override_keys:
        logger.debug(f"Setting override not supported: {key}")
        return None

    return os.environ.get(f"TRACECAT__SETTING_OVERRIDE_{key.upper()}")
