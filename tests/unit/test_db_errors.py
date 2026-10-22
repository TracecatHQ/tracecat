from asyncpg import ForeignKeyViolationError, UniqueViolationError
from sqlalchemy.exc import IntegrityError

from tracecat.db.errors import get_postgres_error, is_unique_violation


def unique_error(name: str) -> UniqueViolationError:
    return UniqueViolationError.new({"C": "23505", "n": name, "M": "duplicate"})


def test_unique_violation_matches_named_constraint() -> None:
    error = IntegrityError("statement", {}, unique_error("uq_synthetic_resource"))
    assert is_unique_violation(error, "uq_synthetic_resource")
    assert not is_unique_violation(error, "uq_other_resource")


def test_unique_violation_follows_driver_cause() -> None:
    original = unique_error("uq_synthetic_resource")
    adapter = RuntimeError("adapter")
    adapter.__cause__ = original
    error = IntegrityError("statement", {}, adapter)
    assert get_postgres_error(error, UniqueViolationError) is original
    assert is_unique_violation(error, "uq_synthetic_resource")


def test_constraint_name_in_message_does_not_classify_error() -> None:
    error = IntegrityError("statement", {}, RuntimeError("uq_synthetic_resource"))
    assert not is_unique_violation(error, "uq_synthetic_resource")


def test_foreign_key_violation_is_not_a_unique_conflict() -> None:
    error = IntegrityError("statement", {}, ForeignKeyViolationError("missing"))
    assert not is_unique_violation(error, "uq_synthetic_resource")


def test_exception_chain_handles_cycles_and_context() -> None:
    original = unique_error("uq_synthetic_resource")
    error = RuntimeError("outer")
    error.__context__ = original
    original.__cause__ = error
    assert is_unique_violation(error, "uq_synthetic_resource")
    assert get_postgres_error(error, ForeignKeyViolationError) is None
