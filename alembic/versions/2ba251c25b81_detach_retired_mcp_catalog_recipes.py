"""Detach retired MCP catalog recipes.

Revision ID: 2ba251c25b81
Revises: 598b32358ec5
Create Date: 2026-08-26 00:00:00.000000

Six catalog recipes are retired. Two are removed outright
(``google-security-command-center-mcp``, ``virustotal-mcp``); four have their
stdio recipe replaced by a hosted HTTP server under the same slug
(``servicenow-mcp``, ``google-cloud-secops-mcp``, ``semgrep-mcp``,
``jamf-mcp``), so only their stdio rows are affected and HTTP rows keep their
binding.

Existing ``mcp_integration`` rows created from those recipes are detached, not
deleted: only ``catalog_slug`` is cleared. Deleting them would invalidate the
row UUIDs that agent presets, pinned preset versions, sessions, and workflows
hold in their JSONB arrays, turning affected workflow runs into non-retryable
validation failures. Agent resolution reads those UUIDs and ignores the catalog
binding, so a detached row keeps resolving exactly as before.

Everything else on the row is preserved: ``id``, ``slug``, ``name``, the stdio
command and args, the encrypted stdio env and headers, ``tools``,
``oauth_integration_id``, and both timestamps. ``slug`` in particular is never
rewritten, because MCP tool names are derived from it.

A detached row is no longer platform-managed, so it reappears in the workspace
"Custom MCP" list where users can edit, repair, or delete it. Leaving the
binding in place would instead have hidden the row from every list once its
catalog entry was gone, while it kept running. Connecting the hosted
replacement for one of the four replaced slugs creates a separate ``<slug>-1``
row and leaves the detached one untouched.

No expand/contract split is needed: the previous app version keeps working
after this migration, because to it a detached row is simply a custom MCP row,
a shape it already handles on every read and write path.

"""

import logging
from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.engine import Connection

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2ba251c25b81"
down_revision: str | None = "598b32358ec5"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

logger = logging.getLogger("alembic.runtime.migration")

# Catalog entries removed outright: every row bound to them is detached.
RETIRED_CATALOG_SLUGS: tuple[str, ...] = (
    "google-security-command-center-mcp",
    "virustotal-mcp",
)
# Catalog entries whose stdio recipe is replaced by a hosted HTTP server under
# the same slug: only stdio rows are detached, HTTP rows stay bound.
RETIRED_STDIO_CATALOG_SLUGS: tuple[str, ...] = (
    "servicenow-mcp",
    "google-cloud-secops-mcp",
    "semgrep-mcp",
    "jamf-mcp",
)

STDIO_SERVER_TYPE = "stdio"

_DETACH_BY_CATALOG_SLUG = sa.text(
    "UPDATE mcp_integration SET catalog_slug = NULL WHERE catalog_slug = :slug"
)
_DETACH_STDIO_BY_CATALOG_SLUG = sa.text(
    "UPDATE mcp_integration SET catalog_slug = NULL "
    "WHERE catalog_slug = :slug AND server_type = :server_type"
)


def _report_detached(slug: str, server_type: str | None, count: int) -> None:
    # Identifiers only: the catalog slug is repo-owned, but workspace ids, row
    # ids, integration names, server URIs, and stdio arguments are
    # customer-authored and must not land in logs. Operators can look rows up by
    # catalog slug against a pre-upgrade snapshot.
    message = (
        "Detached retired MCP catalog binding: "
        f"catalog_slug={slug} "
        f"server_type={server_type or 'any'} "
        f"rows={count}"
    )
    print(message)
    logger.info(message)


def _detach_retired_catalog_bindings(bind: Connection) -> None:
    """Clear ``catalog_slug`` on rows created from retired catalog recipes.

    Idempotent by construction: a detached row has ``catalog_slug IS NULL`` and
    therefore matches no predicate on a second pass.
    """
    for slug in RETIRED_CATALOG_SLUGS:
        result = bind.execute(_DETACH_BY_CATALOG_SLUG, {"slug": slug})
        _report_detached(slug, None, result.rowcount)

    for slug in RETIRED_STDIO_CATALOG_SLUGS:
        result = bind.execute(
            _DETACH_STDIO_BY_CATALOG_SLUG,
            {"slug": slug, "server_type": STDIO_SERVER_TYPE},
        )
        _report_detached(slug, STDIO_SERVER_TYPE, result.rowcount)


def upgrade() -> None:
    # RLS is enabled without FORCE on mcp_integration, so the migration role
    # updates the table directly and no policy handling is needed here.
    bind = op.get_bind()
    _detach_retired_catalog_bindings(bind)


def downgrade() -> None:
    """Detachment is lossy and cannot be reversed."""
    raise NotImplementedError(
        "Detaching retired MCP catalog recipes is not reversible: the migration "
        "clears catalog_slug and nothing records which rows carried it. It "
        "cannot be reconstructed from slug either, because a row's slug may "
        "carry a numeric suffix or belong to a same-named custom MCP that was "
        "never catalog-bound, and the retired stdio recipes no longer exist in "
        "the catalog to re-bind to. Re-binding the four replaced slugs would "
        "also be wrong: their catalog entries now describe hosted HTTP servers, "
        "not the stdio commands these rows still run. To recover, restore the "
        "database from a backup or snapshot taken before this upgrade, then "
        "roll the application back to the matching release."
    )
