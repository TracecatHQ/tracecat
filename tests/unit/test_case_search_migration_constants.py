"""Runtime readiness constants must match the frozen case search migrations."""

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType

from tracecat.cases.text_search import _FUZZY_WORD, FUZZY_THRESHOLD
from tracecat.cases.text_search_indexes import (
    CASE_SEARCH_TRIGGER,
    CASE_SEARCH_TRIGGER_BODY_HASH,
    CASE_SEARCH_TRIGGER_FUNCTION,
    CASE_SEARCH_VECTOR_INDEXES,
    CASE_SEARCH_WORD_INDEXES,
    CASE_SEARCH_WORDS_TRIGGER,
    CASE_SEARCH_WORDS_TRIGGER_BODY_HASH,
    CASE_SEARCH_WORDS_TRIGGER_FUNCTION,
    CASE_TEXT_DOCUMENT_BYTES,
    CASE_TEXT_MATCH_BODY_HASH,
    CASE_TEXT_VECTOR_BODY_HASH,
    CASE_WORDS_BODY_HASH,
    CASE_WORDS_FUNCTION,
)

VERSIONS = Path(__file__).resolve().parents[2] / "alembic/versions"


def _load(filename: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        filename.removesuffix(".py"), VERSIONS / filename
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _md5(body: str) -> str:
    # Readiness hashes prosrc with surrounding whitespace removed.
    return hashlib.md5(body.strip().encode()).hexdigest()


def test_helper_hashes_match_search_migration() -> None:
    migration = _load("e2018a1b2c3d_add_case_text_search_indexes.py")
    assert _md5(migration._MATCH_BODY) == CASE_TEXT_MATCH_BODY_HASH
    assert _md5(migration._VECTOR_BODY) == CASE_TEXT_VECTOR_BODY_HASH


def test_stored_vector_constants_match_vector_migration() -> None:
    migration = _load("5ca140c06ec2_store_case_search_vectors.py")
    assert migration._TRIGGER == CASE_SEARCH_TRIGGER
    assert migration._TRIGGER_FUNCTION == CASE_SEARCH_TRIGGER_FUNCTION
    assert _md5(migration._TRIGGER_BODY) == CASE_SEARCH_TRIGGER_BODY_HASH
    assert dict(migration._VECTOR_INDEXES) == CASE_SEARCH_VECTOR_INDEXES


def test_stored_word_constants_match_word_migration() -> None:
    migration = _load("e4fd31674ae9_store_case_search_words.py")
    assert migration._TRIGGER == CASE_SEARCH_WORDS_TRIGGER
    assert migration._TRIGGER_FUNCTION == CASE_SEARCH_WORDS_TRIGGER_FUNCTION
    assert _md5(migration._TRIGGER_BODY) == CASE_SEARCH_WORDS_TRIGGER_BODY_HASH
    assert migration._WORDS_FUNCTION == CASE_WORDS_FUNCTION
    assert _md5(migration._WORDS_BODY) == CASE_WORDS_BODY_HASH
    # The vocabulary indexes are created with IF NOT EXISTS in the migration
    # and reported with an unqualified operator class by readiness.
    vocabulary = {
        statement.split()[5]: statement.replace("IF NOT EXISTS ", "").replace(
            "public.gin_trgm_ops", "gin_trgm_ops"
        )
        for statement in migration._TABLE_INDEXES
    }
    assert {**dict(migration._WORD_INDEXES), **vocabulary} == CASE_SEARCH_WORD_INDEXES


def test_stored_word_filters_cannot_drop_a_possible_typo_match() -> None:
    """Word lists omit words too dissimilar to match any fuzzy term.

    A term of n letters has at most n + 1 distinct trigrams. Strict word
    similarity is shared / (term + word - shared) trigrams, so a word with more
    than (n + 1) / threshold trigrams can never reach the threshold.
    """
    migration = _load("e4fd31674ae9_store_case_search_words.py")
    assert _FUZZY_WORD.pattern == "[a-zA-Z]{5,32}"
    assert int((32 + 1) / FUZZY_THRESHOLD) == 50
    assert "cardinality(public.show_trgm(w.word)) <= 50" in migration._WORDS_BODY
    assert f"octet_length(source_text) <= {CASE_TEXT_DOCUMENT_BYTES}" in (
        migration._WORDS_BODY
    )
