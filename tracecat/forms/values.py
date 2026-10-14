"""Checks on submitted form values that need no database.

The server re-evaluates everything a client is expected to do: which fields
are visible, which are required and which choices are allowed.
"""

from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from tracecat.forms.constants import MAX_SAFE_INTEGER
from tracecat.forms.enums import FormFieldErrorCode, FormWidget
from tracecat.forms.schemas import FormBlock, FormFieldBlock, FormFieldError
from tracecat.forms.types import CheckedValues


def _equals(value: Any, expected: Any) -> bool:
    """Compare a controller's value with a ``visible_if`` value.

    Python treats ``True == 1``. A toggle must not satisfy a number rule.
    """
    if isinstance(value, bool) or isinstance(expected, bool):
        return value is expected
    return value == expected


UNSAFE_INTEGER_MESSAGE = (
    f"Enter a whole number between -{MAX_SAFE_INTEGER:,} and {MAX_SAFE_INTEGER:,}"
)


def is_unsafe_integer(value: Any) -> bool:
    """Whether a value is an int a browser cannot hold exactly.

    The one range check of forms: int defaults, ``visible_if`` values and
    answers, once read as their input's type. Floats are never caught.
    """
    return (
        isinstance(value, int)
        and not isinstance(value, bool)
        and abs(value) > MAX_SAFE_INTEGER
    )


def _is_empty(value: Any) -> bool:
    """Whether an answer counts as left empty. False is a valid toggle answer."""
    return value is None or value == "" or value == []


def visible_fields(
    blocks: Sequence[FormBlock], values: Mapping[str, Any]
) -> list[FormFieldBlock]:
    """Return the fields a requester with these values sees, in block order.

    A conditional field is visible while its controller equals the rule's
    value. A controller left out or empty counts as its default, which is
    what the run receives for it, as on the requester page.
    """
    fields = [block for block in blocks if isinstance(block, FormFieldBlock)]
    by_name = {field.name: field for field in fields}
    visible: list[FormFieldBlock] = []
    for field in fields:
        if (rule := field.visible_if) is None:
            visible.append(field)
            continue
        if (controller := by_name.get(rule.field)) is None:
            continue
        answer = values.get(rule.field)
        if _equals(controller.default if _is_empty(answer) else answer, rule.equals):
            visible.append(field)
    return visible


def _is_iso_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        # Other ISO forms parse too, such as the week date 2026-W06-2.
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _is_iso_datetime(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    # A date alone parses as midnight; it is not a date and time.
    return len(value) > len("YYYY-MM-DD")


def _check_value(
    field: FormFieldBlock, value: Any
) -> tuple[FormFieldErrorCode, str] | None:
    if field.multiple != isinstance(value, list):
        expected = "a list" if field.multiple else "a single value"
        return FormFieldErrorCode.INVALID_VALUE, f"Expected {expected}"
    items = value if field.multiple else [value]
    if field.options is not None:
        allowed = {option.value for option in field.options}
        if any(not isinstance(item, str) or item not in allowed for item in items):
            return FormFieldErrorCode.INVALID_OPTION, "Choose one of the options"
    if field.widget is FormWidget.DATE and not all(map(_is_iso_date, items)):
        return FormFieldErrorCode.INVALID_DATE, "Enter a date as YYYY-MM-DD"
    if field.widget is FormWidget.DATETIME and not all(map(_is_iso_datetime, items)):
        return (
            FormFieldErrorCode.INVALID_DATE,
            "Enter a date and time in ISO 8601 format",
        )
    return None


def check_values(
    blocks: Sequence[FormBlock], values: Mapping[str, Any]
) -> CheckedValues:
    """Check submitted values against a resolved form.

    Rejects keys that are not fields of the form, drops values of fields the
    requester cannot see, and checks required fields, option membership and
    date formats on the rest. Types are checked later against the workflow's
    input schema.
    """
    known = {block.name for block in blocks if isinstance(block, FormFieldBlock)}
    errors = [
        FormFieldError(
            field=name,
            code=FormFieldErrorCode.UNKNOWN_FIELD,
            message="This is not a field of the form",
        )
        for name in values
        if name not in known
    ]
    visible = visible_fields(blocks, values)
    kept: dict[str, Any] = {}
    for field in visible:
        value = values.get(field.name)
        if _is_empty(value):
            if field.required:
                errors.append(
                    FormFieldError(
                        field=field.name,
                        code=FormFieldErrorCode.REQUIRED,
                        message="This field is required",
                    )
                )
        elif (problem := _check_value(field, value)) is not None:
            code, message = problem
            errors.append(FormFieldError(field=field.name, code=code, message=message))
        else:
            kept[field.name] = value
    return CheckedValues(visible=visible, values=kept, errors=errors)
