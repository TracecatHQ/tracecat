"""Bounded lexical input parsing; PostgreSQL owns tokenization and matching."""

from collections.abc import Sequence

from tracecat.query.lexical_types import (
    MAX_SEARCH_TERMS,
    MAX_SEARCH_TEXT_LENGTH,
    MIN_LITERAL_LENGTH,
    FullTextUnit,
    LiteralMatchPolicy,
    SearchScope,
)


def _validate_text(text: str) -> None:
    if len(text) > MAX_SEARCH_TEXT_LENGTH:
        raise ValueError(
            f"Search text cannot exceed {MAX_SEARCH_TEXT_LENGTH} characters"
        )
    if "\x00" in text:
        raise ValueError("Search text cannot contain null bytes")


def parse_full_text_query(text: str) -> tuple[FullTextUnit, ...]:
    """Parse required terms and per-value phrases without interpreting operators.

    Units without a letter or number (e.g. ``-``) are skipped. Only double
    quotes and backslashes can be escaped. Other backslashes remain literal.
    PostgreSQL must parse each unit with ``pg_catalog.simple`` and
    reject units producing no lexemes; this parser does not predict lexemes.

    Args:
        text: User input, limited by Unicode character and whitespace-term count.

    Returns:
        Raw units which must all match; phrases must each match one value.
        Punctuation-only units are omitted.

    Raises:
        ValueError: If input is empty, punctuation-only, malformed, or too large.
    """
    _validate_text(text)
    units: list[FullTextUnit] = []
    term_count = 0
    has_input = False
    position = 0
    while position < len(text):
        if text[position].isspace():
            position += 1
            continue
        is_phrase = text[position] == '"'
        if is_phrase:
            position += 1
        characters: list[str] = []
        while position < len(text):
            character = text[position]
            if character == "\\" and position + 1 < len(text):
                following = text[position + 1]
                if following in ('"', "\\"):
                    characters.append(following)
                    position += 2
                    continue
            if character == '"':
                if not is_phrase:
                    raise ValueError("Quoted phrases must be separated by whitespace")
                position += 1
                if position < len(text) and not text[position].isspace():
                    raise ValueError("Quoted phrases must be separated by whitespace")
                break
            if character.isspace() and not is_phrase:
                break
            characters.append(character)
            position += 1
        else:
            if is_phrase:
                raise ValueError("Quoted phrase is missing its closing quote")
        unit_text = "".join(characters)
        if is_phrase and not unit_text:
            raise ValueError("Quoted phrases cannot be empty")
        has_input = has_input or bool(unit_text)
        # PostgreSQL drops punctuation-only words such as "-" from tsqueries, so
        # skip them here too. This is a cheap guard, not a replacement for the
        # locale-dependent parser or its final empty-tsquery check.
        if not any(character.isalnum() for character in unit_text):
            continue
        term_count += len(unit_text.split())
        if term_count > MAX_SEARCH_TERMS:
            raise ValueError(f"Search text cannot exceed {MAX_SEARCH_TERMS} terms")
        units.append(FullTextUnit(text=unit_text, is_phrase=is_phrase))
    if not units:
        if has_input:
            raise ValueError(
                "Full-text search must contain a letter or number. "
                "Use literal search for punctuation."
            )
        raise ValueError("Full-text search requires nonempty input")
    return tuple(units)


def literal_search_pattern(
    text: str, *, policy: LiteralMatchPolicy = LiteralMatchPolicy.ESCAPED
) -> str | None:
    """Build a bound ILIKE pattern using an explicit literal compatibility policy.

    New explicit literal searches require three characters after trimming. Legacy
    omitted-mode table searches retain empty-input and SQL wildcard behavior.
    Case literal callers use escaping; they must never select the table policy.
    Adapters must pass the result as a SQL parameter and use ``ESCAPE '\\'``.

    Args:
        text: Substring to search, preserved including leading/trailing spaces.
        policy: Escaped literal matching, or omitted-mode table compatibility.

    Returns:
        A contains pattern, or None for an empty legacy table search.

    Raises:
        ValueError: If text exceeds limits or an explicit literal is too short.
    """
    _validate_text(text)
    if policy == LiteralMatchPolicy.LEGACY_TABLE_WILDCARDS:
        return f"%{text}%" if text else None
    if len(text.strip()) < MIN_LITERAL_LENGTH:
        raise ValueError(
            f"Literal search requires at least {MIN_LITERAL_LENGTH} characters"
        )
    escaped = text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def resolve_search_columns(
    scope: SearchScope, searchable_columns: Sequence[str]
) -> tuple[str, ...]:
    """Resolve a scope against authorized, canonical searchable column names.

    Resource adapters resolve aliases and build the allowed column collection;
    unknown or nonsearchable selected columns fail instead of widening scope.

    Args:
        scope: All columns, or the explicitly selected column names.
        searchable_columns: Columns the adapter permits for this lexical mode.

    Returns:
        Canonical column names in adapter order for ``all_columns``, or in the
        requested order for an explicit selection. The tuple is nonempty.

    Raises:
        ValueError: If scope is empty, duplicated, or contains invalid columns.
    """
    columns = tuple(searchable_columns) if scope == "all_columns" else scope
    if not columns:
        raise ValueError("Search requires at least one searchable column")
    if len(set(columns)) != len(columns):
        raise ValueError("Search columns must be distinct")
    if any(not column or column not in searchable_columns for column in columns):
        raise ValueError("Search column is missing or does not support lexical search")
    return columns
