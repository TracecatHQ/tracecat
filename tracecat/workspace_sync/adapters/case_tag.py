"""Case tag resource adapter."""

from __future__ import annotations

import uuid
from collections.abc import Iterable, Mapping
from typing import cast

import sqlalchemy as sa
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from tracecat.db.models import CaseTag
from tracecat.service import BaseWorkspaceService
from tracecat.workspace_sync.adapters.base import (
    ImportedResource,
    ProjectedResource,
    ResourceProjection,
    SingleYamlAdapter,
    unique_source_id,
)
from tracecat.workspace_sync.enums import SyncResourceType
from tracecat.workspace_sync.schemas import CASE_TAG_ROOT, CaseTagResourceSpec


class CaseTagAdapter(SingleYamlAdapter):
    resource_type = SyncResourceType.CASE_TAG
    spec_attr = "case_tags"
    model = CaseTagResourceSpec
    root = CASE_TAG_ROOT

    async def project(self, ctx: BaseWorkspaceService) -> ResourceProjection:
        stmt = (
            select(CaseTag)
            .where(CaseTag.workspace_id == ctx.workspace_id)
            .order_by(CaseTag.ref.asc(), CaseTag.id.asc())
        )
        tags = list((await ctx.session.execute(stmt)).scalars().all())
        source_ids_by_local_id = await self.source_ids_by_local_id(ctx)
        specs: dict[str, BaseModel] = {}
        resources: list[ProjectedResource] = []
        reserved: set[str] = set(source_ids_by_local_id.values())
        for tag in tags:
            source_id = source_ids_by_local_id.get(tag.id)
            if source_id is None:
                source_id = unique_source_id(tag.ref, reserved=reserved)
            reserved.add(source_id)
            specs[source_id] = CaseTagResourceSpec(
                id=source_id,
                name=tag.name,
                color=tag.color,
            )
            resources.append(self.projected_resource(source_id, tag.id))
        return ResourceProjection(specs=specs, resources=resources)

    async def import_specs(
        self,
        ctx: BaseWorkspaceService,
        specs: Mapping[str, BaseModel],
    ) -> list[ImportedResource]:
        tags = cast(Mapping[str, CaseTagResourceSpec], specs)
        if not tags:
            return []

        duplicate_names = sorted(_duplicates(spec.name for spec in tags.values()))
        if duplicate_names:
            raise ValueError(
                "Case tag sync specs must have unique names: "
                + ", ".join(repr(name) for name in duplicate_names)
            )

        source_ids = set(tags)
        names = {spec.name for spec in tags.values()}
        mapped_local_ids_by_source_id = await self.local_ids_by_source_id(
            ctx,
            source_ids,
        )
        mapped_local_ids = set(mapped_local_ids_by_source_id.values())
        conditions = [
            CaseTag.ref.in_(source_ids),
            CaseTag.name.in_(names),
        ]
        if mapped_local_ids:
            conditions.append(CaseTag.id.in_(mapped_local_ids))
        existing_tags = list(
            (
                await ctx.session.scalars(
                    select(CaseTag).where(
                        CaseTag.workspace_id == ctx.workspace_id,
                        sa.or_(*conditions),
                    )
                )
            ).all()
        )
        tags_by_id = {tag.id: tag for tag in existing_tags}
        tags_by_source_id = {
            source_id: tag
            for source_id, local_id in mapped_local_ids_by_source_id.items()
            if (tag := tags_by_id.get(local_id)) is not None
        }
        tags_by_ref = {tag.ref: tag for tag in existing_tags}
        tags_by_name = {tag.name: tag for tag in existing_tags}
        source_ids_by_tag_id: dict[uuid.UUID, str] = {
            tag.id: source_id for source_id, tag in tags_by_source_id.items()
        }
        for ref, tag in tags_by_ref.items():
            source_ids_by_tag_id.setdefault(tag.id, ref)
        import_targets: list[tuple[str, CaseTagResourceSpec, CaseTag | None]] = []
        for source_id, spec in sorted(tags.items()):
            tag = tags_by_source_id.get(source_id) or tags_by_ref.get(source_id)
            name_owner = tags_by_name.get(spec.name)
            if (
                name_owner is not None
                and (tag is None or name_owner.id != tag.id)
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

        for _source_id, spec, tag in import_targets:
            if tag is not None and tag.name != spec.name:
                tag.name = f"__tracecat_sync_tmp_{tag.id}"
                ctx.session.add(tag)
        try:
            await ctx.session.flush()
        except IntegrityError as e:
            raise ValueError(
                "Case tag sync encountered a duplicate tag name or ref"
            ) from e

        imported_tags: list[tuple[str, CaseTag]] = []
        for source_id, spec, tag in import_targets:
            if tag is None:
                tag = CaseTag(
                    workspace_id=ctx.workspace_id,
                    name=spec.name,
                    ref=source_id,
                    color=spec.color,
                )
            else:
                tag.name = spec.name
                tag.ref = source_id
                tag.color = spec.color
            ctx.session.add(tag)
            imported_tags.append((source_id, tag))
        try:
            await ctx.session.flush()
        except IntegrityError as e:
            raise ValueError(
                "Case tag sync encountered a duplicate tag name or ref"
            ) from e
        return [
            self.imported_resource(source_id, tag.id)
            for source_id, tag in imported_tags
        ]


def _duplicates(values: Iterable[str]) -> set[str]:
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
    owner_source_id = source_ids_by_tag_id.get(name_owner.id)
    owner_spec = specs.get(owner_source_id) if owner_source_id is not None else None
    return owner_spec is not None and owner_spec.name != name_owner.name
