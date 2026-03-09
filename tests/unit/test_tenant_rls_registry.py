from __future__ import annotations

from collections.abc import Iterator

import pytest

from tracecat.db.models import Base
from tracecat.db.tenant_rls import (
    ALL_TENANT_RLS_TABLES,
    ORG_OPTIONAL_WORKSPACE_POLICY_TABLES,
    ORG_POLICY_TABLES,
    SPECIAL_ORG_POLICY_TABLES,
    WORKSPACE_POLICY_TABLES,
)


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
        WORKSPACE_POLICY_TABLES | ORG_OPTIONAL_WORKSPACE_POLICY_TABLES
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
