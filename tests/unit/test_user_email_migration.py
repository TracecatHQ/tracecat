"""Case-insensitive email migration preflight and index behavior."""

import importlib.util
import threading
import time
import uuid
from collections.abc import Iterator
from pathlib import Path
from types import ModuleType

import pytest
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy import Connection, Engine, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG

pytestmark = pytest.mark.usefixtures("db")


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
def schema() -> Iterator[str]:
    """A bare ``"user"`` table in its own schema, without the ORM's indexes."""
    name = f"user_email_migration_{uuid.uuid4().hex[:12]}"
    admin = create_engine(TEST_DB_CONFIG.test_url_sync, poolclass=NullPool)
    with admin.begin() as conn:
        conn.exec_driver_sql(f'CREATE SCHEMA "{name}"')
        conn.exec_driver_sql(
            f'CREATE TABLE "{name}"."user" (id serial PRIMARY KEY, email text)'
        )
    try:
        yield name
    finally:
        with admin.begin() as conn:
            conn.exec_driver_sql(f'DROP SCHEMA "{name}" CASCADE')
        admin.dispose()


@pytest.fixture
def engine(schema: str) -> Iterator[Engine]:
    engine = create_engine(
        TEST_DB_CONFIG.test_url_sync,
        poolclass=NullPool,
        connect_args={"options": f"-csearch_path={schema} -clock_timeout=10s"},
    )
    yield engine
    engine.dispose()


@pytest.fixture
def connection(engine: Engine) -> Iterator[Connection]:
    with engine.begin() as conn:
        with Operations.context(MigrationContext.configure(conn)):
            yield conn


def _insert(conn: Connection, *emails: str) -> None:
    conn.execute(
        text('INSERT INTO "user" (email) VALUES (:email)'),
        [{"email": email} for email in emails],
    )


def _has_lower_index(conn: Connection, schema: str) -> bool:
    return bool(
        conn.execute(
            text(
                "SELECT count(*) FROM pg_indexes "
                "WHERE schemaname = :schema AND indexname = 'ix_user_email_lower'"
            ),
            {"schema": schema},
        ).scalar_one()
    )


def _assert_count_only_error(error: BaseException, groups: int) -> None:
    assert isinstance(error, RuntimeError)
    assert str(error) == (
        f"Found {groups} case-insensitive duplicate user email groups; resolve "
        "these before continuing. See revision 14588b612098 for restricted "
        "operator inspection instructions."
    )


def test_preflight_reports_only_duplicate_group_count(
    migration: ModuleType, connection: Connection, schema: str
) -> None:
    emails = [
        "First.Last@example.com",
        "first.last@example.com",
        "FIRST.LAST@example.com",
        "Other@example.com",
        "other@example.com",
        "unique@example.com",
    ]
    _insert(connection, *emails)

    with pytest.raises(RuntimeError) as exc:
        migration.upgrade()

    _assert_count_only_error(exc.value, groups=2)
    assert "@" not in str(exc.value)
    assert (
        connection.exec_driver_sql('SELECT email FROM "user" ORDER BY id')
        .scalars()
        .all()
        == emails
    )
    assert not _has_lower_index(connection, schema)


@pytest.mark.parametrize(
    "emails", [[], ["First.Last@example.com", "other@example.com"]]
)
def test_upgrade_and_downgrade_without_duplicates(
    migration: ModuleType, connection: Connection, schema: str, emails: list[str]
) -> None:
    if emails:
        _insert(connection, *emails)
    migration.upgrade()
    assert _has_lower_index(connection, schema)

    _insert(connection, "Case.Test@example.com")
    with pytest.raises(IntegrityError):
        with connection.begin_nested():
            _insert(connection, "case.test@example.com")

    migration.downgrade()
    assert not _has_lower_index(connection, schema)
    _insert(connection, "case.test@example.com")


def test_duplicate_committed_during_upgrade_is_reported_as_a_count(
    migration: ModuleType, engine: Engine
) -> None:
    """A case variant inserted while the upgrade starts never reaches the index
    build, whose error would include the email."""
    with engine.begin() as conn:
        _insert(conn, "first.last@example.com")

    writer = engine.connect()
    writer.begin()
    _insert(writer, "First.Last@example.com")

    migrator_pid: list[int] = []
    errors: list[BaseException] = []

    def upgrade() -> None:
        try:
            with engine.begin() as conn:
                migrator_pid.append(
                    conn.exec_driver_sql("SELECT pg_backend_pid()").scalar_one()
                )
                with Operations.context(MigrationContext.configure(conn)):
                    migration.upgrade()
        except BaseException as e:  # noqa: BLE001 - surfaced to the test thread
            errors.append(e)

    thread = threading.Thread(target=upgrade)
    thread.start()
    try:
        # Commit the duplicate only once the upgrade waits on the writer's lock.
        deadline = time.monotonic() + 10
        with engine.connect() as probe:
            while time.monotonic() < deadline:
                waiting = (
                    migrator_pid
                    and probe.execute(
                        text(
                            "SELECT wait_event_type = 'Lock' FROM pg_stat_activity "
                            "WHERE pid = :pid"
                        ),
                        {"pid": migrator_pid[0]},
                    ).scalar_one_or_none()
                )
                if waiting:
                    break
                time.sleep(0.05)
            else:
                pytest.fail("Upgrade never waited on the uncommitted insert")
        writer.commit()
    finally:
        writer.close()
        thread.join(timeout=15)

    assert not thread.is_alive()
    assert len(errors) == 1
    _assert_count_only_error(errors[0], groups=1)
