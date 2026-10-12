"""Domain types for forms."""

from dataclasses import dataclass
from typing import Any

from tracecat.forms.enums import (
    FormAutofillSource,
    FormInputKind,
    FormIssueSeverity,
)
from tracecat.forms.schemas import (
    FormBlock,
    FormFieldBlock,
    FormFieldError,
    FormIssue,
)


@dataclass(frozen=True, slots=True)
class FormInputType:
    """A workflow input type, classified for the widget registry."""

    kind: FormInputKind
    enum_values: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class WidgetSpec:
    """What a widget accepts."""

    accepts: frozenset[FormInputKind]
    options: bool = False
    """Whether the widget renders a list of choices."""
    placeholder: bool = True


@dataclass(frozen=True, slots=True)
class ResolveResult:
    """A form config resolved against a workflow input schema."""

    blocks: list[FormBlock]
    hidden: list[str]
    """Inputs the author hid. They take their schema default."""
    autofill: dict[str, FormAutofillSource]
    """Inputs the server fills in on submit."""
    issues: list[FormIssue]

    @property
    def has_errors(self) -> bool:
        return any(issue.severity is FormIssueSeverity.ERROR for issue in self.issues)


@dataclass(frozen=True, slots=True)
class CheckedValues:
    """Submitted values after the checks that need no database."""

    visible: list[FormFieldBlock]
    """Fields the requester could see, given the values they sent."""
    values: dict[str, Any]
    """Values of visible fields that passed. Empty values are left out."""
    errors: list[FormFieldError]
