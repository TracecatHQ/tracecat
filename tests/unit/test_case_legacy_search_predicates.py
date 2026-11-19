"""The omitted-mode substring search keeps ILIKE matches while using indexes."""

import pytest
from sqlalchemy.dialects import postgresql

from tracecat.cases.search_schemas import CaseQuery
from tracecat.cases.search_service import (
    _may_occur_in_short_id,
    case_identity_predicates,
)

SHORT_IDS = ("CASE-0042", "CASE-12345", "CASE-0007")


def test_every_part_of_a_short_id_keeps_the_short_id_condition() -> None:
    """Dropping the condition is only safe when no short ID can contain the term."""
    parts = {
        short_id[start:end].swapcase() if start % 2 else short_id[start:end]
        for short_id in SHORT_IDS
        for start in range(len(short_id))
        for end in range(start + 1, len(short_id) + 1)
    }
    assert [part for part in sorted(parts) if not _may_occur_in_short_id(part)] == []
    # And each one keeps the formatted short-ID condition in the predicate.
    assert [part for part in sorted(parts) if "concat(" not in _sql(part)] == []


@pytest.mark.parametrize(
    "term", ["lsass", "powershell.exe", "case-x", "42a", " 42", "CASE-00-1", "cas e"]
)
def test_terms_outside_any_short_id_drop_the_condition(term: str) -> None:
    assert not _may_occur_in_short_id(term)
    assert "concat(" not in _sql(term)


def test_unicode_digits_are_not_short_id_digits() -> None:
    assert not _may_occur_in_short_id("\u0664\u0662")  # Arabic-Indic 42


def _sql(term: str) -> str:
    (predicate,) = case_identity_predicates(CaseQuery(search_term=term))
    return str(predicate.compile(dialect=postgresql.dialect()))


def test_words_use_the_trigram_expressions_without_the_short_id() -> None:
    sql = _sql("lsass")
    assert "concat(" not in sql
    assert "ILIKE" not in sql
    # The guarded lower-text expression that ix_case_*_trgm indexes.
    assert "lower(CASE WHEN (octet_length(coalesce(CAST" in sql


def test_short_terms_and_id_fragments_keep_their_conditions() -> None:
    assert "ILIKE" not in _sql("ab")
    assert "concat(" in _sql("CASE-004")
    assert "concat(" in _sql("42")
