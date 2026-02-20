"""cleanup legacy sso and map feature flags to entitlements

Revision ID: 60a5af5effdd
Revises: a91c2b7d4e3f
Create Date: 2026-02-19 19:11:56.396025

"""

import json
import os
from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "60a5af5effdd"
down_revision: str | None = "a91c2b7d4e3f"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

_PAYWALLED_KEYS = ("git_sync", "agent_addons", "case_addons")
_LEGACY_KEYS = (
    "sso",
    "agent_approvals",
    "agent_presets",
    "case_dropdowns",
    "case_durations",
    "case_tasks",
    "case_triggers",
)


def _parse_feature_flags() -> set[str]:
    normalized: set[str] = set()
    for raw_flag in os.environ.get("TRACECAT__FEATURE_FLAGS", "").split(","):
        if not (flag := raw_flag.strip()):
            continue
        normalized.add(flag.lower().replace("_", "-"))
    return normalized


def _paywalled_updates_from_flags(flags: set[str]) -> dict[str, bool]:
    updates: dict[str, bool] = {}
    if "git-sync" in flags:
        updates["git_sync"] = True
    if flags & {"agent-approvals", "agent-presets"}:
        updates["agent_addons"] = True
    if flags & {"case-dropdowns", "case-durations", "case-tasks", "case-triggers"}:
        updates["case_addons"] = True
    return updates


def _jsonb_remove_expr(column: str, keys: tuple[str, ...]) -> str:
    expr = f"COALESCE({column}, '{{}}'::jsonb)"
    for key in keys:
        expr += f" - '{key}'"
    return expr


def upgrade() -> None:
    flags = _parse_feature_flags()
    updates = _paywalled_updates_from_flags(flags)

    # Clean up deprecated legacy keys from all tiers.
    cleaned_tier_expr = _jsonb_remove_expr("entitlements", _LEGACY_KEYS)
    op.execute(
        sa.text(
            f"""
            UPDATE tier
            SET entitlements = {cleaned_tier_expr}
            """
        )
    )

    # Overwrite paywalled entitlement keys for the active default tier from
    # legacy feature flags, preserving non-paywalled keys (e.g. custom_registry).
    default_tier_base_expr = _jsonb_remove_expr("entitlements", _PAYWALLED_KEYS)
    op.execute(
        sa.text(
            """
            UPDATE tier
            SET entitlements = """
            + default_tier_base_expr
            + """ || CAST(:updates AS jsonb)
            WHERE is_default = true
            """
        ).bindparams(updates=json.dumps(updates))
    )

    # Remove deprecated keys from org overrides while preserving supported override keys.
    cleaned_overrides_expr = _jsonb_remove_expr("entitlement_overrides", _LEGACY_KEYS)
    op.execute(
        sa.text(
            f"""
            UPDATE organization_tier
            SET entitlement_overrides = NULLIF({cleaned_overrides_expr}, '{{}}'::jsonb)
            WHERE entitlement_overrides IS NOT NULL
            """
        )
    )


def downgrade() -> None:
    op.execute(
        """
        UPDATE tier
        SET entitlements = COALESCE(entitlements, '{}'::jsonb) ||
            '{"sso": true, "git_sync": true, "agent_addons": true, "case_addons": true}'::jsonb
        WHERE is_default = true
        """
    )
