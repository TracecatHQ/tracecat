"""Runtime readiness constants must match the frozen case search migrations."""

import hashlib
import importlib.util
from pathlib import Path
from types import ModuleType

from tracecat.cases.text_search_indexes import (
    CASE_SEARCH_TRIGGER,
    CASE_SEARCH_TRIGGER_BODY_HASH,
    CASE_SEARCH_TRIGGER_FUNCTION,
    CASE_SEARCH_VECTOR_INDEXES,
    CASE_TEXT_MATCH_BODY_HASH,
    CASE_TEXT_VECTOR_BODY_HASH,
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
