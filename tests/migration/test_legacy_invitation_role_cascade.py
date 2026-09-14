"""Verify legacy role deletion and restoration of the previous FK policy."""

import importlib.util
from pathlib import Path

import pytest
import sqlalchemy as sa
from alembic.migration import MigrationContext
from alembic.operations import Operations
from sqlalchemy.exc import IntegrityError
from sqlalchemy.pool import NullPool

from tests.database import TEST_DB_CONFIG


@pytest.fixture(scope="session")
def default_org() -> None:
    """This migration test only uses connection-local temporary tables."""


@pytest.fixture(scope="session")
def workflow_bucket() -> None:
    """No object storage is needed."""


@pytest.fixture
def clean_redis_db() -> None:
    """No Redis is needed."""


def test_role_deletion_cascades_legacy_invitations_and_restores_restrict() -> None:
    path = (
        Path(__file__).parents[2]
        / "alembic/versions/b27520297564_cascade_legacy_invitations_when_.py"
    )
    spec = importlib.util.spec_from_file_location("legacy_invitation_cascade", path)
    assert spec is not None and spec.loader is not None
    migration = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(migration)
    engine = sa.create_engine(TEST_DB_CONFIG.sys_url_sync, poolclass=NullPool)
    try:
        with engine.begin() as conn:
            conn.execute(sa.text("CREATE TEMP TABLE role (id integer PRIMARY KEY)"))
            conn.execute(
                sa.text("""
                    CREATE TEMP TABLE organization_invitation (
                        id integer PRIMARY KEY,
                        role_id integer,
                        CONSTRAINT fk_organization_invitation_role_id_role
                            FOREIGN KEY (role_id) REFERENCES role(id) ON DELETE RESTRICT
                    )
                """)
            )
            conn.execute(sa.text("INSERT INTO role VALUES (1), (2)"))
            conn.execute(
                sa.text(
                    "INSERT INTO organization_invitation VALUES (10, 1), (20, 2), (30, NULL)"
                )
            )
            with Operations.context(MigrationContext.configure(conn)):
                migration.upgrade()
                assert (
                    conn.scalar(sa.text("SELECT count(*) FROM organization_invitation"))
                    == 3
                )
                conn.execute(sa.text("DELETE FROM role WHERE id = 1"))
                assert conn.execute(
                    sa.text("SELECT id FROM organization_invitation ORDER BY id")
                ).scalars().all() == [20, 30]

                migration.downgrade()
                with pytest.raises(IntegrityError), conn.begin_nested():
                    conn.execute(sa.text("DELETE FROM role WHERE id = 2"))
                assert (
                    conn.scalar(sa.text("SELECT count(*) FROM organization_invitation"))
                    == 2
                )

                migration.upgrade()
                conn.execute(sa.text("DELETE FROM role WHERE id = 2"))
                assert conn.execute(
                    sa.text("SELECT id FROM organization_invitation")
                ).scalars().all() == [30]
    finally:
        engine.dispose()
