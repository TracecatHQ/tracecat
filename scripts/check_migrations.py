#!/usr/bin/env python3
"""Check Alembic topology without running env.py or connecting to a database."""

import argparse
import sys
import warnings
from collections.abc import Mapping, Sequence
from pathlib import Path

from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.script.revision import RevisionError
from alembic.util import CommandError

# This merge reconciles the heads that predate the linear-history policy.
# Its ancestors can contain branches, merges and cross-branch dependencies.
LINEAR_HISTORY_BASE = "2f14222e0d12"

# The 1.1.0-alpha.2.1 release commit re-parented 9680c861644a onto
# bc3124ad3437 on its release branch only. Main kept both as siblings of
# a7c3e9f1b2d4, so databases upgraded with alpha.2.1 failed later upgrades with
# column "email_claimed_at" already exists. These one-time rewrites make main
# match the shipped graph. They become inert once the rewrite is on every base
# and may then be deleted.
AUDITED_REWRITES: Mapping[str, tuple[tuple[bytes, bytes], ...]] = {
    "9680c861644a": (
        (b"Revises: a7c3e9f1b2d4\n", b"Revises: bc3124ad3437\n"),
        (
            b'down_revision = "a7c3e9f1b2d4"\n',
            b'down_revision = "bc3124ad3437"\n',
        ),
    ),
    "b4e8f2a6c1d9": (
        (
            b"Revises: 9680c861644a, bc3124ad3437\n",
            b"Revises: 9680c861644a\n",
        ),
        (
            b'down_revision: tuple[str, str] | None = ("9680c861644a", "bc3124ad3437")\n',
            b'down_revision: str | None = "9680c861644a"\n',
        ),
    ),
    "8c0e18190001": (
        (
            b"Revises: 391f391da70b, bc3124ad3437\n",
            b"Revises: 391f391da70b\n",
        ),
        (
            b'down_revision = ("391f391da70b", "bc3124ad3437")\n',
            b'down_revision = "391f391da70b"\n',
        ),
    ),
}


def check_migrations(
    scripts: ScriptDirectory,
    *,
    linear_since: str = LINEAR_HISTORY_BASE,
    base: ScriptDirectory | None = None,
    audited_rewrites: Mapping[str, tuple[tuple[bytes, bytes], ...]] = AUDITED_REWRITES,
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
                audited_rewrite = audited_rewrites.get(previous.revision)
                fields = (
                    ("depends_on", "branch_labels")
                    if audited_rewrite is not None
                    else ("down_revision", "depends_on", "branch_labels")
                )
                for field in fields:
                    if getattr(current.module, field, None) != getattr(
                        previous.module, field, None
                    ):
                        raise ValueError(
                            f"Existing revision {previous.revision} changed {field}. "
                            "New migrations must extend the previous head without "
                            "rewriting existing revision metadata."
                        )
                current_bytes = Path(current.path).read_bytes()
                base_bytes = Path(previous.path).read_bytes()
                if audited_rewrite is not None:
                    expected_bytes = base_bytes
                    for old_line, new_line in audited_rewrite:
                        old_count = expected_bytes.count(old_line)
                        if old_count == 1:
                            expected_bytes = expected_bytes.replace(
                                old_line, new_line, 1
                            )
                        elif old_count == 0 and expected_bytes.count(new_line) == 1:
                            continue
                        else:
                            raise ValueError(
                                f"The audited rewrite for {previous.revision} no "
                                "longer matches the base file."
                            )
                    if current_bytes != expected_bytes:
                        raise ValueError(
                            f"Existing revision {previous.revision} changed file "
                            "contents beyond its audited rewrite."
                        )
                elif current_bytes != base_bytes:
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
