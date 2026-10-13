"""Batch validation of workspace metadata referenced by case filter trees."""

from __future__ import annotations

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.cases.query import DROPDOWN_PREFIX, CaseFieldResolver
from tracecat.cases.search_adapters import iter_case_filter_conditions
from tracecat.db.models import CaseDropdownDefinition, CaseDropdownOption, CaseTag
from tracecat.exceptions import EntitlementRequired, TracecatValidationError
from tracecat.query.compiler import compile_filter
from tracecat.query.filters import Filter, FilterScalar
from tracecat.tiers.enums import Entitlement


def _string_values(value: FilterScalar | list[FilterScalar] | None) -> list[str]:
    if value is None:
        return []
    values = value if isinstance(value, list) else [value]
    # The shared compiler has already validated these tag/dropdown values as text.
    strings: list[str] = []
    for item in values:
        assert isinstance(item, str)
        strings.append(item)
    return strings


async def validate_case_filter_metadata(
    *,
    session: AsyncSession,
    workspace_id: UUID,
    filters: Sequence[Filter | None],
    case_addons_enabled: bool,
    extra_dropdown_refs: Sequence[str] = (),
) -> tuple[str, ...]:
    """Validate tag refs and dropdown refs/options without per-leaf queries.

    Operators and value shapes use the existing case resolver/compiler.
    Metadata is validated under every boolean group, including NOT, before
    filtering executes. Structured tags use exact refs; legacy UUID/name
    inputs must first use the existing tag-service compatibility resolver.

    Args:
        session: The caller's workspace-authorized database session.
        workspace_id: Workspace selected by the authenticated service role.
        filters: Separately validated explicit and legacy trees.
        case_addons_enabled: The service's trusted CASE_ADDONS entitlement.
        extra_dropdown_refs: Definitions referenced by grouping or metrics.

    Returns:
        Validated dropdown refs to supply to ``CaseFieldResolver``.

    Raises:
        EntitlementRequired: Any dropdown is requested without its entitlement.
        TracecatValidationError: A metadata condition or reference is invalid.
    """
    conditions = [
        condition for tree in filters for condition in iter_case_filter_conditions(tree)
    ]
    dropdown_refs = set(extra_dropdown_refs)
    dropdown_refs.update(
        condition.field.removeprefix(DROPDOWN_PREFIX)
        for condition in conditions
        if condition.field.startswith(DROPDOWN_PREFIX)
    )
    if dropdown_refs and not case_addons_enabled:
        raise EntitlementRequired(Entitlement.CASE_ADDONS.value)

    resolver = CaseFieldResolver(workspace_id, {}, tuple(sorted(dropdown_refs)))
    tag_refs: set[str] = set()
    dropdown_options: dict[str, set[str]] = {}
    for condition in conditions:
        if condition.field != "tags" and not condition.field.startswith(
            DROPDOWN_PREFIX
        ):
            continue
        compile_filter(condition, resolver)
        values = _string_values(condition.value)
        if condition.field == "tags":
            tag_refs.update(values)
        else:
            ref = condition.field.removeprefix(DROPDOWN_PREFIX)
            dropdown_options.setdefault(ref, set()).update(values)

    if tag_refs:
        known_tags = set(
            (
                await session.scalars(
                    select(CaseTag.ref).where(
                        CaseTag.workspace_id == workspace_id,
                        CaseTag.ref.in_(tag_refs),
                    )
                )
            ).all()
        )
        if missing_tags := tag_refs - known_tags:
            raise TracecatValidationError(
                f"Unknown case tag refs: {', '.join(sorted(missing_tags))}"
            )

    if not dropdown_refs:
        return ()

    requested_options = {
        option for options in dropdown_options.values() for option in options
    }
    statement = (
        select(CaseDropdownDefinition.ref, CaseDropdownOption.ref)
        .outerjoin(
            CaseDropdownOption,
            and_(
                CaseDropdownOption.definition_id == CaseDropdownDefinition.id,
                CaseDropdownOption.ref.in_(requested_options),
            ),
        )
        .where(
            CaseDropdownDefinition.workspace_id == workspace_id,
            CaseDropdownDefinition.ref.in_(dropdown_refs),
        )
    )
    result = await session.execute(statement)
    known_options: dict[str, set[str]] = {}
    for ref, option in result.tuples().all():
        options = known_options.setdefault(ref, set())
        if option is not None:
            options.add(option)

    if missing_dropdowns := dropdown_refs - known_options.keys():
        raise TracecatValidationError(
            f"Unknown case dropdown refs: {', '.join(sorted(missing_dropdowns))}"
        )
    for ref, options in dropdown_options.items():
        if missing_options := options - known_options[ref]:
            raise TracecatValidationError(
                f"Unknown options for case dropdown {ref!r}: "
                f"{', '.join(sorted(missing_options))}"
            )
    return tuple(sorted(dropdown_refs))
