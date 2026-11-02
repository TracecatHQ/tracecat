"""Exercise membership RLS as a non-owner role, including legacy NULL rows."""

import uuid
from collections.abc import Iterator

import psycopg
import pytest
from psycopg import sql

from tests.database import TEST_DB_CONFIG
from tracecat.db.tenant_rls import (
    disable_org_table_rls,
    enable_group_member_table_rls,
)


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    yield


@pytest.mark.usefixtures("db")
def test_group_member_policy_isolates_reads_and_writes() -> None:
    schema = f"rls_test_{uuid.uuid4().hex}"
    role = f"rls_test_{uuid.uuid4().hex}"
    org_a, org_b, group_a, group_b, user = (uuid.uuid4() for _ in range(5))
    with psycopg.connect(TEST_DB_CONFIG.test_url_sync.replace("+psycopg", "")) as conn:
        try:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(schema)))
            conn.execute(
                sql.SQL("SET LOCAL search_path TO {}").format(sql.Identifier(schema))
            )
            conn.execute(
                'CREATE TABLE "group" (id uuid PRIMARY KEY, organization_id uuid NOT NULL)'
            )
            conn.execute(
                'CREATE TABLE group_member (user_id uuid, group_id uuid REFERENCES "group", organization_id uuid)'
            )
            conn.execute(
                'INSERT INTO "group" VALUES (%s, %s), (%s, %s)',
                (group_a, org_a, group_b, org_b),
            )
            conn.execute(
                "INSERT INTO group_member VALUES (%s, %s, NULL), (%s, %s, NULL)",
                (user, group_a, user, group_b),
            )
            conn.execute(enable_group_member_table_rls().encode())
            conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(sql.Identifier(role)))
            conn.execute(
                sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(
                    sql.Identifier(schema), sql.Identifier(role)
                )
            )
            conn.execute(
                sql.SQL('GRANT SELECT ON "group" TO {}').format(sql.Identifier(role))
            )
            conn.execute(
                sql.SQL("GRANT ALL ON group_member TO {}").format(sql.Identifier(role))
            )
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
            conn.execute("SELECT set_config('app.rls_bypass', 'off', true)")
            conn.execute(
                "SELECT set_config('app.current_org_id', %s, true)", (str(org_a),)
            )
            assert conn.execute("SELECT group_id FROM group_member").fetchall() == [
                (group_a,)
            ]
            conn.execute(
                "INSERT INTO group_member VALUES (%s, %s, NULL)",
                (uuid.uuid4(), group_a),
            )
            for query, params in [
                (
                    "INSERT INTO group_member VALUES (%s, %s, NULL)",
                    (uuid.uuid4(), group_b),
                ),
                (
                    "UPDATE group_member SET group_id = %s WHERE group_id = %s",
                    (group_b, group_a),
                ),
            ]:
                with pytest.raises(psycopg.errors.InsufficientPrivilege):
                    with conn.transaction():
                        conn.execute(query.encode(), params)
            assert (
                conn.execute(
                    "DELETE FROM group_member WHERE group_id = %s", (group_b,)
                ).rowcount
                == 0
            )
            conn.execute(
                "SELECT set_config('app.current_org_id', %s, true)", (str(org_b),)
            )
            assert conn.execute("SELECT group_id FROM group_member").fetchall() == [
                (group_b,)
            ]
            conn.execute("RESET ROLE")
            conn.execute(disable_org_table_rls("group_member").encode())
            conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
            assert len(conn.execute("SELECT * FROM group_member").fetchall()) == 3
        finally:
            # All DDL, including the test role, is transactional.
            conn.rollback()
