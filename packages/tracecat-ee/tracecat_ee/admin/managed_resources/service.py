"""Platform admin service for managed resources."""

from __future__ import annotations

import uuid

from pydantic import ValidationError as PydanticValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.audit.logger import audit_log
from tracecat.audit.types import AuditAction, AuditMetadata, AuditResourceType
from tracecat.auth.types import PlatformRole
from tracecat.db.models import (
    ManagedReferenceMatch,
    ManagedRollout,
    ManagedTarget,
    Organization,
    PlatformSetting,
    Workspace,
)
from tracecat.exceptions import (
    TracecatConflictError,
    TracecatNotFoundError,
    TracecatValidationError,
)
from tracecat.logger import logger
from tracecat.managed_resources.audit import audit_managed_event
from tracecat.managed_resources.enums import (
    ManagedReferenceKind,
    ManagedRolloutKind,
    ManagedRolloutStatus,
)
from tracecat.managed_resources.schemas import ManagedRolloutRead, RolloutPreview
from tracecat.managed_resources.source import (
    MANAGED_RESOURCES_SOURCE_KEY,
    ManagedSource,
    ManagedSourceSetting,
    cut_source_release,
    get_managed_source,
    list_source_tags,
    load_managed_source,
    pin_source,
    require_managed_source,
    resolve_source_tag,
    source_release_candidate,
)
from tracecat.managed_resources.source_setting import managed_source_workspace_id
from tracecat.managed_resources.workflows import (
    RolloutPhase,
    RolloutRunner,
    TemporalRolloutRunner,
)
from tracecat.pagination import Page, PageParams, paginate
from tracecat.registry.repositories.schemas import GitTagInfo
from tracecat.service import BasePlatformService
from tracecat.workspace_sync.transport import VcsTransportFactory
from tracecat_ee.admin.managed_resources.schemas import (
    ManagedReferenceMatchesUpdate,
    ManagedReferenceMatchItem,
    ManagedReleaseCandidateRead,
    ManagedReleaseCreate,
    ManagedRolloutApplyRequest,
    ManagedRolloutPreviewRequest,
    ManagedSourceRead,
    ManagedSourceSettingRead,
    ManagedSourceUpdate,
    ManagedTargetCreate,
    ManagedTargetRead,
    ManagedTargetUpdate,
    ManagedWorkspaceRead,
)


class AdminManagedResourcesService(BasePlatformService):
    """Configure the managed resources source, targets and rollouts.

    Only platform superusers reach it, on an RLS-bypass session, because a
    rollout reads one organization's repository and writes into others.
    """

    service_name = "admin_managed_resources"

    def __init__(
        self,
        session: AsyncSession,
        role: PlatformRole,
        *,
        runner: RolloutRunner | None = None,
        transport_factory: VcsTransportFactory | None = None,
    ) -> None:
        super().__init__(session, role)
        self._runner = runner or TemporalRolloutRunner()
        self._transport_factory = transport_factory

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
        value = pin_source(source).model_dump_json().encode()
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
        tags, next_cursor = await list_source_tags(
            self.session, self.role, limit=page.limit, cursor=page.cursor
        )
        return Page(items=tags, next_cursor=next_cursor)

    # -- targets ----------------------------------------------------------
    async def list_workspaces(
        self, page: PageParams, *, organization_id: uuid.UUID | None = None
    ) -> Page[ManagedWorkspaceRead]:
        """List workspaces across organizations, marking targets and the source."""
        source_id = await managed_source_workspace_id(self.session)
        stmt = (
            select(
                Workspace.id,
                Workspace.name,
                Organization.id,
                Organization.name,
                ManagedTarget.id,
            )
            .join(Organization, Organization.id == Workspace.organization_id)
            .outerjoin(ManagedTarget, ManagedTarget.workspace_id == Workspace.id)
        )
        if organization_id is not None:
            stmt = stmt.where(Workspace.organization_id == organization_id)
        return await paginate(
            self.session,
            stmt,
            page=page,
            order_by=(
                Organization.name.asc(),
                Workspace.name.asc(),
                Workspace.id.asc(),
            ),
            row_factory=lambda row: ManagedWorkspaceRead.model_validate(
                {
                    "workspace_id": row[0],
                    "workspace_name": row[1],
                    "organization_id": row[2],
                    "organization_name": row[3],
                    "is_source": row[0] == source_id,
                    "target_id": row[4],
                }
            ),
        )

    async def list_targets(self, page: PageParams) -> Page[ManagedTargetRead]:
        """List every target with its organization, pin and last rollout."""
        stmt = (
            select(ManagedTarget, Workspace.name, Organization.id, Organization.name)
            .join(Workspace, Workspace.id == ManagedTarget.workspace_id)
            .join(Organization, Organization.id == Workspace.organization_id)
        )
        return await paginate(
            self.session,
            stmt,
            page=page,
            order_by=(
                Organization.name.asc(),
                Workspace.name.asc(),
                ManagedTarget.id.asc(),
            ),
            row_factory=_target_row,
        )

    async def assign_target(self, params: ManagedTargetCreate) -> ManagedTargetRead:
        """Assign a workspace as a target pinned to a tag. Nothing is applied yet.

        Raises:
            TracecatNotFoundError: The workspace or the tag does not exist.
            TracecatValidationError: The workspace is the source.
            TracecatConflictError: The workspace is already a target.
        """
        source = await require_managed_source(self.session)
        if params.workspace_id == source.workspace_id:
            raise TracecatValidationError("The source workspace cannot be a target")
        workspace = await self.session.scalar(
            select(Workspace).where(Workspace.id == params.workspace_id)
        )
        if workspace is None:
            raise TracecatNotFoundError("Workspace not found")
        await self._require_tag(source, params.pinned_tag)
        existing = await self.session.scalar(
            select(ManagedTarget).where(ManagedTarget.workspace_id == workspace.id)
        )
        if existing is not None:
            raise TracecatConflictError("The workspace is already a target")
        target = ManagedTarget(
            workspace_id=workspace.id,
            pinned_tag=params.pinned_tag,
            assigned_by=self.role.user_id,
        )
        self.session.add(target)
        try:
            await self.session.commit()
        except IntegrityError as e:
            # Two concurrent assigns of the same workspace both passed the
            # check above; the unique constraint caught the second.
            await self.session.rollback()
            raise TracecatConflictError("The workspace is already a target") from e
        await self._audit(
            "managed_target",
            "create",
            target.id,
            workspace,
            data={"release_tag": params.pinned_tag},
        )
        return await self.get_target(target.id)

    async def update_target(
        self, target_id: uuid.UUID, params: ManagedTargetUpdate
    ) -> ManagedTargetRead:
        """Move a target's pin. A rollout applies it.

        Raises:
            TracecatNotFoundError: The target or the tag does not exist.
        """
        target = await self._target(target_id)
        source = await require_managed_source(self.session)
        await self._require_tag(source, params.pinned_tag)
        target.pinned_tag = params.pinned_tag
        await self.session.commit()
        workspace = await self.session.scalar(
            select(Workspace).where(Workspace.id == target.workspace_id)
        )
        await self._audit(
            "managed_target",
            "update",
            target.id,
            workspace,
            data={"release_tag": params.pinned_tag},
        )
        return await self.get_target(target.id)

    async def get_target(self, target_id: uuid.UUID) -> ManagedTargetRead:
        """Get one target.

        Raises:
            TracecatNotFoundError: The target does not exist.
        """
        row = (
            await self.session.execute(
                select(
                    ManagedTarget, Workspace.name, Organization.id, Organization.name
                )
                .join(Workspace, Workspace.id == ManagedTarget.workspace_id)
                .join(Organization, Organization.id == Workspace.organization_id)
                .where(ManagedTarget.id == target_id)
            )
        ).first()
        if row is None:
            raise TracecatNotFoundError("Target not found")
        return _target_row(tuple(row))

    # -- reference matches --------------------------------------------------
    async def list_references(
        self, target_id: uuid.UUID
    ) -> list[ManagedReferenceMatchItem]:
        """Return a target's saved model, MCP integration and store matches."""
        target = await self._target(target_id)
        matches = await self.session.scalars(
            select(ManagedReferenceMatch)
            .where(ManagedReferenceMatch.workspace_id == target.workspace_id)
            .order_by(
                ManagedReferenceMatch.reference_kind, ManagedReferenceMatch.source_key
            )
        )
        return [
            ManagedReferenceMatchItem(
                reference_kind=match.reference_kind,
                source_key=match.source_key,
                resolved_id=match.resolved_id,
            )
            for match in matches
        ]

    async def save_references(
        self, target_id: uuid.UUID, params: ManagedReferenceMatchesUpdate
    ) -> list[ManagedReferenceMatchItem]:
        """Replace the target's matches; later version bumps reuse them.

        Raises:
            TracecatValidationError: A model or MCP integration key is not a
                UUID.
        """
        target = await self._target(target_id)
        # Serialize replacements of the same target's matches until commit.
        await self.session.execute(
            select(ManagedTarget.id)
            .where(ManagedTarget.id == target.id)
            .with_for_update()
        )
        for item in params.matches:
            if item.reference_kind in _UUID_KEYED_KINDS:
                try:
                    uuid.UUID(item.source_key)
                except ValueError as e:
                    raise TracecatValidationError(
                        f"{item.reference_kind.value} key must be a UUID"
                    ) from e
        # Replace, not merge: a match the admin removed must stop applying.
        wanted = {(item.reference_kind, item.source_key) for item in params.matches}
        existing = (
            await self.session.execute(
                select(
                    ManagedReferenceMatch.id,
                    ManagedReferenceMatch.reference_kind,
                    ManagedReferenceMatch.source_key,
                ).where(ManagedReferenceMatch.workspace_id == target.workspace_id)
            )
        ).tuples()
        if omitted := [
            match_id
            for match_id, kind, source_key in existing
            if (kind, source_key) not in wanted
        ]:
            await self.session.execute(
                delete(ManagedReferenceMatch).where(
                    ManagedReferenceMatch.id.in_(omitted)
                )
            )
        for item in params.matches:
            stmt = pg_insert(ManagedReferenceMatch).values(
                workspace_id=target.workspace_id,
                reference_kind=item.reference_kind,
                source_key=item.source_key,
                resolved_id=item.resolved_id,
            )
            await self.session.execute(
                stmt.on_conflict_do_update(
                    constraint="uq_managed_reference_match_source",
                    set_={
                        "resolved_id": stmt.excluded.resolved_id,
                        "updated_at": func.now(),
                    },
                )
            )
        await self.session.commit()
        workspace = await self.session.scalar(
            select(Workspace).where(Workspace.id == target.workspace_id)
        )
        await self._audit(
            "managed_reference_match",
            "upsert",
            target.id,
            workspace,
            data={"match_count": len(params.matches)},
            customer=False,
        )
        return await self.list_references(target_id)

    # -- releases -----------------------------------------------------------
    async def cut_release(self, params: ManagedReleaseCreate) -> GitTagInfo:
        """Tag the source repository's base branch HEAD as a new release.

        Raises:
            TracecatValidationError: The name is not ``vX.Y.Z`` or is taken.
            VcsProviderError: The provider refused the request.
        """
        source = await require_managed_source(self.session)
        tag = await cut_source_release(
            self.session,
            source,
            params.tag,
            expected_commit_sha=params.commit_sha,
            transport_factory=self._transport_factory,
        )
        await audit_managed_event(
            self.session,
            actor=self.role,
            resource_type="managed_package_release",
            action="create",
            data={"release_tag": tag.name, "commit_id": tag.commit_sha},
        )
        return tag

    async def release_candidate(self) -> ManagedReleaseCandidateRead:
        """Describe what cutting a release now would tag.

        Raises:
            ManagedSourceUnavailableError: No usable source is configured.
            VcsProviderError: The provider refused the request.
        """
        source = await require_managed_source(self.session)
        candidate = await source_release_candidate(
            self.session, source, transport_factory=self._transport_factory
        )
        return ManagedReleaseCandidateRead(
            branch=candidate.branch,
            commit_sha=candidate.commit.sha,
            commit_message=candidate.commit.message,
            commit_author=candidate.commit.author,
            commit_date=candidate.commit.date,
            existing_tags=candidate.existing_tags,
            package=candidate.package,
        )

    # -- rollouts -----------------------------------------------------------
    async def preview_rollouts(
        self, params: ManagedRolloutPreviewRequest
    ) -> list[ManagedRolloutRead]:
        """Start one preview per target; each runs as its own durable operation.

        A new preview supersedes the target's earlier unapplied ones.
        """
        # Lock every target up front, in id order, so two batch previews over
        # the same targets cannot deadlock on each other's row locks.
        await self.session.execute(
            select(ManagedTarget.id)
            .where(ManagedTarget.id.in_(params.target_ids))
            .order_by(ManagedTarget.id)
            .with_for_update()
        )
        rollouts: list[ManagedRollout] = []
        for target_id in params.target_ids:
            target = await self._target(target_id)
            rollouts.append(
                await self._new_rollout(
                    target,
                    ManagedRolloutKind.APPLY,
                    tag=params.tag or target.pinned_tag,
                )
            )
        await self.session.commit()
        for rollout in rollouts:
            if not await self._start(rollout, "preview"):
                continue
            await audit_managed_event(
                self.session,
                actor=self.role,
                resource_type="managed_rollout",
                action="preview",
                resource_id=rollout.id,
                data={"release_tag": rollout.tag, "target_id": str(rollout.target_id)},
            )
        return [await self.get_rollout(rollout.id) for rollout in rollouts]

    async def unassign_target(self, target_id: uuid.UUID) -> ManagedRolloutRead:
        """Preview unassigning a target. Applying it deletes its managed resources."""
        target = await self._target(target_id)
        rollout = await self._new_rollout(target, ManagedRolloutKind.UNASSIGN, tag=None)
        await self.session.commit()
        await self._start(rollout, "preview")
        return await self.get_rollout(rollout.id)

    async def apply_rollouts(
        self, params: ManagedRolloutApplyRequest
    ) -> list[ManagedRolloutRead]:
        """Apply the ready rollouts among ``rollout_ids``; skip the rest.

        Applying a tag also moves the target's pin to it.
        """
        started: list[ManagedRollout] = []
        found: list[uuid.UUID] = []
        for rollout_id in params.rollout_ids:
            rollout = await self.session.scalar(
                select(ManagedRollout)
                .where(ManagedRollout.id == rollout_id)
                .with_for_update()
            )
            if rollout is None:
                continue
            found.append(rollout_id)
            if rollout.status not in (
                ManagedRolloutStatus.READY,
                ManagedRolloutStatus.NO_CHANGES,
            ):
                continue
            rollout.status = ManagedRolloutStatus.APPLYING
            if rollout.kind == ManagedRolloutKind.APPLY and rollout.target_id:
                target = await self.session.scalar(
                    select(ManagedTarget).where(ManagedTarget.id == rollout.target_id)
                )
                if target is not None and rollout.tag:
                    target.pinned_tag = rollout.tag
            started.append(rollout)
        await self.session.commit()
        for rollout in started:
            await self._start(rollout, "apply")
        return [await self.get_rollout(rollout_id) for rollout_id in found]

    async def get_rollout(self, rollout_id: uuid.UUID) -> ManagedRolloutRead:
        """Get one rollout record.

        Raises:
            TracecatNotFoundError: The rollout does not exist.
        """
        rollout = await self.session.scalar(
            select(ManagedRollout)
            .where(ManagedRollout.id == rollout_id)
            .execution_options(populate_existing=True)
        )
        if rollout is None:
            raise TracecatNotFoundError("Rollout not found")
        return _rollout_read(rollout)

    async def list_rollouts(
        self, page: PageParams, *, target_id: uuid.UUID | None = None
    ) -> Page[ManagedRolloutRead]:
        """List rollout records, newest first."""
        stmt = select(ManagedRollout)
        if target_id is not None:
            stmt = stmt.where(ManagedRollout.target_id == target_id)
        result = await paginate(
            self.session,
            stmt,
            page=page,
            order_by=(ManagedRollout.created_at.desc(), ManagedRollout.id.desc()),
        )
        return Page(
            items=[_rollout_read(rollout) for rollout in result.items],
            next_cursor=result.next_cursor,
        )

    # -- helpers ------------------------------------------------------------
    async def _new_rollout(
        self,
        target: ManagedTarget,
        kind: ManagedRolloutKind,
        *,
        tag: str | None,
    ) -> ManagedRollout:
        # Serialize rollouts per target, so two concurrent previews cannot
        # both survive the supersede below.
        await self.session.execute(
            select(ManagedTarget.id)
            .where(ManagedTarget.id == target.id)
            .with_for_update()
        )
        await self.session.execute(
            update(ManagedRollout)
            .where(
                ManagedRollout.target_id == target.id,
                ManagedRollout.status.in_(_SUPERSEDABLE),
            )
            .values(status=ManagedRolloutStatus.STALE, updated_at=func.now())
        )
        rollout = ManagedRollout(
            workspace_id=target.workspace_id,
            target_id=target.id,
            kind=kind,
            status=ManagedRolloutStatus.PREVIEWING,
            tag=tag,
            created_by=self.role.user_id,
        )
        self.session.add(rollout)
        await self.session.flush()
        return rollout

    async def _start(self, rollout: ManagedRollout, phase: RolloutPhase) -> bool:
        """Start a phase; record a rollout that could not start as failed."""
        try:
            await self._runner.start(rollout.id, phase)
        except Exception as e:
            logger.opt(exception=e).warning(
                "Could not start managed rollout", rollout_id=str(rollout.id)
            )
            await self.session.execute(
                update(ManagedRollout)
                .where(ManagedRollout.id == rollout.id)
                .values(
                    status=ManagedRolloutStatus.FAILED,
                    error=f"Could not start the {phase}. Try again.",
                    updated_at=func.now(),
                )
            )
            await self.session.commit()
            return False
        return True

    async def _target(self, target_id: uuid.UUID) -> ManagedTarget:
        target = await self.session.scalar(
            select(ManagedTarget).where(ManagedTarget.id == target_id)
        )
        if target is None:
            raise TracecatNotFoundError("Target not found")
        return target

    async def _require_tag(self, source: ManagedSource, tag: str) -> None:
        if (
            await resolve_source_tag(
                self.session, source, tag, transport_factory=self._transport_factory
            )
            is None
        ):
            raise TracecatNotFoundError(
                f"Tag {tag!r} not found in the source repository"
            )

    async def _audit(
        self,
        resource_type: AuditResourceType,
        action: AuditAction,
        resource_id: uuid.UUID,
        workspace: Workspace | None,
        *,
        data: AuditMetadata | None = None,
        customer: bool = True,
    ) -> None:
        await audit_managed_event(
            self.session,
            actor=self.role,
            resource_type=resource_type,
            action=action,
            resource_id=resource_id,
            data=data,
            target_organization_id=(
                workspace.organization_id if customer and workspace else None
            ),
            target_workspace_id=workspace.id if workspace else None,
        )


_UUID_KEYED_KINDS = frozenset(
    {ManagedReferenceKind.MODEL, ManagedReferenceKind.MCP_INTEGRATION}
)
"""Reference kinds whose source key is the source-side resource id."""

_SUPERSEDABLE = (
    ManagedRolloutStatus.PREVIEWING,
    ManagedRolloutStatus.READY,
    ManagedRolloutStatus.NO_CHANGES,
    ManagedRolloutStatus.BLOCKED,
    ManagedRolloutStatus.NEEDS_REFERENCES,
)


def _target_row(row: tuple[object, ...]) -> ManagedTargetRead:
    """Build a target read from a ``(target, workspace, org id, org name)`` row."""
    target, workspace_name, organization_id, organization_name = row
    if not isinstance(target, ManagedTarget):
        raise TypeError("Expected a ManagedTarget row")
    return ManagedTargetRead.model_validate(
        {
            **_target_fields(target),
            "workspace_name": workspace_name,
            "organization_id": organization_id,
            "organization_name": organization_name,
        }
    )


def _target_fields(target: ManagedTarget) -> dict[str, object]:
    return {
        "id": target.id,
        "workspace_id": target.workspace_id,
        "pinned_tag": target.pinned_tag,
        "last_applied_tag": target.last_applied_tag,
        "last_rollout_status": target.last_rollout_status,
        "last_rollout_at": target.last_rollout_at,
        "assigned_at": target.assigned_at,
    }


def _rollout_read(rollout: ManagedRollout) -> ManagedRolloutRead:
    return ManagedRolloutRead(
        id=rollout.id,
        target_id=rollout.target_id,
        workspace_id=rollout.workspace_id,
        kind=rollout.kind,
        status=rollout.status,
        tag=rollout.tag,
        commit_sha=rollout.commit_sha,
        preview=RolloutPreview.model_validate(rollout.preview)
        if rollout.preview
        else None,
        error=rollout.error,
        created_at=rollout.created_at,
        applied_at=rollout.applied_at,
    )


def _source_read(source: ManagedSource) -> ManagedSourceRead:
    return ManagedSourceRead.model_validate(source, from_attributes=True)
