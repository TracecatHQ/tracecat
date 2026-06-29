"""Case tag resource adapter."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tracecat.db.models import CaseTag
from tracecat.service import BaseWorkspaceService
from tracecat.workspace_sync.adapters.base import (
    FlatManifestAdapter,
    ImportedResource,
    ProjectedResource,
    ResourceProjection,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import (
    CASE_TAG_ROOT,
    CaseTagResourceSpec,
    WorkspaceSpec,
)


class CaseTagAdapter(FlatManifestAdapter):
    """Sync adapter for case tags, enforcing unique tag names on import."""

    resource_type = SyncResourceType.CASE_TAG
    spec_attr = "case_tags"
    model = CaseTagResourceSpec
    read_scope = "case:read"
    create_scope = "case:create"
    update_scope = "case:update"
    root = CASE_TAG_ROOT
    import_identity_attrs = ("name",)
    import_identity_noun = "name"

    async def project(
        self, workspace_service: BaseWorkspaceService
    ) -> ResourceProjection:
        """Project case tags into specs."""
        # Deterministic order (ref, then id) keeps the serialized output stable.
        stmt = (
            select(CaseTag)
            .where(CaseTag.workspace_id == workspace_service.workspace_id)
            .order_by(CaseTag.ref.asc(), CaseTag.id.asc())
        )
        tags = list((await workspace_service.session.execute(stmt)).scalars().all())
        assigner = await self.source_id_assigner(workspace_service)
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        for tag in tags:
            source_id = assigner.assign(tag.id, tag.ref)
            specs[source_id] = CaseTagResourceSpec(
                id=source_id,
                name=tag.name,
                color=tag.color,
            )
            resources.append(self.projected_resource(source_id, tag.id))
        return ResourceProjection(specs=specs, resources=resources)

    async def import_specs(
        self,
        workspace_service: BaseWorkspaceService,
        workspace_spec: WorkspaceSpec,
    ) -> list[ImportedResource]:
        """Reconcile case tag specs, resolving name collisions before persisting.

        Rejects duplicate names within the batch and unreleased name conflicts
        with existing tags, then temporarily renames tags being relabeled so the
        unique name constraint holds across the two-phase flush.
        """
        tags = workspace_spec.case_tags
        if not tags:
            return []

        # Fail fast: the unique-name constraint can't be satisfied if the batch
        # itself ships two specs with the same name.
        duplicate_names = sorted(_duplicates(spec.name for spec in tags.values()))
        if duplicate_names:
            raise ValueError(
                "Case tag sync specs must have unique names: "
                + ", ".join(repr(name) for name in duplicate_names)
            )

        source_ids = set(tags)
        names = {spec.name for spec in tags.values()}
        # Resolve which incoming source ids already map to local tag rows.
        mapped_local_ids_by_source_id = await self.local_ids_by_source_id(
            workspace_service,
            source_ids,
        )
        mapped_local_ids = set(mapped_local_ids_by_source_id.values())
        # Load every tag that could be involved: matched by ref, by target name
        # (potential conflict), or by a previously mapped local id.
        conditions = [
            CaseTag.ref.in_(source_ids),
            CaseTag.name.in_(names),
        ]
        if mapped_local_ids:
            conditions.append(CaseTag.id.in_(mapped_local_ids))
        existing_tags = list(
            (
                await workspace_service.session.scalars(
                    select(CaseTag).where(
                        CaseTag.workspace_id == workspace_service.workspace_id,
                        sa.or_(*conditions),
                    )
                )
            ).all()
        )
        # Index the candidate tags by the various keys we resolve against.
        tags_by_id = {tag.id: tag for tag in existing_tags}
        tags_by_source_id = {
            source_id: tag
            for source_id, local_id in mapped_local_ids_by_source_id.items()
            if (tag := tags_by_id.get(local_id)) is not None
        }
        tags_by_ref = {tag.ref: tag for tag in existing_tags}
        tags_by_name = {tag.name: tag for tag in existing_tags}
        # Best-known source id for each existing tag: prefer the sync mapping,
        # else fall back to its ref. Used to tell whether a name's current owner
        # is itself being renamed by this batch.
        source_ids_by_tag_id: dict[uuid.UUID, str] = {
            tag.id: source_id for source_id, tag in tags_by_source_id.items()
        }
        for ref, tag in tags_by_ref.items():
            source_ids_by_tag_id.setdefault(tag.id, ref)
        # Pair each spec with the existing tag it targets (mapping first, then
        # ref), rejecting any name already held by an unrelated tag.
        import_targets: list[tuple[str, CaseTagResourceSpec, CaseTag | None]] = []
        for source_id, spec in sorted(tags.items()):
            tag = tags_by_source_id.get(source_id) or tags_by_ref.get(source_id)
            name_owner = tags_by_name.get(spec.name)
            if (
                name_owner is not None
                # The name is held by a different tag...
                and (tag is None or name_owner.id != tag.id)
                # ...and that holder is not vacating the name in this same batch.
                and not _name_owner_released_by_batch(
                    name_owner,
                    specs=tags,
                    source_ids_by_tag_id=source_ids_by_tag_id,
                )
            ):
                raise ValueError(
                    f"Case tag name {spec.name!r} already exists in this workspace"
                )
            import_targets.append((source_id, spec, tag))

        # Phase 1: park every tag whose name is changing under a unique temporary
        # name. This clears the unique-name index before any final name is taken,
        # so renames that swap names between two tags don't transiently collide.
        for _source_id, spec, tag in import_targets:
            if tag is not None and tag.name != spec.name:
                tag.name = f"__tracecat_sync_tmp_{tag.id}"
                workspace_service.session.add(tag)
        try:
            await workspace_service.session.flush()
        except IntegrityError as e:
            raise ValueError(
                "Case tag sync encountered a duplicate tag name or ref"
            ) from e

        # Phase 2: write the final name/ref/color, creating rows as needed now
        # that the temporary names have freed up the target names.
        imported_tags: list[tuple[str, CaseTag]] = []
        for source_id, spec, tag in import_targets:
            if tag is None:
                tag = CaseTag(
                    workspace_id=workspace_service.workspace_id,
                    name=spec.name,
                    ref=source_id,
                    color=spec.color,
                )
            else:
                tag.name = spec.name
                tag.ref = source_id
                tag.color = spec.color
            workspace_service.session.add(tag)
            imported_tags.append((source_id, tag))
        try:
            await workspace_service.session.flush()
        except IntegrityError as e:
            raise ValueError(
                "Case tag sync encountered a duplicate tag name or ref"
            ) from e
        return [
            self.imported_resource(source_id, tag.id)
            for source_id, tag in imported_tags
        ]


def _duplicates(values: Iterable[str]) -> set[str]:
    """Return the set of values that appear more than once in ``values``."""
    seen: set[str] = set()
    duplicates: set[str] = set()
    for value in values:
        if value in seen:
            duplicates.add(value)
        else:
            seen.add(value)
    return duplicates


def _name_owner_released_by_batch(
    name_owner: CaseTag,
    *,
    specs: Mapping[str, CaseTagResourceSpec],
    source_ids_by_tag_id: Mapping[uuid.UUID, str],
) -> bool:
    """Return whether the current owner of a name is being renamed by this batch.

    A ``True`` result means the name will be freed during the same import, so a
    different tag may safely claim it.
    """
    owner_source_id = source_ids_by_tag_id.get(name_owner.id)
    owner_spec = specs.get(owner_source_id) if owner_source_id is not None else None
    return owner_spec is not None and owner_spec.name != name_owner.name
