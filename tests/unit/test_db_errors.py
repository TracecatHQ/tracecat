"""Constraint matching against errors raised by a real PostgreSQL connection."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat.db.errors import is_data_error, is_unique_violation

pytestmark = pytest.mark.usefixtures("db")


async def _raise_from(
    session: AsyncSession, statement: str, value: str | None
) -> DBAPIError:
    with pytest.raises(DBAPIError) as exc_info:
        async with session.begin_nested():
            await session.execute(text(statement), {"v": value})
    return exc_info.value


@pytest.fixture
async def probe_table(session: AsyncSession) -> AsyncSession:
    await session.execute(
        text(
            "CREATE TEMP TABLE db_errors_probe ("
            " v varchar(3) CONSTRAINT uq_db_errors_probe_v UNIQUE,"
            " w varchar(3) NOT NULL DEFAULT 'x')"
        )
    )
    await session.execute(text("INSERT INTO db_errors_probe (v) VALUES ('a')"))
    return session


@pytest.mark.anyio
async def test_unique_violation_matches_only_the_named_constraint(
    probe_table: AsyncSession,
) -> None:
    error = await _raise_from(
        probe_table, "INSERT INTO db_errors_probe (v) VALUES (:v)", "a"
    )
    assert isinstance(error, IntegrityError)
    assert is_unique_violation(error, "uq_db_errors_probe_v")
    assert not is_unique_violation(error, "uq_some_other_constraint")
    assert not is_data_error(error)


@pytest.mark.anyio
async def test_other_integrity_errors_are_not_unique_violations(
    probe_table: AsyncSession,
) -> None:
    error = await _raise_from(
        probe_table, "INSERT INTO db_errors_probe (v, w) VALUES ('b', :v)", None
    )
    assert isinstance(error, IntegrityError)
    assert not is_unique_violation(error, "uq_db_errors_probe_v")


@pytest.mark.anyio
@pytest.mark.parametrize(
    "value",
    ["a\x00b", "a\ud800", "abcd"],
    ids=["null-byte", "surrogate", "overlong"],
)
async def test_unstorable_text_is_a_data_error(
    probe_table: AsyncSession, value: str
) -> None:
    error = await _raise_from(
        probe_table, "INSERT INTO db_errors_probe (v) VALUES (:v)", value
    )
    assert is_data_error(error)
    assert not is_unique_violation(error, "uq_db_errors_probe_v")


@pytest.mark.anyio
async def test_server_side_data_exceptions_are_not_client_errors(
    probe_table: AsyncSession,
) -> None:
    error = await _raise_from(probe_table, "SELECT 1 / length(:v)", "")
    assert not is_data_error(error)
