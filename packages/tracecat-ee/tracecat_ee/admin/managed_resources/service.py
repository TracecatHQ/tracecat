"""Platform admin service for managed resources."""

from __future__ import annotations

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete, func
from sqlalchemy.dialects.postgresql import insert as pg_insert

from tracecat.audit.logger import audit_log
from tracecat.db.models import PlatformSetting
from tracecat.managed_resources.source import (
    MANAGED_RESOURCES_SOURCE_KEY,
    ManagedSource,
    ManagedSourceSetting,
    get_managed_source,
    load_managed_source,
    source_sync_service,
)
from tracecat.pagination import Page, PageParams
from tracecat.registry.repositories.schemas import GitTagInfo
from tracecat.service import BasePlatformService
from tracecat_ee.admin.managed_resources.schemas import (
    ManagedSourceRead,
    ManagedSourceSettingRead,
    ManagedSourceUpdate,
)


class AdminManagedResourcesService(BasePlatformService):
    """Configure and read the managed resources source for platform admins."""

    service_name = "admin_managed_resources"

    async def get_source(self) -> ManagedSourceSettingRead:
        """Get the managed resources source, if one is set.

        Raises:
            ManagedSourceUnavailableError: The configured source can no longer
                be used.
        """
        source = await get_managed_source(self.session)
        return ManagedSourceSettingRead(
            source=_source_read(source) if source is not None else None
        )

    @audit_log(
        resource_type="platform_setting",
        action="update",
        resource_id_attr="workspace_id",
    )
    async def set_source(self, params: ManagedSourceUpdate) -> ManagedSourceRead:
        """Set the managed resources source and pin its current Git binding.

        The audit events record the source workspace ID, read from ``params``
        before the call and from the result after it.

        Raises:
            TracecatNotFoundError: The workspace does not exist.
            TracecatValidationError: The workspace has no Git sync binding.
        """
        source = await load_managed_source(self.session, params.workspace_id)
        value = ManagedSourceSetting.pin(source).model_dump_json().encode()
        # Not AdminSettingsService._upsert_setting: that is a select-then-write
        # and handles encryption. This value is not sensitive, so it is stored
        # unencrypted with one atomic Postgres upsert instead.
        stmt = pg_insert(PlatformSetting).values(
            key=MANAGED_RESOURCES_SOURCE_KEY,
            value=value,
            value_type="json",
            is_encrypted=False,
        )
        await self.session.execute(
            stmt.on_conflict_do_update(
                index_elements=[PlatformSetting.key],
                set_={
                    "value": stmt.excluded.value,
                    "is_encrypted": False,
                    "updated_at": func.now(),
                },
            )
        )
        await self.session.commit()
        return _source_read(source)

    @audit_log(
        resource_type="platform_setting",
        action="delete",
        resource_id_attr="workspace_id",
    )
    async def clear_source(self) -> ManagedSourceSetting | None:
        """Remove the managed resources source setting, if any.

        Returns the removed pinned setting so the audit success event records
        the previous source workspace ID, or ``None`` when nothing was set or
        the stored value was malformed.
        """
        raw = await self.session.scalar(
            delete(PlatformSetting)
            .where(PlatformSetting.key == MANAGED_RESOURCES_SOURCE_KEY)
            .returning(PlatformSetting.value)
        )
        await self.session.commit()
        if raw is None:
            return None
        try:
            return ManagedSourceSetting.model_validate_json(raw)
        except PydanticValidationError:
            return None

    async def list_source_tags(self, page: PageParams) -> Page[GitTagInfo]:
        """List one page of source repository tags, newest first.

        Raises:
            ManagedSourceUnavailableError: No usable source is configured.
            TracecatValidationError: The cursor is malformed.
            VcsProviderError: The provider rejected or failed the request.
        """
        sync_service = await source_sync_service(self.session, self.role)
        tags, next_cursor = await sync_service.list_tags(
            limit=page.limit, cursor=page.cursor
        )
        return Page(items=tags, next_cursor=next_cursor)


def _source_read(source: ManagedSource) -> ManagedSourceRead:
    return ManagedSourceRead.model_validate(source, from_attributes=True)
