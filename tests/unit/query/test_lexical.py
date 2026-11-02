import pytest

from tracecat.query.errors import QueryErrorCode, TracecatQueryTimeoutError
from tracecat.query.lexical import (
    literal_search_pattern,
    parse_full_text_query,
    resolve_search_columns,
)
from tracecat.query.lexical_types import (
    MAX_MATCH_COLUMN_LENGTH,
    MAX_MATCH_EXCERPT_LENGTH,
    MAX_MATCH_EXCERPTS,
    MAX_MATCHED_COLUMNS,
    MAX_SEARCH_TERMS,
    MAX_SEARCH_TEXT_LENGTH,
    TEXT_SEARCH_CONFIG,
    ColumnSearchOrder,
    FullTextUnit,
    LexicalSearchError,
    LexicalSearchErrorCode,
    LexicalSearchOptions,
    LiteralMatchPolicy,
    SearchMatchMetadata,
    SearchMode,
    SearchScope,
)


def test_full_text_preserves_raw_units_and_phrase_boundaries() -> None:
    assert parse_full_text_query(
        '  café\tHOST_name example.test 192.0.2.1 abc123 "red fox" '
    ) == (
        FullTextUnit("café"),
        FullTextUnit("HOST_name"),
        FullTextUnit("example.test"),
        FullTextUnit("192.0.2.1"),
        FullTextUnit("abc123"),
        FullTextUnit("red fox", is_phrase=True),
    )
    assert TEXT_SEARCH_CONFIG == "pg_catalog.simple"


def test_full_text_escapes_and_unsupported_operators_are_plain_text() -> None:
    assert parse_full_text_query(r'"say \"hello\"" path\\file path\file foo\"bar') == (
        FullTextUnit('say "hello"', is_phrase=True),
        FullTextUnit("path\\file"),
        FullTextUnit("path\\file"),
        FullTextUnit('foo"bar'),
    )
    assert parse_full_text_query("alpha OR -beta gamma:* field:value") == tuple(
        FullTextUnit(text)
        for text in ("alpha", "OR", "-beta", "gamma:*", "field:value")
    )


@pytest.mark.parametrize(
    "text",
    ["", " \t", "...", "- |", '""', 'a ""', '"unterminated', '"red"fox', 'red"fox"'],
)
def test_full_text_rejects_empty_or_malformed_units(text: str) -> None:
    with pytest.raises(ValueError):
        parse_full_text_query(text)


def test_full_text_skips_punctuation_only_units() -> None:
    assert parse_full_text_query('alpha - beta | "--" -gamma') == (
        FullTextUnit("alpha"),
        FullTextUnit("beta"),
        FullTextUnit("-gamma"),
    )
    with pytest.raises(ValueError, match="letter or number"):
        parse_full_text_query("-")


def test_query_limits_include_words_inside_phrases() -> None:
    assert len(parse_full_text_query(" ".join(["word"] * MAX_SEARCH_TERMS))) == (
        MAX_SEARCH_TERMS
    )
    assert parse_full_text_query("a" * MAX_SEARCH_TEXT_LENGTH)
    for text in (
        "a" * (MAX_SEARCH_TEXT_LENGTH + 1),
        "\x00word",
        " ".join(["word"] * (MAX_SEARCH_TERMS + 1)),
        '"' + " ".join(["word"] * (MAX_SEARCH_TERMS + 1)) + '"',
    ):
        with pytest.raises(ValueError):
            parse_full_text_query(text)


def test_literal_patterns_keep_table_wildcard_policy_separate() -> None:
    text = r"50%_done\file"
    assert literal_search_pattern(text) == r"%50\%\_done\\file%"
    assert (
        literal_search_pattern(text, policy=LiteralMatchPolicy.LEGACY_TABLE_WILDCARDS)
        == f"%{text}%"
    )
    assert (
        literal_search_pattern("", policy=LiteralMatchPolicy.LEGACY_TABLE_WILDCARDS)
        is None
    )
    assert literal_search_pattern(
        "_", policy=LiteralMatchPolicy.LEGACY_TABLE_WILDCARDS
    ) == ("%_%")
    assert literal_search_pattern("  abc  ") == "%  abc  %"
    assert literal_search_pattern("%%%") == r"%\%\%\%%"


@pytest.mark.parametrize("text", ["", "ab", " \t ", "a" * 1_001, "abc\x00"])
def test_explicit_literal_rejects_short_or_unbounded_queries(text: str) -> None:
    with pytest.raises(ValueError):
        literal_search_pattern(text)


def test_scope_selection_and_explicit_order() -> None:
    columns = ("summary", "details", "labels")
    assert resolve_search_columns("all_columns", columns) == columns
    assert resolve_search_columns(("details", "summary"), columns) == (
        "details",
        "summary",
    )
    assert (
        LexicalSearchOptions(SearchMode.FULL_TEXT, "all_columns", "relevance").order
        == "relevance"
    )
    assert LexicalSearchOptions(
        SearchMode.LITERAL, ("summary",), ColumnSearchOrder("created_at")
    ).order == ColumnSearchOrder("created_at", "desc")
    with pytest.raises(ValueError, match="column ordering"):
        LexicalSearchOptions(SearchMode.LITERAL, "all_columns", "relevance")


@pytest.mark.parametrize(
    ("scope", "columns"),
    [
        ("all_columns", ()),
        ((), ("summary",)),
        (("summary", "summary"), ("summary",)),
        (("missing_or_numeric",), ("summary",)),
    ],
)
def test_scope_never_silently_widens(
    scope: SearchScope, columns: tuple[str, ...]
) -> None:
    with pytest.raises(ValueError):
        resolve_search_columns(scope, columns)


def test_match_metadata_is_bounded_plain_text() -> None:
    metadata = SearchMatchMetadata(1.0, ("summary",), ("<untrusted> text",))
    assert metadata.excerpts == ("<untrusted> text",)
    for score in (float("nan"), float("inf"), -1.0):
        with pytest.raises(ValueError):
            SearchMatchMetadata(score=score)
    for columns in (
        ("summary",) * (MAX_MATCHED_COLUMNS + 1),
        ("a" * (MAX_MATCH_COLUMN_LENGTH + 1),),
    ):
        with pytest.raises(ValueError):
            SearchMatchMetadata(matched_columns=columns)
    for excerpts in (
        ("text",) * (MAX_MATCH_EXCERPTS + 1),
        ("a" * (MAX_MATCH_EXCERPT_LENGTH + 1),),
    ):
        with pytest.raises(ValueError):
            SearchMatchMetadata(excerpts=excerpts)


def test_availability_errors_have_codes_and_reuse_query_timeout() -> None:
    for code in LexicalSearchErrorCode:
        error = LexicalSearchError(code)
        assert error.code == code
        assert error.detail is not None
        assert error.detail["code"] == code.value
    assert TracecatQueryTimeoutError().code == QueryErrorCode.TIMEOUT
