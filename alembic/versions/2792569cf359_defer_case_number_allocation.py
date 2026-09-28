"""Defer case-number allocation until the end of case creation.

Revision ID: 2792569cf359
Revises: 864d277bedfa
Create Date: 2026-07-31 18:00:24.238167

"""

from collections.abc import Sequence

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "2792569cf359"
down_revision: str | None = "864d277bedfa"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_CASE_NUMBER_CONSTRAINT = "uq_case_workspace_case_number"
_REPLACEMENT_INDEX = "ix_case_workspace_case_number_replacement"


def _create_replacement_index() -> None:
    """Build a replacement index without blocking case writes."""
    with op.get_context().autocommit_block():
        # A failed migration can leave the concurrently-created index behind.
        # Rebuild it so a retry never adopts an invalid partial index.
        op.execute(f'DROP INDEX CONCURRENTLY IF EXISTS "{_REPLACEMENT_INDEX}"')
        op.execute(
            f"""
            CREATE UNIQUE INDEX CONCURRENTLY "{_REPLACEMENT_INDEX}"
            ON "case" (workspace_id, case_number)
            """
        )


def _replace_case_number_constraint(*, deferred: bool) -> None:
    _create_replacement_index()
    op.drop_constraint(_CASE_NUMBER_CONSTRAINT, "case", type_="unique")
    deferred_clause = " DEFERRABLE INITIALLY DEFERRED" if deferred else ""
    op.execute(
        f"""
        ALTER TABLE "case"
        ADD CONSTRAINT "{_CASE_NUMBER_CONSTRAINT}"
        UNIQUE USING INDEX "{_REPLACEMENT_INDEX}"{deferred_clause}
        """
    )


def _install_deferred_allocator() -> None:
    op.execute(
        """
        CREATE OR REPLACE FUNCTION assign_workspace_case_number()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.case_number IS NULL THEN
                -- Compatibility path for application versions that rely on the
                -- trigger to allocate a number during INSERT.
                UPDATE workspace
                SET last_case_number = last_case_number + 1
                WHERE id = NEW.workspace_id
                RETURNING last_case_number INTO NEW.case_number;

                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'Workspace % not found while allocating case number',
                        NEW.workspace_id;
                END IF;
            ELSIF NEW.case_number > 0 THEN
                -- Preserve explicit-number imports without locking the workspace
                -- row when the counter is already at or above the supplied value.
                UPDATE workspace
                SET last_case_number = NEW.case_number
                WHERE id = NEW.workspace_id
                  AND last_case_number < NEW.case_number;

                IF NOT FOUND THEN
                    PERFORM 1 FROM workspace WHERE id = NEW.workspace_id;
                    IF NOT FOUND THEN
                        RAISE EXCEPTION
                            'Workspace % not found while allocating case number',
                            NEW.workspace_id;
                    END IF;
                END IF;
            ELSE
                -- Non-positive numbers are transaction-local sentinels. Validate
                -- the foreign workspace without taking its counter-row lock.
                PERFORM 1 FROM workspace WHERE id = NEW.workspace_id;
                IF NOT FOUND THEN
                    RAISE EXCEPTION
                        'Workspace % not found while deferring case number allocation',
                        NEW.workspace_id;
                END IF;
            END IF;

            RETURN NEW;
        END;
        $$;
        """
    )
    op.execute(
        """
        CREATE OR REPLACE FUNCTION require_assigned_workspace_case_number()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF EXISTS (
                SELECT 1
                FROM "case"
                WHERE id = NEW.id
                  AND case_number <= 0
            ) THEN
                RAISE EXCEPTION 'Case number must be assigned before commit'
                    USING ERRCODE = '23514';
            END IF;

            RETURN NULL;
        END;
        $$;
        """
    )
    op.execute('DROP TRIGGER IF EXISTS trg_case_require_assigned_number ON "case"')
    op.execute(
        """
        CREATE CONSTRAINT TRIGGER trg_case_require_assigned_number
        AFTER INSERT OR UPDATE OF case_number ON "case"
        DEFERRABLE INITIALLY DEFERRED
        FOR EACH ROW
        WHEN (NEW.case_number <= 0)
        EXECUTE FUNCTION require_assigned_workspace_case_number()
        """
    )


def _install_immediate_allocator() -> None:
    op.execute('DROP TRIGGER IF EXISTS trg_case_require_assigned_number ON "case"')
    op.execute("DROP FUNCTION IF EXISTS require_assigned_workspace_case_number()")
    op.execute(
        """
        CREATE OR REPLACE FUNCTION assign_workspace_case_number()
        RETURNS trigger
        LANGUAGE plpgsql
        AS $$
        BEGIN
            IF NEW.case_number IS NULL THEN
                UPDATE workspace
                SET last_case_number = last_case_number + 1
                WHERE id = NEW.workspace_id
                RETURNING last_case_number INTO NEW.case_number;
            ELSE
                UPDATE workspace
                SET last_case_number = GREATEST(last_case_number, NEW.case_number)
                WHERE id = NEW.workspace_id;
            END IF;

            IF NOT FOUND THEN
                RAISE EXCEPTION
                    'Workspace % not found while allocating case number',
                    NEW.workspace_id;
            END IF;

            RETURN NEW;
        END;
        $$;
        """
    )


def upgrade() -> None:
    _replace_case_number_constraint(deferred=True)
    _install_deferred_allocator()


def downgrade() -> None:
    _replace_case_number_constraint(deferred=False)
    _install_immediate_allocator()
