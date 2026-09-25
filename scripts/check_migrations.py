#!/usr/bin/env python3
"""Check Alembic topology without running env.py or connecting to a database."""

import argparse
import sys
import warnings
from collections.abc import Sequence
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.script.revision import RevisionError
from alembic.util import CommandError

# This merge reconciles the heads that predate the linear-history policy.
# Its ancestors can contain branches, merges and cross-branch dependencies.
LINEAR_HISTORY_BASE = "2f14222e0d12"


def check_migrations(
    scripts: ScriptDirectory,
    *,
    linear_since: str = LINEAR_HISTORY_BASE,
    base: ScriptDirectory | None = None,
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
        if base is not None:
            candidate = {revision.revision: revision for revision in revisions}
            for previous in base.walk_revisions():
                current = candidate.get(previous.revision)
                if current is None:
                    raise ValueError(
                        f"Existing revision {previous.revision} was removed or renamed."
                    )
                # Use declared metadata: Alembic's branch_labels attribute also
                # includes labels inherited from other revisions in the graph.
                for field in ("down_revision", "depends_on", "branch_labels"):
                    if getattr(current.module, field, None) != getattr(
                        previous.module, field, None
                    ):
                        raise ValueError(
                            f"Existing revision {previous.revision} changed {field}. "
                            "New migrations must extend the previous head without "
                            "rewriting existing revision metadata."
                        )
                if Path(current.path).read_bytes() != Path(previous.path).read_bytes():
                    raise ValueError(
                        f"Existing revision {previous.revision} changed file contents. "
                        "Existing migration files are immutable, including formatting; "
                        "put new operations in a new revision."
                    )
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


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--base-dir",
        help="Base checkout's Alembic directory; also reject changes to existing revisions.",
    )
    args = parser.parse_args(argv)
    try:
        head = check_migrations(
            ScriptDirectory.from_config(Config("alembic.ini")),
            base=ScriptDirectory(args.base_dir) if args.base_dir is not None else None,
        )
    except (
        ValueError,
        KeyError,
        OSError,
        CommandError,
        RevisionError,
        UserWarning,
    ) as exc:
        print(f"Alembic migration history check failed: {exc}", file=sys.stderr)
        return 1
    print(
        f"Alembic migration history OK: one head ({head}), linear after {LINEAR_HISTORY_BASE}."
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
