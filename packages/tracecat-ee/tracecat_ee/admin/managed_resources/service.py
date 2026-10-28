"""Platform admin service for managed resources."""

from __future__ import annotations

from tracecat.managed_resources.source import source_sync_service
from tracecat.pagination import Page, PageParams
from tracecat.registry.repositories.schemas import GitTagInfo
from tracecat.service import BasePlatformService


class AdminManagedResourcesService(BasePlatformService):
    """Read the managed resources source repository for platform admins."""

    service_name = "admin_managed_resources"

    async def list_source_tags(self, page: PageParams) -> Page[GitTagInfo]:
        """List one page of source repository tags, newest first.

        Raises:
            ManagedSourceNotConfiguredError: No source workspace is configured.
            ManagedSourceInvalidError: The configured source can no longer be used.
            TracecatValidationError: The cursor is malformed.
            VcsProviderError: The provider rejected or failed the request.
        """
        sync_service = await source_sync_service(self.session, self.role)
        tags, next_cursor = await sync_service.list_tags(
            limit=page.limit, cursor=page.cursor
        )
        return Page(items=tags, next_cursor=next_cursor)
