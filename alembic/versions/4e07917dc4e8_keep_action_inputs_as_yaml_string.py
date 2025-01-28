"""Keep Action inputs as yaml string

Revision ID: 4e07917dc4e8
Revises: f92c80ef8c9d
Create Date: 2025-01-28 16:02:49.388047

"""

import json
from collections.abc import Sequence

import sqlalchemy as sa
import sqlmodel.sql.sqltypes
import yaml
from sqlalchemy import text
from sqlalchemy.dialects import postgresql

from alembic import op
from tracecat.logger import logger

# revision identifiers, used by Alembic.
revision: str = "4e07917dc4e8"
down_revision: str | None = "f92c80ef8c9d"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    # First alter the column type to text
    op.alter_column(
        "action",
        "inputs",
        existing_type=postgresql.JSONB(astext_type=sa.Text()),
        type_=sqlmodel.sql.sqltypes.AutoString(),
        nullable=False,
    )

    # Then get all existing action inputs
    connection = op.get_bind()
    actions = connection.execute(text("SELECT id, inputs FROM action")).fetchall()

    # Convert JSONB to YAML strings
    for action_id, inputs in actions:
        # JSONB data is a string. Load it as a dict.
        # If its an empty dict, coerce it to None (empty string in yaml)
        data = json.loads(inputs) or None
        # Convert the dict to a YAML string.
        yaml_str = yaml.dump(data) if data is not None else ""
        logger.info(
            "Updating action with inputs:",
            inputs=inputs,
            type=type(inputs).__name__,
            data=data,
            data_type=type(data).__name__,
            yaml_str=yaml_str,
        )

        if inputs is not None:
            # If the input is already a string, use it directly

            connection.execute(
                text("UPDATE action SET inputs = :yaml WHERE id = :id"),
                {"yaml": yaml_str, "id": action_id},
            )


def downgrade() -> None:
    # First get all existing action inputs
    connection = op.get_bind()
    actions = connection.execute(text("SELECT id, inputs FROM action")).fetchall()

    # Convert strings back to JSON
    for action_id, inputs in actions:
        if inputs is not None:
            # Nonetypes are stored as empty strings
            try:
                data = yaml.safe_load(inputs)
            except yaml.YAMLError:
                data = inputs
            data = data or {}
            json_data = json.dumps(data)

            logger.info(
                "Downgrading action with inputs:",
                inputs=inputs,
                type=type(inputs).__name__,
                data=data,
                data_type=type(data).__name__,
                json_data=json_data,
                json_data_type=type(json_data),
            )

            connection.execute(
                text("UPDATE action SET inputs = :json WHERE id = :id"),
                {"json": json_data, "id": action_id},
            )

    # Then alter the column type back to JSONB with explicit USING clause
    op.execute("ALTER TABLE action ALTER COLUMN inputs TYPE JSONB USING inputs::jsonb")
