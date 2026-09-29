"""Workspace install state for the platform skill library."""

from __future__ import annotations

import base64
from collections.abc import Iterable, Sequence
from enum import StrEnum

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert

from tracecat.agent.skill.library.catalog import get_library_skills, load_library
from tracecat.agent.skill.library.schemas import LibrarySkillRead
from tracecat.agent.skill.library.types import LibrarySkill
from tracecat.agent.skill.schemas import SkillRead, SkillUpload, SkillUploadFile
from tracecat.agent.skill.service import SkillService
from tracecat.authz.controls import require_scope
from tracecat.db.models import AgentPreset, SkillLibraryInstall
from tracecat.exceptions import TracecatNotFoundError, TracecatValidationError
from tracecat.pagination import (
    BaseCursorPaginator,
    CursorPaginatedResponse,
    CursorPaginationParams,
)
from tracecat.service import BaseWorkspaceService, requires_entitlement
from tracecat.tiers.enums import Entitlement


def normalize_library_slugs(slugs: Iterable[str] | None) -> list[str]:
    """Return library slugs deduplicated and sorted, the canonical saved form."""
    return sorted(set(slugs or []))


class SkillLibraryErrorCode(StrEnum):
    """Machine-readable skill library error codes."""

    NOT_INSTALLED = "library_skill_not_installed"
    IN_USE = "library_skill_in_use"


class SkillLibraryService(BaseWorkspaceService):
    """List, install, uninstall, and fork platform library skills."""

    service_name = "skill_library"

    def _get_skill(self, slug: str) -> LibrarySkill:
        if (skill := load_library().get(slug)) is None:
            raise TracecatNotFoundError(f"Library skill '{slug}' not found")
        return skill

    async def _installed(self, slugs: Sequence[str], *, lock: bool = False) -> set[str]:
        stmt = select(SkillLibraryInstall.library_slug).where(
            SkillLibraryInstall.workspace_id == self.workspace_id,
            SkillLibraryInstall.library_slug.in_(slugs),
        )
        if lock:
            # FOR SHARE pairs with uninstall's FOR UPDATE: a racing uninstall
            # either sees this bind committed or this read sees no row.
            stmt = stmt.order_by(SkillLibraryInstall.library_slug).with_for_update(
                read=True
            )
        return set((await self.session.execute(stmt)).scalars())

    @require_scope("agent:read")
    async def list_skills(
        self, params: CursorPaginationParams
    ) -> CursorPaginatedResponse[LibrarySkillRead]:
        """List library skills by slug with this workspace's install state."""

        skills = sorted(load_library().values(), key=lambda skill: skill.slug)
        cursor = (
            BaseCursorPaginator.decode_cursor(params.cursor).id
            if params.cursor
            else None
        )
        if params.reverse:
            window = [s for s in skills if cursor is None or s.slug < cursor]
            page = window[-params.limit :]
            has_before, has_after = len(window) > len(page), cursor is not None
        else:
            window = [s for s in skills if cursor is None or s.slug > cursor]
            page = window[: params.limit]
            has_before, has_after = cursor is not None, len(window) > len(page)
        installed = await self._installed([skill.slug for skill in page])
        return CursorPaginatedResponse(
            items=[
                LibrarySkillRead(
                    slug=skill.slug,
                    description=skill.description,
                    installed=skill.slug in installed,
                )
                for skill in page
            ],
            next_cursor=(
                BaseCursorPaginator.encode_cursor(page[-1].slug)
                if page and has_after
                else None
            ),
            prev_cursor=(
                BaseCursorPaginator.encode_cursor(page[0].slug)
                if page and has_before
                else None
            ),
            has_more=has_after,
            has_previous=has_before,
        )

    @require_scope("agent:create")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def install(self, slug: str) -> LibrarySkillRead:
        """Opt this workspace into a library skill. Idempotent."""

        skill = self._get_skill(slug)
        await self.install_many([slug])
        await self.session.commit()
        return LibrarySkillRead(
            slug=skill.slug, description=skill.description, installed=True
        )

    @require_scope("agent:create")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def install_many(self, slugs: Sequence[str]) -> list[str]:
        """Install known skills without committing the caller's transaction.

        Returns the sorted slugs newly installed by this transaction.
        """
        normalized = normalize_library_slugs(slugs)
        get_library_skills(normalized)
        if not normalized:
            return []
        result = await self.session.execute(
            insert(SkillLibraryInstall)
            .values(
                [
                    {"workspace_id": self.workspace_id, "library_slug": slug}
                    for slug in normalized
                ]
            )
            .on_conflict_do_nothing(
                constraint="uq_skill_library_install_workspace_slug"
            )
            .returning(SkillLibraryInstall.library_slug)
        )
        return sorted(result.scalars())

    @require_scope("agent:delete")
    async def uninstall(self, slug: str) -> None:
        """Remove a library skill unless a preset head still binds it."""

        install_id = await self.session.scalar(
            select(SkillLibraryInstall.id)
            .where(
                SkillLibraryInstall.workspace_id == self.workspace_id,
                SkillLibraryInstall.library_slug == slug,
            )
            .with_for_update()
        )
        if install_id is None:
            raise TracecatNotFoundError(f"Library skill '{slug}' is not installed")
        bound = (
            await self.session.execute(
                select(AgentPreset.slug).where(
                    AgentPreset.workspace_id == self.workspace_id,
                    AgentPreset.deleted_at.is_(None),
                    AgentPreset.library_skills.contains([slug]),
                )
            )
        ).scalars()
        if presets := sorted(bound):
            raise TracecatValidationError(
                f"Library skill '{slug}' is used by presets: {presets}",
                detail={"code": SkillLibraryErrorCode.IN_USE.value, "presets": presets},
            )
        await self.session.execute(
            delete(SkillLibraryInstall).where(SkillLibraryInstall.id == install_id)
        )
        await self.session.commit()

    @require_scope("agent:create")
    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def fork(self, slug: str) -> SkillRead:
        """Copy a library skill into a new, editable workspace skill draft."""

        markdown = self._get_skill(slug).markdown
        files = [
            SkillUploadFile(
                path="SKILL.md", content_base64=base64.b64encode(markdown).decode()
            )
        ]
        return await SkillService(self.session, role=self.role).upload_skill(
            SkillUpload(name=slug, files=files)
        )

    async def validated_bindings(self, slugs: Sequence[str] | None) -> list[str] | None:
        """Normalize a binding selection and lock-validate it; empty becomes None."""
        normalized = normalize_library_slugs(slugs)
        if not normalized:
            return None
        await self.validate_bindable(normalized)
        return normalized

    async def missing_installs(self, slugs: Sequence[str]) -> list[str]:
        """Return slugs that are unknown or not installed here, without locking.

        For previews only; writes must go through ``validate_bindable``.
        """
        normalized = normalize_library_slugs(slugs)
        if not normalized:
            return []
        # Install rows only exist for catalog slugs, so unknown slugs are missing.
        return sorted(set(normalized) - await self._installed(normalized))

    @requires_entitlement(Entitlement.AGENT_ADDONS)
    async def validate_bindable(self, slugs: Sequence[str]) -> None:
        """Require every slug to exist in the library and be installed here.

        Locks the install rows for the caller's transaction, so a concurrent
        uninstall cannot slip past the binding it is about to write.

        Raises:
            TracecatValidationError: If a slug is unknown or not installed.
        """

        get_library_skills(slugs)
        installed = await self._installed(slugs, lock=True)
        if missing := sorted(set(slugs) - installed):
            raise TracecatValidationError(
                f"Library skills are not installed in this workspace: {missing}",
                detail={
                    "code": SkillLibraryErrorCode.NOT_INSTALLED.value,
                    "slugs": missing,
                },
            )
