from __future__ import annotations

import uuid
from collections.abc import Iterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from tracecat.auth.types import Role
from tracecat.cases.service import CaseFieldsService
from tracecat.db.models import Base
from tracecat.db.tenant_rls import (
    ALL_TENANT_RLS_TABLES,
    ORG_OPTIONAL_WORKSPACE_POLICY_TABLES,
    ORG_POLICY_TABLES,
    SPECIAL_ORG_POLICY_TABLES,
    SPECIAL_WORKSPACE_POLICY_TABLES,
    WORKSPACE_POLICY_TABLES,
    enable_org_shared_table_rls,
)
from tracecat.tables.service import TablesService


@pytest.fixture(scope="session", autouse=True)
def workflow_bucket() -> Iterator[None]:
    """Disable MinIO-dependent workflow bucket setup for pure unit tests."""
    yield


def _mapped_table_names() -> set[str]:
    table_names: set[str] = set()
    for mapper in Base.registry.mappers:
        table_name = getattr(mapper.local_table, "name", None)
        if isinstance(table_name, str):
            table_names.add(table_name)
    return table_names


def _mapped_table_names_with_column(column_name: str) -> set[str]:
    table_names: set[str] = set()
    for mapper in Base.registry.mappers:
        local_table = mapper.local_table
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
    )

    missing_org_coverage = org_keyed_tables - covered_org_tables

    assert not missing_org_coverage, (
        "Organization-keyed SQLAlchemy tables must be registered for tenant RLS: "
        f"{sorted(missing_org_coverage)}"
    )


def test_tenant_rls_registry_contains_only_mapped_tables() -> None:
    mapped_tables = _mapped_table_names()
    stale_registry_entries = ALL_TENANT_RLS_TABLES - mapped_tables

    assert not stale_registry_entries, (
        "Tenant RLS registry contains tables that are not mapped in SQLAlchemy: "
        f"{sorted(stale_registry_entries)}"
    )


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


def test_org_shared_rls_allows_global_reads_but_not_global_writes() -> None:
    sql = enable_org_shared_table_rls("agent_catalog")

    assert "organization_id IS NULL" in sql
    _, with_check_clause = sql.split("WITH CHECK", maxsplit=1)
    assert "organization_id IS NULL" not in with_check_clause
