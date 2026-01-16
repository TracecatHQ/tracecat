"""Scope org-unique constraints by organization.

Revision ID: 045d54f6247c
Revises: 4a298374b126
Create Date: 2026-01-16 00:00:00.000000

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "045d54f6247c"
down_revision: str | None = "4a298374b126"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # organization_secret: (organization_id, name, environment)
    op.drop_constraint(
        op.f("uq_organization_secret_name_environment"),
        "organization_secret",
        type_="unique",
    )
    op.create_unique_constraint(
        op.f("uq_organization_secret_organization_id_name_environment"),
        "organization_secret",
        ["organization_id", "name", "environment"],
    )

    # organization_settings: unique key -> (organization_id, key)
    op.drop_index(
        op.f("ix_organization_settings_key"), table_name="organization_settings"
    )
    op.create_unique_constraint(
        op.f("uq_organization_settings_organization_id_key"),
        "organization_settings",
        ["organization_id", "key"],
    )

    # registry_repository: unique origin -> (organization_id, origin)
    op.drop_constraint(
        op.f("uq_registry_repository_origin"),
        "registry_repository",
        type_="unique",
    )
    op.create_unique_constraint(
        op.f("uq_registry_repository_organization_id_origin"),
        "registry_repository",
        ["organization_id", "origin"],
    )

    # registry_action: unique namespace+name -> (organization_id, namespace, name)
    op.drop_constraint(
        "uq_registry_action_namespace_name",
        "registry_action",
        type_="unique",
    )
    op.create_unique_constraint(
        op.f("uq_registry_action_organization_id_namespace_name"),
        "registry_action",
        ["organization_id", "namespace", "name"],
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("uq_registry_action_organization_id_namespace_name"),
        "registry_action",
        type_="unique",
    )
    op.create_unique_constraint(
        "uq_registry_action_namespace_name",
        "registry_action",
        ["namespace", "name"],
    )

    op.drop_constraint(
        op.f("uq_registry_repository_organization_id_origin"),
        "registry_repository",
        type_="unique",
    )
    op.create_unique_constraint(
        op.f("uq_registry_repository_origin"),
        "registry_repository",
        ["origin"],
    )

    op.drop_constraint(
        op.f("uq_organization_settings_organization_id_key"),
        "organization_settings",
        type_="unique",
    )
    op.create_index(
        op.f("ix_organization_settings_key"),
        "organization_settings",
        ["key"],
        unique=True,
    )

    op.drop_constraint(
        op.f("uq_organization_secret_organization_id_name_environment"),
        "organization_secret",
        type_="unique",
    )
    op.create_unique_constraint(
        op.f("uq_organization_secret_name_environment"),
        "organization_secret",
        ["name", "environment"],
    )
