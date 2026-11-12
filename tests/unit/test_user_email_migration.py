"""Case-insensitive email migration preflight and index behavior."""

import importlib.util
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, create_engine, text
from sqlalchemy.exc import IntegrityError


@pytest.fixture
def migration() -> ModuleType:
    path = (
        Path(__file__).resolve().parents[2]
        / "alembic/versions/14588b612098_enforce_case_insensitive_user_email_.py"
    )
    spec = importlib.util.spec_from_file_location("user_email_migration", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def connection() -> Iterator[Connection]:
    engine = create_engine("sqlite+pysqlite:///:memory:")
    with engine.begin() as conn:
        conn.exec_driver_sql('CREATE TABLE "user" (id INTEGER PRIMARY KEY, email TEXT)')
        with Operations.context(MigrationContext.configure(conn)):
            yield conn
    engine.dispose()


def test_preflight_reports_only_duplicate_group_count(
    migration: ModuleType, connection: Connection
) -> None:
    emails = [
        "First.Last@example.com",
        "first.last@example.com",
        "FIRST.LAST@example.com",
        "Other@example.com",
        "other@example.com",
        "unique@example.com",
    ]
    connection.execute(
        text('INSERT INTO "user" (email) VALUES (:email)'),
        [{"email": email} for email in emails],
    )

    with pytest.raises(RuntimeError) as exc:
        migration.upgrade()

    assert str(exc.value) == (
        "Found 2 case-insensitive duplicate user email groups; resolve these "
        "before continuing. See revision 14588b612098 for restricted operator "
        "inspection instructions."
    )
    for email in emails:
        assert email not in str(exc.value)
    assert "@" not in str(exc.value)
    assert (
        connection.exec_driver_sql('SELECT email FROM "user" ORDER BY id')
        .scalars()
        .all()
        == emails
    )
    assert (
        connection.exec_driver_sql(
            "SELECT count(*) FROM sqlite_master WHERE name = 'ix_user_email_lower'"
        ).scalar_one()
        == 0
    )


@pytest.mark.parametrize(
    "emails", [[], ["First.Last@example.com", "other@example.com"]]
)
def test_upgrade_and_downgrade_without_duplicates(
    migration: ModuleType, connection: Connection, emails: list[str]
) -> None:
    for email in emails:
        connection.execute(
            text('INSERT INTO "user" (email) VALUES (:email)'), {"email": email}
        )
    migration.upgrade()

    connection.exec_driver_sql(
        "INSERT INTO \"user\" (email) VALUES ('Case.Test@example.com')"
    )
    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            connection.exec_driver_sql(
                "INSERT INTO \"user\" (email) VALUES ('case.test@example.com')"
            )

    migration.downgrade()
    connection.exec_driver_sql(
        "INSERT INTO \"user\" (email) VALUES ('case.test@example.com')"
    )
