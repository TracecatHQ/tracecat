"""Pure value types for source declarations and parser diagnostics."""

from dataclasses import dataclass

from tracecat.agent.references.uri import ReferenceDiagnosticCode, ReferenceTarget


@dataclass(frozen=True, slots=True)
class SourceLocation:
    """One-based line/column after BOM removal; columns count Unicode code points."""

    path: str
    line: int
    column: int

    def __post_init__(self) -> None:
        if self.line < 1 or self.column < 1:
            raise ValueError("Source positions must be one-based")


@dataclass(frozen=True, slots=True)
class ReferenceOccurrence:
    """A link occurrence; presentation text never participates in target identity."""

    target: ReferenceTarget
    location: SourceLocation


@dataclass(frozen=True, slots=True)
class ReferenceDiagnostic:
    """Stable error classification and source without resource lookup or disclosure."""

    code: ReferenceDiagnosticCode
    location: SourceLocation | None = None


@dataclass(frozen=True, slots=True)
class ParsedReferences:
    """All occurrences and errors; callers must reject executable use on errors."""

    references: tuple[ReferenceOccurrence, ...]
    diagnostics: tuple[ReferenceDiagnostic, ...]
