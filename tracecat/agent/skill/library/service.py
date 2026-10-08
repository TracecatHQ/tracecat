"""Workspace install state for the platform skill library."""

from __future__ import annotations

import base64
from collections.abc import Iterable, Sequence
from enum import StrEnum

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import array, insert

from tracecat.agent.skill.dependencies import SkillToolDependencyService
from tracecat.agent.skill.library.catalog import get_library_skills, load_library
from tracecat.agent.skill.library.schemas import (
    LibrarySkillDetailRead,
    LibrarySkillFileRead,
    LibrarySkillRead,
    LibrarySkillSourceRead,
)
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
from tracecat.service import BaseWorkspaceService


def normalize_library_slugs(slugs: Iterable[str] | None) -> list[str]:
    """Return library slugs deduplicated and sorted, the canonical saved form."""
    return sorted(set(slugs or []))


def _skill_read(skill: LibrarySkill, *, installed: bool) -> LibrarySkillRead:
    source = skill.source
    return LibrarySkillRead(
        slug=skill.slug,
        description=skill.description,
        installed=installed,
        source=LibrarySkillSourceRead(
            group=source.group,
            group_summary=source.group_summary,
            group_description=source.group_description,
            summary=source.summary,
            provider=source.provider,
            repo=source.repo,
            commit=source.commit,
            license=source.license,
            url=source.url,
            kind=source.kind,
        )
        if source
        else None,
    )


def _file_read(path: str, data: bytes) -> LibrarySkillFileRead:
    try:
        content = data.decode("utf-8")
    except UnicodeDecodeError:
        content = None
    return LibrarySkillFileRead(path=path, size_bytes=len(data), content=content)


class SkillLibraryErrorCode(StrEnum):
    """Machine-readable skill library error codes."""

    NOT_INSTALLED = "library_skill_not_installed"
    IN_USE = "library_skill_in_use"
    TOOLS_UNAVAILABLE = "library_skill_tools_unavailable"


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
            has_before = len(window) > len(page)
            # Forged or stale cursors may sit past the last entry.
            has_after = (
                cursor is not None and bool(skills) and skills[-1].slug >= cursor
            )
        else:
            window = [s for s in skills if cursor is None or s.slug > cursor]
            page = window[: params.limit]
            has_before = (
                cursor is not None and bool(skills) and skills[0].slug <= cursor
            )
            has_after = len(window) > len(page)
        installed = await self._installed([skill.slug for skill in page])
        return CursorPaginatedResponse(
            items=[
                _skill_read(skill, installed=skill.slug in installed) for skill in page
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

    @require_scope("agent:read")
    async def get_skill(self, slug: str) -> LibrarySkillDetailRead:
        """Return a library skill's files and this workspace's install state."""

        skill = self._get_skill(slug)
        summary = _skill_read(skill, installed=slug in await self._installed([slug]))
        return LibrarySkillDetailRead(
            **summary.model_dump(),
            files=[
                _file_read(path, data) for path, data in sorted(skill.files.items())
            ],
        )

    @require_scope("agent:create")
    async def install(self, slug: str) -> LibrarySkillRead:
        """Opt this workspace into a library skill. Idempotent."""

        return (await self.batch_install([slug]))[0]

    @require_scope("agent:create")
    async def batch_install(self, slugs: Sequence[str]) -> list[LibrarySkillRead]:
        """Install every selected library skill in one transaction. Idempotent."""

        skills = [self._get_skill(slug) for slug in normalize_library_slugs(slugs)]
        await self.install_many([skill.slug for skill in skills])
        await self.session.commit()
        return [_skill_read(skill, installed=True) for skill in skills]

    @require_scope("agent:create")
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

        await self.uninstall_many([slug])

    @require_scope("agent:delete")
    async def uninstall_many(self, slugs: Sequence[str]) -> None:
        """Remove all selected installs, or reject the batch without any deletion.

        Exclusive install locks pair with binding validation's shared locks.
        Acquire them in slug order, then check every binding before deleting.
        """
        normalized = normalize_library_slugs(slugs)
        if not normalized:
            return
        installed = set(
            (
                await self.session.scalars(
                    select(SkillLibraryInstall.library_slug)
                    .where(
                        SkillLibraryInstall.workspace_id == self.workspace_id,
                        SkillLibraryInstall.library_slug.in_(normalized),
                    )
                    .order_by(SkillLibraryInstall.library_slug)
                    .with_for_update()
                )
            ).all()
        )
        if missing := sorted(set(normalized) - installed):
            raise TracecatNotFoundError(
                f"Library skills are not installed: {', '.join(missing)}"
            )
        bound_stmt = (
            select(AgentPreset.slug, AgentPreset.name)
            .where(
                AgentPreset.workspace_id == self.workspace_id,
                AgentPreset.deleted_at.is_(None),
                AgentPreset.library_skills.has_any(array(normalized)),
            )
            .order_by(AgentPreset.name)
        )
        bound = (await self.session.execute(bound_stmt)).tuples().all()
        if bound:
            noun = "agent" if len(bound) == 1 else "agents"
            names = ", ".join(f"'{name}'" for _, name in bound)
            raise TracecatValidationError(
                f"Remove the selected skills from {noun} {names} before uninstalling. "
                "No skills were uninstalled.",
                detail={
                    "code": SkillLibraryErrorCode.IN_USE.value,
                    "slugs": normalized,
                    "presets": sorted(preset_slug for preset_slug, _ in bound),
                },
            )
        await self.session.execute(
            delete(SkillLibraryInstall).where(
                SkillLibraryInstall.workspace_id == self.workspace_id,
                SkillLibraryInstall.library_slug.in_(normalized),
            )
        )
        await self.session.commit()

    @require_scope("agent:create")
    async def fork(self, slug: str) -> SkillRead:
        """Copy a library skill into a new, editable workspace skill draft."""

        files = [
            SkillUploadFile(
                path=path, content_base64=base64.b64encode(content).decode()
            )
            for path, content in self._get_skill(slug).files.items()
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

    async def validate_declared_tools(self, slugs: Sequence[str]) -> None:
        """Require every registry tool the entries declare to exist here.

        Raises:
            TracecatValidationError: If a slug is unknown or a tool is missing.
        """

        tool_ids = [
            tool for skill in get_library_skills(slugs) for tool in skill.declared_tools
        ]
        if missing := await SkillToolDependencyService(
            self.session, role=self.role
        ).missing_registry_tools(tool_ids):
            raise TracecatValidationError(
                f"Library skills need tools that are not available: {', '.join(missing)}",
                detail={
                    "code": SkillLibraryErrorCode.TOOLS_UNAVAILABLE.value,
                    "tool_ids": missing,
                },
            )

    async def validate_bindable(self, slugs: Sequence[str]) -> None:
        """Require every slug to exist in the library and be installed here.

        Locks the install rows for the caller's transaction, so a concurrent
        uninstall cannot slip past the binding it is about to write.

        Raises:
            TracecatValidationError: If a slug is unknown or not installed.
        """

        await self.validate_declared_tools(slugs)
        installed = await self._installed(slugs, lock=True)
        if missing := sorted(set(slugs) - installed):
            raise TracecatValidationError(
                f"Install {', '.join(missing)} from the skill library first.",
                detail={
                    "code": SkillLibraryErrorCode.NOT_INSTALLED.value,
                    "slugs": missing,
                },
            )
