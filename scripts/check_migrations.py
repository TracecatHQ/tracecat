#!/usr/bin/env python3
"""Check Alembic topology without running env.py or connecting to a database."""

import sys
import warnings

from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.script.revision import RevisionError
from alembic.util import CommandError

# This merge reconciles the heads that predate the linear-history policy.
# Its ancestors can contain branches, merges and cross-branch dependencies.
LINEAR_HISTORY_BASE = "6e921d4f0b72"


def check_migrations(
    scripts: ScriptDirectory, *, linear_since: str = LINEAR_HISTORY_BASE
) -> str:
    """Return the sole head, rejecting invalid graphs and new non-linear history."""
    # Alembic warns rather than fails for some invalid metadata (e.g. duplicate
    # revision IDs). Those must fail CI too.
    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        heads = scripts.get_heads()
        if len(heads) != 1:
            raise ValueError(
                f"Expected exactly one Alembic head; found {len(heads)}: {heads}. "
                "Rebase unmerged migrations onto the latest head."
            )
        bases = scripts.get_bases()
        if len(bases) != 1:
            raise ValueError(f"Expected exactly one Alembic base; found {bases}.")

        revisions = list(scripts.walk_revisions())
        historical = {
            revision.revision for revision in scripts.walk_revisions(head=linear_since)
        }
        for revision in revisions:
            if revision.revision in historical:
                continue
            if not isinstance(revision.down_revision, str):
                raise ValueError(
                    f"Revision {revision.revision} must have exactly one parent; "
                    f"found {revision.down_revision!r}. New merge revisions are "
                    f"not allowed after {linear_since}."
                )
            if revision.dependencies:
                raise ValueError(
                    f"Revision {revision.revision} uses depends_on; "
                    "new migrations must form a single down_revision chain."
                )
        return heads[0]


def main() -> int:
    try:
        head = check_migrations(ScriptDirectory.from_config(Config("alembic.ini")))
    except (ValueError, KeyError, CommandError, RevisionError, UserWarning) as exc:
        print(f"Alembic migration history check failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Alembic migration history OK: one head ({head}), linear after {LINEAR_HISTORY_BASE}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
