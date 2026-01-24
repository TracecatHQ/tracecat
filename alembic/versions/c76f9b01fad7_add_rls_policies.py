"""Add Row-Level Security (RLS) policies for multi-tenancy

Revision ID: c76f9b01fad7
Revises: f4f5b93cbb16
Create Date: 2025-01-24

This migration enables PostgreSQL Row-Level Security on all tenant-scoped tables.

Tables are categorized as:
- Workspace-scoped: Filtered by workspace_id
- Organization-scoped: Filtered by organization_id
- Special (workspace table): Supports both workspace and org-level access

RLS is controlled by PostgreSQL session variables:
- app.current_workspace_id: Current workspace context
- app.current_org_id: Current organization context
- app.current_user_id: Current user (for audit)

Bypass mechanism:
- Setting any context to '00000000-0000-0000-0000-000000000000' bypasses RLS for that dimension
- This allows system operations to have unrestricted access
"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "c76f9b01fad7"
down_revision: str | None = "f4f5b93cbb16"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# RLS bypass value - setting context to this value bypasses RLS checks
RLS_BYPASS_VALUE = "00000000-0000-0000-0000-000000000000"

# Workspace-scoped tables (filtered by workspace_id)
WORKSPACE_SCOPED_TABLES = [
    "workflow",
    "workflow_definition",
    "workflow_folder",
    "action",
    "webhook",
    "webhook_api_key",
    "schedule",
    "secret",
    "workspace_variable",
    "tables",
    "case",
    "case_comment",
    "case_event",
    "case_task",
    "case_tag",
    "case_duration_definition",
    "case_duration",
    "tag",
    "interaction",
    "approval",
    "agent_session",
    "agent_session_history",
    "agent_preset",
    "file",
    "chat",
    "chat_message",
]

# Organization-scoped tables (filtered by organization_id)
ORG_SCOPED_TABLES = [
    "organization_secret",
    "organization_settings",
]


def _enable_rls_workspace_table(table: str) -> str:
    """Generate SQL to enable RLS on a workspace-scoped table."""
    return f"""
        ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY;
        ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY;

        CREATE POLICY rls_policy_{table} ON "{table}"
            FOR ALL
            USING (
                current_setting('app.current_workspace_id', true) = '{RLS_BYPASS_VALUE}'
                OR workspace_id = current_setting('app.current_workspace_id', true)::uuid
            )
            WITH CHECK (
                current_setting('app.current_workspace_id', true) = '{RLS_BYPASS_VALUE}'
                OR workspace_id = current_setting('app.current_workspace_id', true)::uuid
            );
    """


def _disable_rls_workspace_table(table: str) -> str:
    """Generate SQL to disable RLS on a workspace-scoped table."""
    return f"""
        DROP POLICY IF EXISTS rls_policy_{table} ON "{table}";
        ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY;
        ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY;
    """


def _enable_rls_org_table(table: str) -> str:
    """Generate SQL to enable RLS on an organization-scoped table."""
    return f"""
        ALTER TABLE "{table}" ENABLE ROW LEVEL SECURITY;
        ALTER TABLE "{table}" FORCE ROW LEVEL SECURITY;

        CREATE POLICY rls_policy_{table} ON "{table}"
            FOR ALL
            USING (
                current_setting('app.current_org_id', true) = '{RLS_BYPASS_VALUE}'
                OR organization_id = current_setting('app.current_org_id', true)::uuid
            )
            WITH CHECK (
                current_setting('app.current_org_id', true) = '{RLS_BYPASS_VALUE}'
                OR organization_id = current_setting('app.current_org_id', true)::uuid
            );
    """


def _disable_rls_org_table(table: str) -> str:
    """Generate SQL to disable RLS on an organization-scoped table."""
    return f"""
        DROP POLICY IF EXISTS rls_policy_{table} ON "{table}";
        ALTER TABLE "{table}" NO FORCE ROW LEVEL SECURITY;
        ALTER TABLE "{table}" DISABLE ROW LEVEL SECURITY;
    """


def _enable_rls_workspace_special() -> str:
    """Generate SQL for the workspace table with org-level access support.

    The workspace table is special because:
    - It has organization_id (for org-level queries)
    - It has id (for workspace-level queries)
    - Org admins need to list all workspaces in their org
    """
    return f"""
        ALTER TABLE "workspace" ENABLE ROW LEVEL SECURITY;
        ALTER TABLE "workspace" FORCE ROW LEVEL SECURITY;

        CREATE POLICY rls_policy_workspace ON "workspace"
            FOR ALL
            USING (
                -- Bypass check
                current_setting('app.current_workspace_id', true) = '{RLS_BYPASS_VALUE}'
                -- Direct workspace access
                OR id = current_setting('app.current_workspace_id', true)::uuid
                -- Org-level access (for listing workspaces in an org)
                OR organization_id = current_setting('app.current_org_id', true)::uuid
            )
            WITH CHECK (
                current_setting('app.current_workspace_id', true) = '{RLS_BYPASS_VALUE}'
                OR id = current_setting('app.current_workspace_id', true)::uuid
                OR organization_id = current_setting('app.current_org_id', true)::uuid
            );
    """


def _disable_rls_workspace_special() -> str:
    """Generate SQL to disable RLS on the workspace table."""
    return """
        DROP POLICY IF EXISTS rls_policy_workspace ON "workspace";
        ALTER TABLE "workspace" NO FORCE ROW LEVEL SECURITY;
        ALTER TABLE "workspace" DISABLE ROW LEVEL SECURITY;
    """


def upgrade() -> None:
    """Enable RLS on all tenant-scoped tables."""
    # Enable RLS on workspace-scoped tables
    for table in WORKSPACE_SCOPED_TABLES:
        op.execute(_enable_rls_workspace_table(table))

    # Enable RLS on organization-scoped tables
    for table in ORG_SCOPED_TABLES:
        op.execute(_enable_rls_org_table(table))

    # Enable RLS on workspace table (special handling)
    op.execute(_enable_rls_workspace_special())


def downgrade() -> None:
    """Disable RLS on all tenant-scoped tables."""
    # Disable RLS on workspace table first
    op.execute(_disable_rls_workspace_special())

    # Disable RLS on organization-scoped tables
    for table in ORG_SCOPED_TABLES:
        op.execute(_disable_rls_org_table(table))

    # Disable RLS on workspace-scoped tables
    for table in WORKSPACE_SCOPED_TABLES:
        op.execute(_disable_rls_workspace_table(table))
