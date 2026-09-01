#!/usr/bin/env python3
"""Audit merged pull-request history against `.github/commit-conventions.toml`.

A reporting tool for humans. It never mutates labels, the conventions file,
or `release-drafter.yml` — it only reads them and prints findings.

Four subcommands:

    labels     compare labels the config can produce against the repo's
               actual labels (`gh label list`)
    prefixes   tabulate observed `type`/`scope` frequencies in merged PR
               titles, with each value's status under the current config
    backfill   find merged PRs whose GitHub labels are missing one or more
               labels the current config would assign
    sections   show how merged PRs distribute across the release-notes
               categories in `release-drafter.yml`

Label resolution here mirrors the lenient autolabeler semantics that
`release-drafter.yml` encodes as regexes, not the strict checks in
`scripts/check_pr_title.py` (which this module does not import).

Requires the `gh` CLI, authenticated against GitHub.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Final

sys.path.insert(0, str(Path(__file__).resolve().parent))

from commit_conventions import (  # noqa: E402
    Conventions,
    ConventionsError,
    load_conventions,
)

DEFAULT_REPO: Final = "TracecatHQ/tracecat"
DEFAULT_LIMIT: Final = 500

RELEASE_DRAFTER_PATH: Final = (
    Path(__file__).resolve().parents[1] / ".github" / "release-drafter.yml"
)

# Bucket key for a title the grammar below cannot parse at all.
UNPARSEABLE_KEY: Final = "(unparseable)"

EXIT_OK: Final = 0
EXIT_CONFIG: Final = 2

# `[codex] `, `[dependabot] ` and friends. Mirrors check_pr_title.py's BOT_PREFIX.
_BOT_PREFIX: Final = re.compile(r"^\[[\w.-]+\]\s+")
# GitHub's own revert titles. Mirrors check_pr_title.py's REVERT_WRAPPER.
_REVERT_WRAPPER: Final = re.compile(r'^Revert\s+".*"\s*$')
# `type`, optional `(scope)`, optional `!`, then a mandatory colon.
# Mirrors check_pr_title.py's PREFIX.
_PREFIX: Final = re.compile(
    r"^(?P<type>[^\s(!:]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:(?P<rest>.*)$"
)
_SCOPE_SEPARATOR: Final = "+"


@dataclass(frozen=True, slots=True)
class PullRequest:
    """A merged pull request, as reported by `gh pr list`."""

    number: int
    title: str
    labels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ParsedTitle:
    """A PR title parsed under the lenient autolabeler grammar."""

    type: str
    scope_parts: tuple[str, ...]
    breaking: bool
    # GitHub's own `Revert "..."` wrapper. The autolabeler files these under
    # `chore`, not `revert`, so the flag has to survive parsing.
    revert_wrapper: bool = False


@dataclass(frozen=True, slots=True)
class ObservedItem:
    """One observed `type` or `scope` value and its status under the config."""

    value: str
    count: int
    status: str


@dataclass(frozen=True, slots=True)
class BackfillResult:
    """A merged PR whose GitHub labels are missing config-derived labels."""

    number: int
    title: str
    missing_labels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Category:
    """One release-notes section from `release-drafter.yml`."""

    title: str
    labels: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ReleaseDrafterConfig:
    """The parts of `release-drafter.yml` this script reads.

    Read at runtime rather than hardcoded: the category list is expected to
    change independently of this script.
    """

    categories: tuple[Category, ...]
    exclude_labels: tuple[str, ...]

    def all_labels(self) -> frozenset[str]:
        """Every label named anywhere in this config."""
        labels: set[str] = set(self.exclude_labels)
        for category in self.categories:
            labels.update(category.labels)
        return frozenset(labels)


def parse_title(title: str, conventions: Conventions) -> ParsedTitle | None:
    """Parse a PR title under the lenient autolabeler grammar.

    Grammar: an optional bracketed bot prefix, then either a `Revert "..."`
    wrapper (type `revert`, no scope) or `type(scope)!: description`, where
    `scope` and `!` are optional and `scope` may be a `+`-joined compound.
    Returns None when the title matches none of this.
    """
    stripped = title.strip()
    if conventions.allow_revert_wrapper and _REVERT_WRAPPER.match(stripped):
        return ParsedTitle(
            type="revert", scope_parts=(), breaking=False, revert_wrapper=True
        )

    body = stripped
    if conventions.allow_bot_prefix:
        body = _BOT_PREFIX.sub("", body, count=1)

    match = _PREFIX.match(body)
    if match is None:
        return None

    type_ = match.group("type")
    raw_scope = match.group("scope") or ""
    scope_parts = tuple(part for part in raw_scope.split(_SCOPE_SEPARATOR) if part)
    breaking = match.group("bang") == "!"
    return ParsedTitle(type=type_, scope_parts=scope_parts, breaking=breaking)


def classify_type(type_: str, conventions: Conventions) -> str:
    """Status of an observed `type` token under the current config."""
    if type_ in conventions.types:
        return "canonical"
    if type_ in conventions.type_aliases:
        target = conventions.type_aliases[type_]
        return f"alias-of-{target}" if target else "alias-of-(none)"
    if type_ in conventions.legacy_types:
        return "legacy"
    return "unparseable"


def classify_scope(scope: str, conventions: Conventions) -> str:
    """Status of an observed scope token under the current config."""
    if scope in conventions.canonical_scopes:
        return "canonical"
    if scope in conventions.scope_aliases:
        return f"alias-of-{conventions.scope_aliases[scope]}"
    if scope in conventions.legacy_scopes:
        return f"retired-use-{conventions.legacy_scopes[scope].suggest}"
    if scope in conventions.ambiguous_scopes:
        return "ambiguous"
    return "vendor(absorbed into integrations)"


# The scope shape the drafter's own regexes accept. A component outside it
# cannot match any autolabeler rule, vendor fallback included.
DRAFTER_SCOPE: Final = re.compile(r"[a-z0-9_-]+")


def resolve_labels(parsed: ParsedTitle, conventions: Conventions) -> frozenset[str]:
    """Labels the current config would assign to a parsed title.

    Lenient autolabeler semantics, mirroring the regexes in
    `release-drafter.yml`: a type label (canonical, else legacy), a breaking
    label when `!` is present, and one area label per scope component
    (canonical directly, else via alias, else no label if ambiguous, else
    the vendor fallback `integrations`).
    """
    if parsed.revert_wrapper:
        return frozenset({conventions.types["chore"]})

    labels: set[str] = set()

    type_label = conventions.types.get(parsed.type)
    if type_label is None:
        legacy = conventions.legacy_types.get(parsed.type)
        if legacy is not None:
            type_label = legacy.label
    if type_label is not None:
        labels.add(type_label)

    if parsed.breaking:
        labels.add(conventions.breaking_label)

    vendor_label = conventions.scopes["integrations"]
    for part in parsed.scope_parts:
        label = conventions.scope_label(part, type_=parsed.type)
        if label is None:
            canonical = conventions.scope_aliases.get(part)
            if canonical is not None:
                label = conventions.scope_label(canonical, type_=parsed.type)
        if label is None:
            legacy = conventions.legacy_scopes.get(part)
            if legacy is not None:
                label = legacy.label
        if label is not None:
            labels.add(label)
        # Two ways the drafter declines to absorb an unknown scope, both
        # mirrored deliberately rather than improved on. Its vendor-fallback
        # lookahead cannot span `+`, so a compound scope containing an unknown
        # component gets no integrations label. And its scope group is
        # `[a-z0-9_-]+`, so a malformed scope matches nothing at all: there are
        # 7 in this repo's history, `docs(self-hosting, cheatsheets)` among
        # them. Absorbing those here would label PRs the drafter never touches.
        elif (
            len(parsed.scope_parts) == 1
            and part not in conventions.ambiguous_scopes
            and DRAFTER_SCOPE.fullmatch(part)
        ):
            labels.add(vendor_label)

    return frozenset(labels)


def load_release_drafter(path: Path) -> ReleaseDrafterConfig:
    """Read `categories:` and `exclude-labels:` from `release-drafter.yml`."""
    # Keep this import function-local: the dependency-free backfill job runs
    # `labels-for` without third-party packages installed.
    import yaml

    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        print(f"error: cannot read {path}: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_CONFIG) from exc

    try:
        raw = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        print(f"error: cannot parse {path}: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_CONFIG) from exc

    if not isinstance(raw, dict):
        print(f"error: {path} does not contain a mapping", file=sys.stderr)
        raise SystemExit(EXIT_CONFIG)

    categories: list[Category] = []
    for entry in raw.get("categories", []):
        title = entry.get("title") if isinstance(entry, dict) else None
        raw_labels = entry.get("labels", []) if isinstance(entry, dict) else None
        if (
            not isinstance(title, str)
            or not isinstance(raw_labels, list)
            or not all(isinstance(label, str) for label in raw_labels)
        ):
            print(f"error: {path} has a malformed `categories` entry", file=sys.stderr)
            raise SystemExit(EXIT_CONFIG)
        categories.append(Category(title=title, labels=tuple(raw_labels)))

    raw_exclude = raw.get("exclude-labels", [])
    if not isinstance(raw_exclude, list) or not all(
        isinstance(label, str) for label in raw_exclude
    ):
        print(f"error: {path} has a malformed `exclude-labels`", file=sys.stderr)
        raise SystemExit(EXIT_CONFIG)

    return ReleaseDrafterConfig(
        categories=tuple(categories), exclude_labels=tuple(raw_exclude)
    )


def _run_gh(argv: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a read-only `gh` command, exiting the process on failure."""
    try:
        result = subprocess.run(argv, capture_output=True, text=True, check=False)
    except FileNotFoundError as exc:
        print("error: `gh` is not installed or not on PATH.", file=sys.stderr)
        raise SystemExit(EXIT_CONFIG) from exc
    if result.returncode != 0:
        print(f"error: `{' '.join(argv)}` failed:", file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(EXIT_CONFIG)
    return result


def fetch_merged_prs(repo: str, limit: int) -> list[PullRequest]:
    """Fetch merged PRs via `gh pr list`."""
    argv = [
        "gh",
        "pr",
        "list",
        "--repo",
        repo,
        "--state",
        "merged",
        "--limit",
        str(limit),
        "--json",
        "number,title,labels",
    ]
    result = _run_gh(argv)
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"error: could not parse `gh pr list` output: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_CONFIG) from exc

    prs: list[PullRequest] = []
    for entry in raw:
        label_names = tuple(sorted(label["name"] for label in entry.get("labels", [])))
        prs.append(
            PullRequest(
                number=entry["number"], title=entry["title"], labels=label_names
            )
        )
    return prs


def fetch_repo_labels(repo: str) -> frozenset[str]:
    """Fetch the repo's actual label names via `gh label list`."""
    argv = ["gh", "label", "list", "--repo", repo, "--limit", "200", "--json", "name"]
    result = _run_gh(argv)
    try:
        raw = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        print(f"error: could not parse `gh label list` output: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_CONFIG) from exc
    return frozenset(entry["name"] for entry in raw)


def tabulate_types(
    prs: list[PullRequest], conventions: Conventions
) -> list[ObservedItem]:
    """Frequency and status of every observed `type` token, including unparseable."""
    counts: dict[str, int] = {}
    for pr in prs:
        parsed = parse_title(pr.title, conventions)
        key = parsed.type if parsed is not None else UNPARSEABLE_KEY
        counts[key] = counts.get(key, 0) + 1
    items = [
        ObservedItem(value=key, count=count, status=classify_type(key, conventions))
        for key, count in counts.items()
    ]
    return sorted(items, key=lambda item: (-item.count, item.value))


def tabulate_scopes(
    prs: list[PullRequest], conventions: Conventions
) -> list[ObservedItem]:
    """Frequency and status of every observed scope component in parseable titles."""
    counts: dict[str, int] = {}
    for pr in prs:
        parsed = parse_title(pr.title, conventions)
        if parsed is None:
            continue
        for part in parsed.scope_parts:
            counts[part] = counts.get(part, 0) + 1
    items = [
        ObservedItem(value=key, count=count, status=classify_scope(key, conventions))
        for key, count in counts.items()
    ]
    return sorted(items, key=lambda item: (-item.count, item.value))


def cmd_labels(args: argparse.Namespace, conventions: Conventions) -> int:
    """Compare labels the config can produce against the repo's actual labels."""
    release_drafter = load_release_drafter(RELEASE_DRAFTER_PATH)
    config_labels = set(conventions.all_labels()) | release_drafter.all_labels()
    repo_labels = fetch_repo_labels(args.repo)

    missing = sorted(config_labels - repo_labels)
    unused = sorted(repo_labels - config_labels)
    shared = sorted(config_labels & repo_labels)

    print(
        f"Labels the config references but {args.repo} does not have "
        f"({len(missing)}) — these silently drop a category:"
    )
    for label in missing:
        print(f"  {label}")

    print()
    print(
        f"Labels {args.repo} has but the config never references "
        f"({len(unused)}) — candidates for deletion:"
    )
    for label in unused:
        print(f"  {label}")

    print()
    print(f"Labels present in both ({len(shared)}):")
    for label in shared:
        print(f"  {label}")

    return EXIT_OK


def cmd_prefixes(args: argparse.Namespace, conventions: Conventions) -> int:
    """Tabulate observed type/scope frequencies in merged PR titles."""
    prs = fetch_merged_prs(args.repo, args.limit)
    types = tabulate_types(prs, conventions)
    scopes = tabulate_scopes(prs, conventions)

    print(f"Types observed across {len(prs)} merged PRs from {args.repo}:")
    for item in types:
        print(f"  {item.count:>5}  {item.value:<20} {item.status}")

    print()
    scope_total = sum(item.count for item in scopes)
    print(f"Scopes observed ({scope_total} scope occurrences across those PRs):")
    for item in scopes:
        print(f"  {item.count:>5}  {item.value:<20} {item.status}")

    return EXIT_OK


def cmd_backfill(args: argparse.Namespace, conventions: Conventions) -> int:
    """Report merged PRs missing labels the current config would assign."""
    if args.apply:
        print(
            "error: --apply refuses to run. Applying labels needs human "
            "approval — copy the printed `gh api` commands and run them "
            "yourself.",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    prs = fetch_merged_prs(args.repo, args.limit)
    results: list[BackfillResult] = []
    for pr in prs:
        parsed = parse_title(pr.title, conventions)
        if parsed is None:
            continue
        computed = resolve_labels(parsed, conventions)
        missing = tuple(sorted(computed - set(pr.labels)))
        if missing:
            results.append(
                BackfillResult(number=pr.number, title=pr.title, missing_labels=missing)
            )

    for result in results:
        print(f"#{result.number} {result.title}")
        print(f"  missing: {', '.join(result.missing_labels)}")

    print()
    print(
        f"{len(results)} of {len(prs)} merged PRs are missing at least one "
        "config-derived label."
    )

    if results:
        print()
        print("Commands a human could run to apply them:")
        for result in results:
            for label in result.missing_labels:
                print(
                    f"gh api repos/{args.repo}/issues/{result.number}/labels "
                    f'-f "labels[]={label}"'
                )

    return EXIT_OK


def cmd_labels_for(args: argparse.Namespace, conventions: Conventions) -> int:
    """Print the labels one title resolves to, one per line.

    A pure function of the title and the config: it makes no network calls and
    mutates nothing. `.github/workflows/release-drafter.yml` uses it to backfill
    labels onto a squash-merged PR.
    """
    parsed = parse_title(args.title, conventions)
    if parsed is None:
        return EXIT_OK
    for label in sorted(resolve_labels(parsed, conventions)):
        print(label)
    return EXIT_OK


def cmd_sections(args: argparse.Namespace, conventions: Conventions) -> int:
    """Show how merged PRs distribute across release-drafter categories.

    Counts each PR under the union of the labels it already carries and the
    labels the config derives from its title. That union is the state the
    backfill job converges on, since it only ever adds labels, so it is what
    the release notes will actually contain. Counting derived labels alone
    understates every section a human has been hand-labelling.
    """
    release_drafter = load_release_drafter(RELEASE_DRAFTER_PATH)
    prs = fetch_merged_prs(args.repo, args.limit)

    exclude = set(release_drafter.exclude_labels)
    counts: dict[str, int] = {
        category.title: 0 for category in release_drafter.categories
    }
    excluded_count = 0
    uncategorized_count = 0

    for pr in prs:
        parsed = parse_title(pr.title, conventions)
        derived = (
            resolve_labels(parsed, conventions) if parsed is not None else frozenset()
        )
        labels = derived | set(pr.labels)

        if labels & exclude:
            excluded_count += 1
            continue

        for category in release_drafter.categories:
            if labels & set(category.labels):
                counts[category.title] += 1
                break
        else:
            uncategorized_count += 1

    print(
        f"Sections for {len(prs)} merged PRs from {args.repo} "
        "(existing labels plus config-derived ones):"
    )
    for category in release_drafter.categories:
        print(f"  {counts[category.title]:>5}  {category.title}")
    print(f"  {uncategorized_count:>5}  (uncategorized)")

    print()
    print(f"Excluded by exclude-labels before categorizing: {excluded_count}")

    return EXIT_OK


def _build_common_parser(*, subcommand: bool) -> argparse.ArgumentParser:
    """`--repo`/`--limit`, shared by the top-level parser and every subcommand.

    Accepting them at both levels lets `--repo X prefixes` and
    `prefixes --repo X` both work.

    The subcommand copies must not carry defaults. argparse applies a
    subparser's defaults after the top-level parse, so a real default there
    silently overwrites `--limit 5 prefixes` back to DEFAULT_LIMIT, and the
    command reports the wrong row count with no error. SUPPRESS makes the
    subparser set the attribute only when the flag is actually given.
    """
    repo_default = argparse.SUPPRESS if subcommand else DEFAULT_REPO
    limit_default = argparse.SUPPRESS if subcommand else DEFAULT_LIMIT

    common = argparse.ArgumentParser(add_help=False)
    common.add_argument(
        "--repo",
        default=repo_default,
        help=f"GitHub repo to audit (default: {DEFAULT_REPO})",
    )
    common.add_argument(
        "--limit",
        type=int,
        default=limit_default,
        help=f"number of merged PRs to fetch (default: {DEFAULT_LIMIT})",
    )
    return common


def build_parser() -> argparse.ArgumentParser:
    """Build the top-level argument parser."""
    common = _build_common_parser(subcommand=False)
    sub_common = _build_common_parser(subcommand=True)
    parser = argparse.ArgumentParser(
        prog="audit_commit_conventions.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
        parents=[common],
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser(
        "labels",
        parents=[sub_common],
        help="compare config labels against the repo's actual labels",
    )
    subparsers.add_parser(
        "prefixes",
        parents=[sub_common],
        help="tabulate observed type/scope frequencies in merged PR titles",
    )

    backfill_parser = subparsers.add_parser(
        "backfill",
        parents=[sub_common],
        help="find merged PRs missing config-derived labels",
    )
    backfill_parser.add_argument(
        "--apply",
        action="store_true",
        help="refused: label mutation needs human approval",
    )

    labels_for_parser = subparsers.add_parser(
        "labels-for",
        parents=[sub_common],
        help="print the labels the config derives from one title, one per line",
    )
    labels_for_parser.add_argument("title", help="the PR title to resolve")

    subparsers.add_parser(
        "sections",
        parents=[sub_common],
        help="show how merged PRs distribute across release-notes categories",
    )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: dispatch to the selected subcommand."""
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        conventions = load_conventions()
    except ConventionsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    if args.command == "labels":
        return cmd_labels(args, conventions)
    if args.command == "prefixes":
        return cmd_prefixes(args, conventions)
    if args.command == "backfill":
        return cmd_backfill(args, conventions)
    if args.command == "labels-for":
        return cmd_labels_for(args, conventions)
    if args.command == "sections":
        return cmd_sections(args, conventions)

    parser.error(f"unknown command: {args.command}")
    return EXIT_CONFIG


if __name__ == "__main__":
    raise SystemExit(main())
