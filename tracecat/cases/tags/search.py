"""Batch resolution of legacy tag identifiers for case search."""

from collections.abc import Sequence
from uuid import UUID

from slugify import slugify
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.cases.types import ResolvedCaseTag
from tracecat.db.models import CaseTag
from tracecat.exceptions import TracecatValidationError


async def resolve_case_tag_filters(
    *, session: AsyncSession, workspace_id: UUID, tags: Sequence[str] | None
) -> list[ResolvedCaseTag]:
    """Resolve every nonblank identifier with one workspace-scoped query.

    UUID inputs match IDs exclusively. Other inputs try the exact ref before
    the established free-form-name slug fallback. Candidate lookup is deduplicated,
    while results preserve input order and duplicates for legacy callers.
    A missing identifier rejects the entire filter, including mixed valid inputs.
    """
    identifiers = [identifier for identifier in tags or [] if identifier.strip()]
    if not identifiers:
        return []

    candidates: dict[str, UUID | tuple[str, ...]] = {}
    tag_ids: set[UUID] = set()
    tag_refs: set[str] = set()
    for identifier in dict.fromkeys(identifiers):
        if "\x00" in identifier:
            raise TracecatValidationError(
                "Case tag filters cannot contain null characters"
            )
        try:
            tag_id = UUID(identifier)
        except ValueError:
            slug = slugify(identifier)
            refs = (identifier, slug) if slug and slug != identifier else (identifier,)
            candidates[identifier] = refs
            tag_refs.update(refs)
        else:
            candidates[identifier] = tag_id
            tag_ids.add(tag_id)

    statement = select(CaseTag.id, CaseTag.ref).where(
        CaseTag.workspace_id == workspace_id,
        or_(CaseTag.id.in_(tag_ids), CaseTag.ref.in_(tag_refs)),
    )
    rows = (await session.execute(statement)).tuples().all()
    by_id = {tag_id: ResolvedCaseTag(id=tag_id, ref=ref) for tag_id, ref in rows}
    by_ref = {tag.ref: tag for tag in by_id.values()}
    resolved: list[ResolvedCaseTag] = []
    for identifier in identifiers:
        candidate = candidates[identifier]
        if isinstance(candidate, UUID):
            tag = by_id.get(candidate)
        else:
            tag = next((by_ref[ref] for ref in candidate if ref in by_ref), None)
        if tag is None:
            raise TracecatValidationError(f"Unknown case tag filter: {identifier!r}")
        resolved.append(tag)
    return resolved
