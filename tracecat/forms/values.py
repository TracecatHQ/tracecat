"""Checks on submitted form values that need no database.

The server re-evaluates everything a client is expected to do: which fields
are visible, which are required and which choices are allowed.
"""

import re
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from typing import Any

from pydantic import ValidationError

from tracecat.dsl.validation import normalize_trigger_inputs
from tracecat.expressions.schemas import ExpectedField
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


def is_empty_answer(value: Any) -> bool:
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
        if _equals(
            controller.default if is_empty_answer(answer) else answer, rule.equals
        ):
            visible.append(field)
    return visible


# The ECMAScript date time string format, which ``new Date()`` parses in every
# browser: YYYY-MM-DDTHH:MM, optional seconds and fraction, and an optional Z
# or ±HH:MM offset.
_BROWSER_DATETIME = re.compile(
    r"[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}"
    r"(?::[0-9]{2}(?:\.[0-9]+)?)?(?:Z|[+-][0-9]{2}:[0-9]{2})?"
)


def _is_iso_date(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        # Other ISO forms parse too, such as the week date 2026-W06-2.
        return date.fromisoformat(value).isoformat() == value
    except ValueError:
        return False


def _is_iso_datetime(value: Any) -> bool:
    # Other ISO forms parse too, such as 2026-W06-2T10:00:00 and
    # 20260203T100000, but a browser shows them as Invalid Date.
    if not isinstance(value, str) or not _BROWSER_DATETIME.fullmatch(value):
        return False
    try:
        datetime.fromisoformat(value)
    except ValueError:
        return False
    return True


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
            "Enter a date and time as YYYY-MM-DDTHH:MM, with optional seconds "
            "and a Z or ±HH:MM offset",
        )
    return None


def check_answer(
    field: FormFieldBlock, expected: ExpectedField, value: Any
) -> tuple[FormFieldErrorCode, str] | None:
    """Check one non-empty answer as a submission does.

    The widget's checks come first: shape, options and date format. Then the
    value is read as its input's type, as the run reads it, and an int must
    stay in the browser-safe range. The resolver checks each field's default
    with this, so a form never opens with an answer it cannot submit.
    """
    if (problem := _check_value(field, value)) is not None:
        return problem
    try:
        normalized = normalize_trigger_inputs(
            {field.name: expected}, {field.name: value}
        )
    except ValidationError as e:
        detail = e.errors(include_input=False, include_url=False)[0]
        return FormFieldErrorCode.INVALID_VALUE, detail["msg"]
    if is_unsafe_integer(normalized[field.name]):
        return FormFieldErrorCode.INVALID_VALUE, UNSAFE_INTEGER_MESSAGE
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
        if is_empty_answer(value):
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
