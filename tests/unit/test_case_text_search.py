"""Bounded query semantics, SQL composition, and plain-text excerpts."""

import uuid
from unittest.mock import AsyncMock

import pytest
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from tracecat.cases.text_search import (
    MAX_EXCERPT_LENGTH,
    STATEMENT_TIMEOUT_MS,
    build_case_text_expressions,
    case_search_excerpt,
    case_search_execution_context,
    get_case_search_excerpts,
    parse_case_text_query,
)
from tracecat.db.models import Case
from tracecat.query.errors import TracecatQueryTimeoutError
from tracecat.query.lexical_types import SearchMode


@pytest.mark.parametrize(
    "value",
    [
        "",
        "  ",
        "x" * 1001,
        "bad\x00query",
        'powershell ""',
        '"unclosed',
        '"powershell"encoded',
        'powershell"encoded',
        "|",
        "word " * 33,
    ],
)
def test_rejects_malformed_or_excessive_shared_queries(value: str) -> None:
    with pytest.raises(ValueError):
        parse_case_text_query(value)


def test_operator_words_and_short_words_are_required_text() -> None:
    query = parse_case_text_query("powershell OR -encoded word:* field:value on")
    assert [clause.value for clause in query.clauses] == [
        "powershell",
        "OR",
        "-encoded",
        "word:*",
        "field:value",
        "on",
    ]
    assert all(clause.kind == "word" for clause in query.clauses)
    assert parse_case_text_query("ps").clauses[0].fuzzy is False
    assert parse_case_text_query("literal:powershell").clauses[0].kind == "word"


def test_punctuation_only_words_are_ignored_in_full_text() -> None:
    query = parse_case_text_query("powershell - encoded")
    assert [clause.value for clause in query.clauses] == ["powershell", "encoded"]
    with pytest.raises(ValueError, match="letter or number"):
        parse_case_text_query("-")


@pytest.mark.parametrize("value", ["", "on", "  a  "])
def test_explicit_literal_requires_shared_minimum_length(value: str) -> None:
    with pytest.raises(ValueError):
        parse_case_text_query(value, mode=SearchMode.LITERAL)


def test_literal_preserves_original_spaces_and_punctuation() -> None:
    query = parse_case_text_query(" %__ ", mode=SearchMode.LITERAL)
    assert query.text == " %__ "
    assert query.clauses[0].value == " %__ "


def test_words_phrases_indicators_and_fuzzy_budget() -> None:
    query = parse_case_text_query(
        'powershel "encoded command" malware incident 192.0.2.10'
    )
    assert [(clause.kind, clause.fuzzy) for clause in query.clauses] == [
        ("word", True),
        ("phrase", False),
        ("word", True),
        ("word", False),
        ("word", False),
    ]
    assert parse_case_text_query('"192.0.2.10"').clauses[0].kind == "phrase"
    assert parse_case_text_query("a" * 64).clauses[0].fuzzy is False
    assert parse_case_text_query("a" * 64).clauses[0].kind == "word"
    assert parse_case_text_query("0.0.0.0").clauses[0].kind == "word"
    assert parse_case_text_query("a3" * 32).clauses[0].kind == "word"


@pytest.mark.parametrize("value", ["CASE-0001", "case-1"])
def test_identifiers_match_exact_columns_and_indexed_text_mentions(value: str) -> None:
    expression = build_case_text_expressions(parse_case_text_query(value))
    statement = sa.select(Case.id).where(expression.predicate)
    sql = str(statement.compile(dialect=postgresql.dialect()))
    assert "case_number =" in sql
    assert "LIKE" not in sql
    assert '"case".summary_tsv' in sql
    assert "tracecat_case_safe_vector_v1" not in sql
    assert "@@" in sql


def test_uuid_is_exact_and_large_case_numbers_are_rejected() -> None:
    query = parse_case_text_query("00000000-0000-4000-8000-000000000001")
    assert query.case_id is not None
    with pytest.raises(ValueError, match="Short ID number"):
        parse_case_text_query("CASE-9999999999")
    assert parse_case_text_query("1").case_number is None


@pytest.mark.parametrize("length", [32, 40, 64, 96, 128])
def test_hex_hashes_remain_literal_even_without_digits(length: int) -> None:
    value = "ab" * (length // 2)
    for term in (value, f'"{value}"'):
        query = parse_case_text_query(term)
        assert query.case_id is None
        assert query.clauses[0].kind in {"word", "phrase"}
        assert query.clauses[0].fuzzy is False


@pytest.mark.parametrize(
    "value",
    [
        "powershell on host",
        'powershell "on" host',
        '"on powershell" host',
        '"on to" powershell',
    ],
)
def test_short_exact_words_are_allowed_with_a_selective_clause(value: str) -> None:
    query = parse_case_text_query(value)
    short = next(clause for clause in query.clauses if "on" in clause.value.split())
    assert short.kind in {"word", "phrase"}
    assert short.fuzzy is False


def test_phrase_sql_uses_separate_source_vectors() -> None:
    expression = build_case_text_expressions(
        parse_case_text_query('"powershell encoded"')
    )
    sql = str(
        sa.select(Case.id)
        .where(expression.predicate)
        .compile(dialect=postgresql.dialect())
    )
    assert sql.count("phraseto_tsquery(") == 2
    assert '"case".summary_tsv @@ phraseto_tsquery(' in sql
    assert '"case".description_tsv @@ phraseto_tsquery(' in sql
    assert "pg_catalog.simple" in sql
    assert "||" not in sql
    assert "%>>" not in sql


def test_fuzzy_operator_matches_index_expression_and_literals_are_bound() -> None:
    expression = build_case_text_expressions(parse_case_text_query("powershel"))
    sql = str(
        sa.select(Case.id)
        .where(expression.predicate)
        .compile(dialect=postgresql.dialect())
    )
    # psycopg doubles percent signs in compiled SQL, preserving the %>> operator.
    assert "lower(CASE WHEN" in sql
    assert 'CAST("case".summary AS TEXT)' in sql
    assert "%%>>" in sql
    assert "EXISTS (SELECT" in sql
    assert "strict_word_similarity(" in sql
    literal = build_case_text_expressions(
        parse_case_text_query("abc%_\\' OR true", mode=SearchMode.LITERAL)
    )
    compiled = (
        sa.select(Case.id)
        .where(literal.predicate)
        .compile(dialect=postgresql.dialect())
    )
    assert "abc" not in str(compiled)
    assert "%abc\\%\\_\\\\' OR true%" in compiled.params.values()


def test_excerpt_is_plain_bounded_and_near_a_match() -> None:
    excerpt = case_search_excerpt(
        "description",
        "<p>"
        + "padding " * 800
        + "<b>NATIVE_STARTPowerShellNATIVE_STOP</b> &amp; alert</p>"
        + " tail" * 80,
        start_marker="NATIVE_START",
        stop_marker="NATIVE_STOP",
    )
    assert excerpt.field == "description"
    assert "PowerShell & alert" in excerpt.text
    assert "<b>" not in excerpt.text
    assert len(excerpt.text) <= MAX_EXCERPT_LENGTH
    assert excerpt.text.startswith("…")
    assert excerpt.text.endswith("…")


@pytest.mark.anyio
async def test_excerpt_batch_skips_empty_inputs_and_bounds_returned_ids() -> None:
    session = AsyncMock()
    query = parse_case_text_query("powershell")
    workspace = uuid.UUID("00000000-0000-4000-8000-000000000001")
    assert (
        await get_case_search_excerpts(
            session, workspace_id=workspace, case_ids=[], query=query
        )
        == {}
    )
    with pytest.raises(ValueError, match="one returned case page"):
        await get_case_search_excerpts(
            session, workspace_id=workspace, case_ids=[workspace] * 201, query=query
        )
    session.execute.assert_not_awaited()


@pytest.mark.anyio
async def test_execution_settings_are_transaction_local_and_restored() -> None:
    session = AsyncMock()
    session.scalar.side_effect = ["0", "0.5"]
    async with case_search_execution_context(session):
        pass
    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}" in statements
    parameters = [
        call.args[1] for call in session.execute.await_args_list if len(call.args) > 1
    ]
    assert {"threshold": "0.65"} in parameters
    assert {"threshold": "0.5"} in parameters
    assert {"timeout": "0"} in parameters


@pytest.mark.anyio
async def test_nonfuzzy_execution_keeps_timeout_without_trigram_settings() -> None:
    session = AsyncMock()
    session.scalar.return_value = "0"
    async with case_search_execution_context(session, use_trigram_threshold=False):
        pass
    assert [str(call.args[0]) for call in session.scalar.await_args_list] == [
        "SHOW statement_timeout"
    ]
    assert [str(call.args[0]) for call in session.execute.await_args_list] == [
        f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}",
        "SELECT set_config('statement_timeout', :timeout, true)",
    ]


@pytest.mark.anyio
async def test_nested_fuzzy_execution_changes_and_restores_threshold_once() -> None:
    session = AsyncMock()
    session.scalar.side_effect = ["0", "0.5", "1500ms", "0.65"]
    async with case_search_execution_context(session):
        async with case_search_execution_context(session):
            pass
    statements = [str(call.args[0]) for call in session.execute.await_args_list]
    assert statements.count("SELECT public.strict_word_similarity('', '')") == 2
    threshold_parameters = [
        call.args[1]
        for call in session.execute.await_args_list
        if "set_config('pg_trgm.strict_word_similarity_threshold'" in str(call.args[0])
    ]
    assert threshold_parameters == [{"threshold": "0.65"}, {"threshold": "0.5"}]


@pytest.mark.anyio
async def test_timeout_message_is_specific_to_case_search_and_retains_error_code() -> (
    None
):
    session = AsyncMock()
    session.scalar.side_effect = ["0", "0.5"]
    with pytest.raises(
        TracecatQueryTimeoutError, match="Case search timed out"
    ) as error:
        async with case_search_execution_context(session):
            raise TracecatQueryTimeoutError()
    assert error.value.code.value == "query_timeout"


@pytest.mark.anyio
async def test_failure_leaves_setting_cleanup_to_transaction_rollback() -> None:
    session = AsyncMock()
    session.scalar.side_effect = ["0", "0.5"]
    with pytest.raises(RuntimeError):
        async with case_search_execution_context(session):
            raise RuntimeError("synthetic failure")
    assert session.execute.await_count == 3
