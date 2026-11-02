"""Shared lexical search contracts, independent of SQL and semantic retrieval."""

from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from typing import Literal

from tracecat.exceptions import TracecatException

MAX_SEARCH_TEXT_LENGTH = 1_000
MAX_SEARCH_TERMS = 32
MIN_LITERAL_LENGTH = 3
MAX_MATCHED_COLUMNS = 8
MAX_MATCH_COLUMN_LENGTH = 255
MAX_MATCH_EXCERPTS = 3
MAX_MATCH_EXCERPT_LENGTH = 240
TEXT_SEARCH_CONFIG = "pg_catalog.simple"


class SearchMode(StrEnum):
    """Explicit lexical modes; omitted table mode remains a legacy policy."""

    FULL_TEXT = "full_text"
    LITERAL = "literal"


class LiteralMatchPolicy(StrEnum):
    """LIKE escaping policy; wildcard compatibility is only for legacy tables."""

    ESCAPED = "escaped"
    LEGACY_TABLE_WILDCARDS = "legacy_table_wildcards"


type SearchScope = Literal["all_columns"] | tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ColumnSearchOrder:
    """Order by a resource-resolved column with a stable resource ID tie-breaker."""

    column: str
    direction: Literal["asc", "desc"] = "desc"

    def __post_init__(self) -> None:
        if not self.column or self.direction not in ("asc", "desc"):
            raise ValueError("Column ordering requires a column and asc/desc direction")


type SearchOrder = Literal["relevance"] | ColumnSearchOrder


@dataclass(frozen=True, slots=True)
class LexicalSearchOptions:
    """Explicit mode, column scope, and ordering for a lexical search adapter.

    Resource adapters resolve column names and append their stable ID tie-breaker.
    Relevance is descending and only available in full-text mode.
    """

    mode: SearchMode
    scope: SearchScope
    order: SearchOrder

    def __post_init__(self) -> None:
        if self.mode == SearchMode.LITERAL and self.order == "relevance":
            raise ValueError("Literal search requires column ordering")


@dataclass(frozen=True, slots=True)
class FullTextUnit:
    """Raw text to parse with PostgreSQL simple, preserving phrase boundaries."""

    text: str
    is_phrase: bool = False


class LexicalSearchErrorCode(StrEnum):
    """Availability failures that callers distinguish without parsing messages."""

    UNAVAILABLE = "search_unavailable"
    INDEXING = "search_indexing"
    CONFIGURATION_CHANGED = "search_configuration_changed"


class LexicalSearchError(TracecatException):
    """A typed lexical availability failure; query timeouts use the shared error."""

    def __init__(self, code: LexicalSearchErrorCode) -> None:
        self.code = code
        messages: dict[LexicalSearchErrorCode, str] = {
            LexicalSearchErrorCode.UNAVAILABLE: "Lexical search is unavailable.",
            LexicalSearchErrorCode.INDEXING: "Lexical search indexing is incomplete.",
            LexicalSearchErrorCode.CONFIGURATION_CHANGED: (
                "Lexical search configuration changed. Restart the search."
            ),
        }
        message = messages[code]
        super().__init__(message, detail={"code": code.value, "message": message})


@dataclass(frozen=True, slots=True)
class SearchMatchMetadata:
    """Bounded metadata carried beside resource data, never merged into a row.

    Excerpts are plain text, not trusted HTML. Scores are nonnegative relevance
    values, not probabilities. Column names identify resource-resolved fields.
    """

    score: float | None = None
    matched_columns: tuple[str, ...] = ()
    excerpts: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.score is not None and (not isfinite(self.score) or self.score < 0):
            raise ValueError("Search score must be finite and nonnegative")
        if len(self.matched_columns) > MAX_MATCHED_COLUMNS or any(
            not column or len(column) > MAX_MATCH_COLUMN_LENGTH
            for column in self.matched_columns
        ):
            raise ValueError("Search match columns exceed their bounds")
        if len(self.excerpts) > MAX_MATCH_EXCERPTS or any(
            len(excerpt) > MAX_MATCH_EXCERPT_LENGTH for excerpt in self.excerpts
        ):
            raise ValueError("Search match excerpts exceed their bounds")
