"""Translate resolved legacy case filters into shared filter-language nodes."""

from __future__ import annotations

from collections.abc import Iterator, Mapping, Sequence
from datetime import datetime
from enum import StrEnum
from typing import assert_never
from uuid import UUID

from tracecat.auth.types import Role
from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.search_schemas import CaseQueryErrorCode
from tracecat.exceptions import TracecatValidationError
from tracecat.query.filters import (
    AndClause,
    Condition,
    Filter,
    FilterOp,
    NotClause,
    OrClause,
)

CURRENT_USER = "me"
UNASSIGNED_IDENTIFIERS = frozenset({"unassigned", "__UNASSIGNED__"})


def parse_dropdown_entry(entry: str) -> tuple[str, str]:
    """Split a ``definition_ref:option_ref`` dropdown filter into its refs.

    Raises:
        ValueError: The entry is not in that form.
    """
    definition, separator, option = entry.partition(":")
    if not separator or not definition or not option:
        raise ValueError(
            f"Invalid dropdown filter {entry!r}. Expected 'definition_ref:option_ref'."
        )
    return definition, option


def format_dropdown_entry(definition: str, option: str) -> str:
    """Join refs into a ``definition_ref:option_ref`` dropdown filter."""
    return f"{definition}:{option}"


class CurrentUserRequiredError(TracecatValidationError):
    """A query says "me", but the caller is not a user (for example a workflow)."""

    code: CaseQueryErrorCode = "current_user_required"

    def __init__(self) -> None:
        super().__init__(
            'This query uses "me", which needs a signed-in user. '
            "Workflows and service callers must pass explicit user IDs."
        )


def requesting_user_id(role: Role) -> UUID | None:
    """Return the user a query runs as, or ``None`` for non-user callers.

    Only user roles (the UI, the API, and MCP) have a "me". A workflow runs
    as a service role, even when it carries the user who triggered it, so it
    never resolves "me" or sees personal views. This keeps a workflow's
    results the same whether a person or a schedule starts it.
    """
    return role.user_id if role.type == "user" else None


def resolve_user_refs(
    values: Sequence[UUID | str], *, user_id: UUID | None
) -> list[UUID]:
    """Replace ``"me"`` with the caller's user ID and drop other sentinels.

    Args:
        values: User IDs, ``"me"``, or the unassigned sentinels.
        user_id: The requesting user's ID, or ``None`` for non-user callers.

    Returns:
        De-duplicated user IDs in input order.

    Raises:
        CurrentUserRequiredError: ``"me"`` appears and there is no user.
    """
    resolved: list[UUID] = []
    for value in values:
        if isinstance(value, UUID):
            resolved.append(value)
        elif value == CURRENT_USER:
            if user_id is None:
                raise CurrentUserRequiredError()
            resolved.append(user_id)
    return list(dict.fromkeys(resolved))


def iter_case_filter_conditions(filters: Filter | None) -> Iterator[Condition]:
    """Visit every condition, including negated ones, for metadata validation."""
    pending = [filters] if filters is not None else []
    while pending:
        match pending.pop():
            case Condition() as condition:
                yield condition
            case AndClause(and_=children) | OrClause(or_=children):
                pending.extend(reversed(children))
            case NotClause(not_=child):
                pending.append(child)
            case _ as unreachable:
                assert_never(unreachable)


def _enum_condition[EnumT: StrEnum](
    field: str, values: EnumT | Sequence[EnumT] | None
) -> Condition | None:
    if not values:
        return None
    members = [values] if isinstance(values, StrEnum) else values
    return Condition(
        field=field,
        op=FilterOp.IN,
        value=list(dict.fromkeys(member.value for member in members)),
    )


def legacy_case_filters(
    *,
    status: CaseStatus | Sequence[CaseStatus] | None = None,
    priority: CasePriority | Sequence[CasePriority] | None = None,
    severity: CaseSeverity | Sequence[CaseSeverity] | None = None,
    assignee_ids: Sequence[UUID] | None = None,
    include_unassigned: bool = False,
    creator_ids: Sequence[UUID] | None = None,
    tag_refs: Sequence[str] | None = None,
    dropdown_filters: Mapping[str, Sequence[str]] | None = None,
    start_time: datetime | None = None,
    end_time: datetime | None = None,
    updated_after: datetime | None = None,
    updated_before: datetime | None = None,
) -> Filter | None:
    """Translate legacy criteria while preserving their AND/OR semantics.

    The service must resolve every requested tag identifier to a workspace
    tag ref and validate dropdown definitions/options before calling this
    adapter. This function accepts resolved metadata rather than silently
    discarding unresolved identifiers.

    Text, exact identity, and hierarchy stay in ``CaseQuery``. Pagination,
    sorting, and returned-detail flags stay in ``CaseSearchRequest``. They
    do not become user-controlled boolean filter leaves.

    Args:
        status: One status or statuses to match with OR semantics.
        priority: One priority or priorities to match with OR semantics.
        severity: One severity or severities to match with OR semantics.
        assignee_ids: Resolved user UUIDs to match with OR semantics.
        include_unassigned: Also match an unset assignee.
        creator_ids: Resolved user UUIDs who created the case, OR semantics.
        tag_refs: Workspace-resolved tag refs; match any selected tag.
        dropdown_filters: Validated definition refs and their option refs.
        start_time: Inclusive lower creation timestamp.
        end_time: Inclusive upper creation timestamp.
        updated_after: Inclusive lower update timestamp.
        updated_before: Inclusive upper update timestamp.

    Returns:
        A shared filter tree, or ``None`` when no matching criteria exist.
    """
    conditions: list[Filter] = []
    for field, values in (
        ("status", status),
        ("priority", priority),
        ("severity", severity),
    ):
        if condition := _enum_condition(field, values):
            conditions.append(condition)

    assignees: list[Filter] = []
    if assignee_ids:
        assignees.append(
            Condition(
                field="assignee_id",
                op=FilterOp.IN,
                value=list(dict.fromkeys(assignee_ids)),
            )
        )
    if include_unassigned:
        assignees.append(Condition(field="assignee_id", op=FilterOp.IS_NULL))
    if len(assignees) == 1:
        conditions.append(assignees[0])
    elif assignees:
        conditions.append(OrClause.model_validate({"or": assignees}))

    if creator_ids:
        conditions.append(
            Condition(
                field="created_by",
                op=FilterOp.IN,
                value=list(dict.fromkeys(creator_ids)),
            )
        )

    if tag_refs:
        conditions.append(
            Condition(field="tags", op=FilterOp.IN, value=list(dict.fromkeys(tag_refs)))
        )
    for ref, option_refs in (dropdown_filters or {}).items():
        if option_refs:
            conditions.append(
                Condition(
                    field=f"dropdowns.{ref}",
                    op=FilterOp.IN,
                    value=list(dict.fromkeys(option_refs)),
                )
            )

    for field, op, value in (
        ("created_at", FilterOp.GTE, start_time),
        ("created_at", FilterOp.LTE, end_time),
        ("updated_at", FilterOp.GTE, updated_after),
        ("updated_at", FilterOp.LTE, updated_before),
    ):
        if value is not None:
            conditions.append(Condition(field=field, op=op, value=value))

    if not conditions:
        return None
    if len(conditions) == 1:
        return conditions[0]
    return AndClause.model_validate({"and": conditions})
