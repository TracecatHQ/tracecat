"""Integration tests for RLS policies.

These tests verify that RLS policies correctly filter database access
based on organization and workspace context.

Classes marked ``requires_rls_mode`` need:
1. A running PostgreSQL database with the RLS migration applied
2. RLS mode enabled (TRACECAT__RLS_MODE=enforce or shadow)

Run them with:
    TRACECAT__RLS_MODE=enforce uv run pytest tests/unit/test_rls_policies.py -v

``TestRlsIsolation`` sets the mode itself and runs in every test run.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import TYPE_CHECKING

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from tracecat import config
from tracecat.auth.types import Role
from tracecat.cases.service import CaseFieldsService
from tracecat.contexts import ctx_role
from tracecat.db.engine import (
    _initialize_session_rls_context,
    get_async_engine,
    get_async_session,
    get_async_session_tenant_context_manager,
)
from tracecat.db.models import Workspace
from tracecat.db.rls import (
    RLS_BYPASS_OFF,
    RLS_BYPASS_ON,
    RLS_UNSET_VALUE,
    RLS_VAR_BYPASS,
    RLS_VAR_ORG_ID,
    RLS_VAR_WORKSPACE_ID,
    is_rls_enabled,
    set_rls_context,
)
from tracecat.tables.enums import SqlType
from tracecat.tables.schemas import TableColumnCreate, TableCreate, TableRowInsert
from tracecat.tables.service import TablesService

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator


# Skip classes that read the process-wide RLS mode when it is off
requires_rls_mode = pytest.mark.skipif(
    not is_rls_enabled(),
    reason="RLS mode not enabled. Set TRACECAT__RLS_MODE=enforce or shadow",
)


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for these tests."""
    yield


@pytest.fixture
async def rls_session() -> AsyncGenerator[AsyncSession, None]:
    """Create a session for RLS testing without automatic context setting."""
    async with AsyncSession(get_async_engine(), expire_on_commit=False) as session:
        yield session


@pytest.fixture
def org_id_a() -> uuid.UUID:
    """Organization A for testing."""
    return uuid.uuid4()


@pytest.fixture
def org_id_b() -> uuid.UUID:
    """Organization B for testing."""
    return uuid.uuid4()


@pytest.fixture
def workspace_id_a() -> uuid.UUID:
    """Workspace A for testing."""
    return uuid.uuid4()


@pytest.fixture
def workspace_id_b() -> uuid.UUID:
    """Workspace B for testing."""
    return uuid.uuid4()


@pytest.fixture
def role_workspace_a(org_id_a: uuid.UUID, workspace_id_a: uuid.UUID) -> Role:
    """Role with access to workspace A."""
    return Role(
        type="user",
        workspace_id=workspace_id_a,
        organization_id=org_id_a,
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )


@pytest.fixture
def role_workspace_b(org_id_b: uuid.UUID, workspace_id_b: uuid.UUID) -> Role:
    """Role with access to workspace B."""
    return Role(
        type="user",
        workspace_id=workspace_id_b,
        organization_id=org_id_b,
        user_id=uuid.uuid4(),
        service_id="tracecat-api",
    )


@requires_rls_mode
class TestRlsContextVariables:
    """Tests for RLS PostgreSQL session variables."""

    @pytest.mark.anyio
    async def test_context_variables_are_set_correctly(
        self, rls_session: AsyncSession, org_id_a: uuid.UUID, workspace_id_a: uuid.UUID
    ):
        """Test that RLS context variables are set in the PostgreSQL session."""
        await set_rls_context(rls_session, org_id_a, workspace_id_a)

        # Query the current settings
        result = await rls_session.execute(
            text(f"SELECT current_setting('{RLS_VAR_ORG_ID}', true)")
        )
        org_setting = result.scalar()
        assert org_setting == str(org_id_a)

        result = await rls_session.execute(
            text(f"SELECT current_setting('{RLS_VAR_WORKSPACE_ID}', true)")
        )
        workspace_setting = result.scalar()
        assert workspace_setting == str(workspace_id_a)

        result = await rls_session.execute(
            text(f"SELECT current_setting('{RLS_VAR_BYPASS}', true)")
        )
        bypass_setting = result.scalar()
        assert bypass_setting == RLS_BYPASS_OFF

    @pytest.mark.anyio
    async def test_none_context_resets_tenant_scope(self, rls_session: AsyncSession):
        """Test that unset tenant IDs do not imply bypass access."""
        await set_rls_context(rls_session, org_id=None, workspace_id=None)

        result = await rls_session.execute(
            text(f"SELECT current_setting('{RLS_VAR_ORG_ID}', true)")
        )
        org_setting = result.scalar()
        assert org_setting == RLS_UNSET_VALUE

        result = await rls_session.execute(
            text(f"SELECT current_setting('{RLS_VAR_WORKSPACE_ID}', true)")
        )
        workspace_setting = result.scalar()
        assert workspace_setting == RLS_UNSET_VALUE

        result = await rls_session.execute(
            text(f"SELECT current_setting('{RLS_VAR_BYPASS}', true)")
        )
        bypass_setting = result.scalar()
        assert bypass_setting == RLS_BYPASS_OFF

    @pytest.mark.anyio
    async def test_context_reapplies_after_commit(
        self, rls_session: AsyncSession, org_id_a: uuid.UUID, workspace_id_a: uuid.UUID
    ):
        """Test that cached RLS context is automatically re-applied after commit."""
        await set_rls_context(rls_session, org_id=org_id_a, workspace_id=workspace_id_a)
        await rls_session.commit()

        result = await rls_session.execute(
            text(f"SELECT current_setting('{RLS_VAR_ORG_ID}', true)")
        )
        assert result.scalar() == str(org_id_a)

        result = await rls_session.execute(
            text(f"SELECT current_setting('{RLS_VAR_WORKSPACE_ID}', true)")
        )
        assert result.scalar() == str(workspace_id_a)


@requires_rls_mode
class TestRlsPolicyEnforcement:
    """Tests for RLS policy enforcement on tables.

    Note: These tests require a test database with actual data.
    They are designed to verify that RLS policies are working but
    may need adjustment based on the test data setup.
    """

    @pytest.mark.anyio
    @pytest.mark.parametrize("table", ["workspace", "workflow"])
    async def test_rls_enabled_with_policy(
        self, rls_session: AsyncSession, table: str
    ) -> None:
        """RLS is enabled without FORCE and the table's policy is installed."""
        row = (
            await rls_session.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity "
                    "FROM pg_class WHERE relname = :table"
                ),
                {"table": table},
            )
        ).fetchone()
        assert row is not None
        relrowsecurity, relforcerowsecurity = row
        assert relrowsecurity is True
        assert relforcerowsecurity is False

        policy_names = (
            await rls_session.execute(
                text("SELECT policyname FROM pg_policies WHERE tablename = :table"),
                {"table": table},
            )
        ).scalars()
        assert f"rls_policy_{table}" in set(policy_names)


async def _make_owner_of(
    session: AsyncSession, *, schema_name: str, table_name: str
) -> str:
    """Hand a dynamic table to a fresh role with no superuser or BYPASSRLS.

    The role lives in the test transaction and is rolled back with it.
    """
    owner = f"rls_owner_{uuid.uuid4().hex[:12]}"
    await session.execute(text(f'CREATE ROLE "{owner}" NOSUPERUSER NOBYPASSRLS'))
    await session.execute(text(f'GRANT USAGE ON SCHEMA "{schema_name}" TO "{owner}"'))
    await session.execute(
        text(f'ALTER TABLE "{schema_name}"."{table_name}" OWNER TO "{owner}"')
    )
    return owner


async def _row_ids_as(
    session: AsyncSession, *, db_role: str, schema_name: str, table_name: str
) -> list[uuid.UUID]:
    await session.execute(text(f'SET ROLE "{db_role}"'))
    try:
        result = await session.execute(
            text(f'SELECT id FROM "{schema_name}"."{table_name}" ORDER BY id')
        )
        return list(result.scalars())
    finally:
        await session.execute(text("RESET ROLE"))


async def _force_flags(
    session: AsyncSession, *, schema_name: str, table_name: str
) -> tuple[bool, bool]:
    row = (
        await session.execute(
            text(
                """
                SELECT c.relrowsecurity, c.relforcerowsecurity
                FROM pg_class c
                JOIN pg_namespace n ON n.oid = c.relnamespace
                WHERE n.nspname = :schema_name AND c.relname = :table_name
                """
            ),
            {"schema_name": schema_name, "table_name": table_name},
        )
    ).one()
    return row[0], row[1]


@pytest.fixture
async def owned_table(
    session: AsyncSession, svc_role: Role
) -> tuple[str, str, str, uuid.UUID]:
    """A Tables table with one row, owned by a role that RLS can filter.

    Returns (owner role, schema, table, row id).
    """
    service = TablesService(session=session, role=svc_role)
    table = await service.create_table(
        TableCreate(
            name="rls_probe",
            columns=[
                TableColumnCreate(
                    name="label", type=SqlType.TEXT, nullable=True, default=None
                )
            ],
        )
    )
    row = await service.insert_row(table, TableRowInsert(data={"label": "a"}))
    schema_name = service._get_schema_name()
    owner = await _make_owner_of(
        session, schema_name=schema_name, table_name=table.name
    )
    return owner, schema_name, table.name, row["id"]


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
class TestRlsIsolation:
    """Workspace policies on dynamic tables filter even the table owner.

    The app role will own the dynamic schemas, so FORCE is what keeps the
    policy in effect. Each test reads as a non-superuser, non-BYPASSRLS owner.
    """

    async def test_new_dynamic_tables_force_rls(
        self, session: AsyncSession, svc_role: Role
    ) -> None:
        tables = TablesService(session=session, role=svc_role)
        table = await tables.create_table(TableCreate(name="rls_force_probe"))
        case_fields = CaseFieldsService(session=session, role=svc_role)
        await case_fields.initialize_workspace_schema()

        for schema_name, table_name in (
            (tables._get_schema_name(), table.name),
            (case_fields.schema_name, case_fields.sanitized_table_name),
        ):
            assert await _force_flags(
                session, schema_name=schema_name, table_name=table_name
            ) == (True, True)

    @pytest.mark.parametrize(
        ("mode", "context", "visible"),
        [
            # Off and shadow set app.rls_bypass=on: results never change.
            (config.RLSMode.OFF, "other_workspace", True),
            (config.RLSMode.SHADOW, "other_workspace", True),
            (config.RLSMode.ENFORCE, "own_workspace", True),
            (config.RLSMode.ENFORCE, "other_workspace", False),
            (config.RLSMode.ENFORCE, "no_role", False),
        ],
    )
    async def test_owner_rows_follow_rls_mode(
        self,
        session: AsyncSession,
        svc_role: Role,
        owned_table: tuple[str, str, str, uuid.UUID],
        monkeypatch: pytest.MonkeyPatch,
        mode: config.RLSMode,
        context: str,
        visible: bool,
    ) -> None:
        owner, schema_name, table_name, row_id = owned_table
        roles: dict[str, Role | None] = {
            "own_workspace": svc_role,
            "other_workspace": svc_role.model_copy(
                update={"workspace_id": uuid.uuid4()}
            ),
            "no_role": None,
        }
        monkeypatch.setattr(config, "TRACECAT__RLS_MODE", mode)
        token = ctx_role.set(roles[context])
        try:
            await _initialize_session_rls_context(session)
        finally:
            ctx_role.reset(token)

        row_ids = await _row_ids_as(
            session, db_role=owner, schema_name=schema_name, table_name=table_name
        )
        assert row_ids == ([row_id] if visible else [])

    async def test_owner_skips_policy_without_force(
        self,
        session: AsyncSession,
        owned_table: tuple[str, str, str, uuid.UUID],
    ) -> None:
        """Control: the same owner and context see the row once FORCE is off."""
        owner, schema_name, table_name, row_id = owned_table
        await set_rls_context(
            session, org_id=uuid.uuid4(), workspace_id=uuid.uuid4(), bypass=False
        )
        assert (
            await _row_ids_as(
                session, db_role=owner, schema_name=schema_name, table_name=table_name
            )
            == []
        )

        await session.execute(
            text(
                f'ALTER TABLE "{schema_name}"."{table_name}" NO FORCE ROW LEVEL SECURITY'
            )
        )
        assert await _row_ids_as(
            session, db_role=owner, schema_name=schema_name, table_name=table_name
        ) == [row_id]


async def _rls_settings(session: AsyncSession) -> tuple[str, str, str]:
    """Return the session's (bypass, org, workspace) RLS settings."""
    row = (
        await session.execute(
            text(
                f"SELECT current_setting('{RLS_VAR_BYPASS}', true), "
                f"current_setting('{RLS_VAR_ORG_ID}', true), "
                f"current_setting('{RLS_VAR_WORKSPACE_ID}', true)"
            )
        )
    ).one()
    return row[0], row[1], row[2]


@pytest.mark.anyio
@pytest.mark.usefixtures("db")
class TestTenantSession:
    """System code that knows its tenant runs inside it, not under the bypass.

    Invariant: under ``enforce`` a tenant session never turns the bypass on and
    always carries the tenant's organization, re-applied on every transaction.
    """

    async def test_workspace_resolves_its_organization(
        self, svc_workspace: Workspace, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(config, "TRACECAT__RLS_MODE", config.RLSMode.ENFORCE)
        expected = (
            RLS_BYPASS_OFF,
            str(svc_workspace.organization_id),
            str(svc_workspace.id),
        )
        async with get_async_session_tenant_context_manager(
            workspace_id=svc_workspace.id
        ) as session:
            assert await _rls_settings(session) == expected
            await session.commit()
            assert await _rls_settings(session) == expected

    async def test_organization_only(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(config, "TRACECAT__RLS_MODE", config.RLSMode.ENFORCE)
        org_id = uuid.uuid4()
        async with get_async_session_tenant_context_manager(
            organization_id=org_id
        ) as session:
            assert await _rls_settings(session) == (
                RLS_BYPASS_OFF,
                str(org_id),
                RLS_UNSET_VALUE,
            )

    async def test_unknown_workspace_stays_scoped_to_it(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """A deleted workspace leaves no organization, never the bypass."""
        monkeypatch.setattr(config, "TRACECAT__RLS_MODE", config.RLSMode.ENFORCE)
        workspace_id = uuid.uuid4()
        async with get_async_session_tenant_context_manager(
            workspace_id=workspace_id
        ) as session:
            assert await _rls_settings(session) == (
                RLS_BYPASS_OFF,
                RLS_UNSET_VALUE,
                str(workspace_id),
            )

    @pytest.mark.parametrize("mode", [config.RLSMode.OFF, config.RLSMode.SHADOW])
    async def test_off_and_shadow_match_every_other_session(
        self, monkeypatch: pytest.MonkeyPatch, mode: config.RLSMode
    ) -> None:
        monkeypatch.setattr(config, "TRACECAT__RLS_MODE", mode)
        async with get_async_session_tenant_context_manager(
            organization_id=uuid.uuid4()
        ) as session:
            bypass, _, _ = await _rls_settings(session)
        assert bypass == RLS_BYPASS_ON

    async def test_needs_a_tenant(self) -> None:
        with pytest.raises(ValueError, match="organization or workspace"):
            async with get_async_session_tenant_context_manager():
                pass


@requires_rls_mode
class TestRlsWithCtxRole:
    """Tests for RLS integration with ctx_role context variable."""

    @pytest.mark.anyio
    async def test_ctx_role_is_used_by_session(
        self,
        role_workspace_a: Role,
    ):
        """Test that ctx_role is properly read when creating sessions."""
        # Set ctx_role before getting session
        ctx_role.set(role_workspace_a)

        try:
            async for session in get_async_session():
                # Check that the context was set
                result = await session.execute(
                    text(f"SELECT current_setting('{RLS_VAR_WORKSPACE_ID}', true)")
                )
                workspace_setting = result.scalar()
                assert workspace_setting == str(role_workspace_a.workspace_id)
                break
        finally:
            ctx_role.set(None)
