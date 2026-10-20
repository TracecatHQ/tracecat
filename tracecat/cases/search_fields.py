"""Discover case filter inputs from the same resolver that executes queries."""

from collections.abc import Mapping, Sequence
from uuid import UUID

import sqlalchemy as sa

from tracecat.cases.enums import CasePriority, CaseSeverity, CaseStatus
from tracecat.cases.query import CaseFieldResolver
from tracecat.cases.search_schemas import CaseFilterField, CaseFilterMetadata
from tracecat.exceptions import TracecatValidationError
from tracecat.query.filters import MAX_FILTER_CONDITIONS, MAX_FILTER_DEPTH
from tracecat.query.resolver import FieldKind


def case_filter_metadata(
    workspace_id: UUID,
    schema: Mapping[str, object],
    dropdown_refs: Sequence[str],
) -> CaseFilterMetadata:
    """Advertise supported fields without maintaining a second operator matrix.

    Dropdown labels/options, tag labels and member labels come from existing
    workspace metadata in the client. Payload paths are supplied by the user.
    """
    resolver = CaseFieldResolver(workspace_id, schema, dropdown_refs)
    addresses = [
        "status",
        "priority",
        "severity",
        "assignee_id",
        "tags",
        "summary",
        "description",
        "case_number",
        "created_at",
        "updated_at",
        *(f"fields.{name}" for name in schema),
        *(f"dropdowns.{ref}" for ref in dropdown_refs),
        "payload.<path>",
    ]
    enum_choices = {
        "status": [item.value for item in CaseStatus],
        "priority": [item.value for item in CasePriority],
        "severity": [item.value for item in CaseSeverity],
    }
    labels = {
        "status": "Status",
        "priority": "Priority",
        "severity": "Severity",
        "assignee_id": "Assignee",
        "tags": "Tags",
        "summary": "Summary",
        "description": "Description",
        "case_number": "Case number",
        "created_at": "Created at",
        "updated_at": "Updated at",
        "payload.<path>": "Payload path",
    }
    fields: list[CaseFilterField] = []
    for address in addresses:
        try:
            resolved = resolver.resolve(address)
        except TracecatValidationError:
            # Generic JSON and multi-select custom fields are deliberately not
            # advertised. Existing saved conditions still reach validation.
            continue
        if resolved is None:
            continue
        choices = enum_choices.get(address)
        metadata = schema.get(address.removeprefix("fields."))
        label = labels.get(address, address)
        if address.startswith("fields.") and isinstance(metadata, Mapping):
            if isinstance(display_name := metadata.get("display_name"), str):
                label = display_name
            if isinstance(options := metadata.get("options"), list):
                choices = [option for option in options if isinstance(option, str)]
        input_type = "text"
        if resolved.kind is FieldKind.NUMBER:
            input_type = "number"
        elif resolved.kind is FieldKind.TEMPORAL:
            value_type = resolved.value_type
            if value_type is None:
                value_type = resolved.expr.type if not callable(resolved.expr) else None
            input_type = (
                "datetime-local" if isinstance(value_type, sa.DateTime) else "date"
            )
        fields.append(
            CaseFilterField(
                field=address,
                label=label,
                kind=resolved.kind,
                operators=sorted(resolved.allowed_ops),
                choices=choices,
                input_type=input_type,
            )
        )
    return CaseFilterMetadata(
        fields=fields,
        max_depth=MAX_FILTER_DEPTH,
        max_conditions=MAX_FILTER_CONDITIONS,
    )
