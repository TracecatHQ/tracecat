from __future__ import annotations

import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest
from sqlalchemy import Table

from tracecat.auth.types import Role
from tracecat.cases.service import CaseFieldsService
from tracecat.db.models import Base, Membership, OrganizationMembership
from tracecat.db.tenant_rls import (
    ALL_TENANT_RLS_TABLES,
    ASSIGNMENT_SPLIT_POLICY_TABLES,
    ORG_OPTIONAL_WORKSPACE_POLICY_TABLES,
    ORG_POLICY_TABLES,
    SPECIAL_ORG_POLICY_TABLES,
    SPECIAL_TENANT_POLICY_TABLES,
    SPECIAL_WORKSPACE_POLICY_TABLES,
    WORKSPACE_POLICY_TABLES,
    disable_assignment_split_table_rls,
    enable_agent_tag_link_table_rls,
    enable_assignment_split_table_rls,
)
from tracecat.tables.service import TablesService

# Present in the database and still RLS-governed, but no longer mapped: nothing
# writes them, and the contract revision drops them.
UNMAPPED_LEGACY_TABLES = frozenset({"membership", "organization_membership"})


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for pure unit tests."""
    yield


def _mapped_table_names() -> set[str]:
    table_names: set[str] = set()
    for mapper in Base.registry.mappers:
        local_table = mapper.local_table
        if not isinstance(local_table, Table):
            continue
        table_name = local_table.name
        if isinstance(table_name, str):
            table_names.add(table_name)
    return table_names


def _mapped_table_names_with_column(column_name: str) -> set[str]:
    table_names: set[str] = set()
    for mapper in Base.registry.mappers:
        local_table = mapper.local_table
        if not isinstance(local_table, Table):
            continue
        table_name = getattr(local_table, "name", None)
        if isinstance(table_name, str) and column_name in local_table.columns:
            table_names.add(table_name)
    return table_names


def test_all_workspace_keyed_models_are_registered_for_tenant_rls() -> None:
    workspace_keyed_tables = _mapped_table_names_with_column("workspace_id")
    covered_workspace_tables = (
        WORKSPACE_POLICY_TABLES
        | ORG_OPTIONAL_WORKSPACE_POLICY_TABLES
        | SPECIAL_WORKSPACE_POLICY_TABLES
        # Assignments carry a split policy: workspace-scoped writes plus an
        # additive org-wide read for org-presence queries.
        | frozenset(ASSIGNMENT_SPLIT_POLICY_TABLES)
    )

    missing_workspace_coverage = workspace_keyed_tables - covered_workspace_tables

    assert not missing_workspace_coverage, (
        "Workspace-keyed SQLAlchemy tables must be registered for tenant RLS: "
        f"{sorted(missing_workspace_coverage)}"
    )


def test_all_org_keyed_models_are_registered_for_tenant_rls() -> None:
    org_keyed_tables = _mapped_table_names_with_column("organization_id")
    covered_org_tables = (
        ORG_POLICY_TABLES
        | ORG_OPTIONAL_WORKSPACE_POLICY_TABLES
        | SPECIAL_ORG_POLICY_TABLES
        | frozenset(ASSIGNMENT_SPLIT_POLICY_TABLES)
    )

    missing_org_coverage = org_keyed_tables - covered_org_tables

    assert not missing_org_coverage, (
        "Organization-keyed SQLAlchemy tables must be registered for tenant RLS: "
        f"{sorted(missing_org_coverage)}"
    )


def test_tenant_rls_registry_contains_only_mapped_tables() -> None:
    mapped_tables = _mapped_table_names()
    stale_registry_entries = (
        ALL_TENANT_RLS_TABLES - mapped_tables - UNMAPPED_LEGACY_TABLES
    )

    assert not stale_registry_entries, (
        "Tenant RLS registry contains tables that are not mapped in SQLAlchemy: "
        f"{sorted(stale_registry_entries)}"
    )


def test_legacy_membership_tables_stay_registered_for_tenant_rls() -> None:
    # The legacy tables are unmapped and unwritten but still present in the DB,
    # so they keep their policies until the contract release drops them.
    assert not isinstance(Membership.__table__, Table)
    assert not isinstance(OrganizationMembership.__table__, Table)
    assert not UNMAPPED_LEGACY_TABLES & _mapped_table_names()
    assert "membership" in WORKSPACE_POLICY_TABLES
    assert "organization_membership" in ORG_POLICY_TABLES


def test_assignment_tables_use_split_policy() -> None:
    for table in ASSIGNMENT_SPLIT_POLICY_TABLES:
        assert table in SPECIAL_TENANT_POLICY_TABLES
        assert table not in ORG_POLICY_TABLES
        assert table not in ORG_OPTIONAL_WORKSPACE_POLICY_TABLES


def test_assignment_split_policy_keeps_writes_workspace_scoped() -> None:
    for table in ASSIGNMENT_SPLIT_POLICY_TABLES:
        policy_sql = enable_assignment_split_table_rls(table)

        assert f'ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY' in policy_sql
        # A FOR ALL policy would re-admit org-wide writes from a workspace session.
        assert "FOR ALL" not in policy_sql
        for verb in ("insert", "update", "delete"):
            assert f"CREATE POLICY rls_policy_{table}_{verb} ON" in policy_sql
            assert f"FOR {verb.upper()}" in policy_sql

        # Writes never match an org-wide row while a workspace context is set.
        write_sql = policy_sql.split("_org_read")[-1]
        assert "workspace_id IS NULL" not in write_sql
        assert (
            "NULLIF(current_setting('app.current_workspace_id', true), '')::uuid IS NULL"
            in write_sql
        )

        # The additive read policy is org-only, with the bypass clause.
        org_read_policy = policy_sql.split("_org_read")[1].split("CREATE POLICY")[0]
        assert "FOR SELECT" in org_read_policy
        assert "app.current_workspace_id" not in org_read_policy
        assert "app.rls_bypass" in org_read_policy
        assert "WITH CHECK" not in org_read_policy


def test_assignment_split_policy_disable_drops_every_policy() -> None:
    for table in ASSIGNMENT_SPLIT_POLICY_TABLES:
        disable_sql = disable_assignment_split_table_rls(table)
        for verb in ("insert", "update", "delete"):
            assert f"DROP POLICY IF EXISTS rls_policy_{table}_{verb}" in disable_sql
        assert f"DROP POLICY IF EXISTS rls_policy_{table}_org_read" in disable_sql
        assert f'DROP POLICY IF EXISTS rls_policy_{table} ON "{table}"' in disable_sql


def test_agent_tag_link_is_registered_for_tenant_rls() -> None:
    assert "agent_tag_link" in SPECIAL_TENANT_POLICY_TABLES


def test_agent_tag_link_rls_policy_uses_parent_workspace_scopes() -> None:
    policy_sql = enable_agent_tag_link_table_rls()

    assert 'ALTER TABLE "agent_tag_link" ENABLE ROW LEVEL SECURITY' in policy_sql
    assert "CREATE POLICY rls_policy_agent_tag_link" in policy_sql
    assert "FROM agent_tag" in policy_sql
    assert "agent_tag.id = agent_tag_link.tag_id" in policy_sql
    assert "agent_tag.workspace_id = NULLIF(current_setting" in policy_sql
    assert "FROM agent_preset" in policy_sql
    assert "agent_preset.id = agent_tag_link.preset_id" in policy_sql
    assert "agent_preset.workspace_id = NULLIF(current_setting" in policy_sql
    assert "WITH CHECK" in policy_sql


def test_dynamic_workspace_rls_targets_workspace_scoped_schemas() -> None:
    session = AsyncMock()
    session.sync_session = MagicMock()
    session.sync_session.info = {}
    role = Role(
        type="service",
        workspace_id=uuid.uuid4(),
        organization_id=uuid.uuid4(),
        user_id=None,
        service_id="tracecat-service",
    )

    tables_service = TablesService(session=session, role=role)
    case_fields_service = CaseFieldsService(session=session, role=role)

    assert tables_service._get_schema_name().startswith("tables_")
    assert tables_service._full_table_name("alerts").startswith('"tables_')
    assert case_fields_service.schema_name.startswith("custom_fields_")
    assert (
        case_fields_service._table_definition().schema
        == case_fields_service.schema_name
    )
