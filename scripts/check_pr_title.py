#!/usr/bin/env python3
"""Validate a pull-request title against `.github/commit-conventions.toml`.

Stdlib only. Reads the title from a positional file (a `COMMIT_EDITMSG` path),
`--title`, or `$PR_TITLE`, in that order of precedence.

Exit codes:
    0  the title is valid (warnings do not fail)
    1  the title is invalid
    2  the conventions file or the invocation is broken
"""

from __future__ import annotations

import argparse
import os
import re
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

EXIT_OK: Final = 0
EXIT_INVALID: Final = 1
EXIT_CONFIG: Final = 2

# `[codex] `, `[dependabot] ` and friends.
# Spelled in ASCII on purpose. JavaScript `\\w` is ASCII-only and Python's is
# Unicode-aware, so `[\\w.-]` here and in release-drafter.yml would mean two
# different things: `[\u0431\u043e\u0442] feat(ui): x` passed this checker and got no
# labels from the drafter.
BOT_PREFIX: Final = re.compile(r"^\[[A-Za-z0-9._-]+\]\s+")
# GitHub's own revert titles. The inner group is the title being reverted,
# which is where the replacement this checker suggests comes from.
REVERT_WRAPPER: Final = re.compile(r'^Revert\s+"(?P<inner>.*)"\s*$')
# `type`, optional `(scope)`, optional `!`, then a mandatory colon.
PREFIX: Final = re.compile(
    r"^(?P<type>[^\s(!:]+)(?:\((?P<scope>[^)]*)\))?(?P<bang>!)?:(?P<rest>.*)$"
)
# One component of a scope.
SCOPE_PART: Final = re.compile(r"^[a-z0-9]+(?:[_-][a-z0-9]+)*$")

SCOPE_SEPARATOR: Final = "+"


@dataclass(frozen=True, slots=True)
class Violation:
    """One problem with a title.

    `code` is the stable, machine-readable identity. Callers and tests branch on
    `code`; `message` is for humans only and may be reworded at any time.
    """

    code: str
    message: str


@dataclass(frozen=True, slots=True)
class Report:
    """The outcome of checking one title."""

    title: str
    violations: tuple[Violation, ...]
    warnings: tuple[Violation, ...]

    @property
    def ok(self) -> bool:
        return not self.violations

    @property
    def codes(self) -> tuple[str, ...]:
        return tuple(v.code for v in self.violations)


def _quote(values: Sequence[str]) -> str:
    return ", ".join(f"`{value}`" for value in values)


def _check_type(type_: str, conventions: Conventions) -> tuple[Violation | None, bool]:
    """Validate the type. Returns the violation and whether it deprecates."""
    if type_ in conventions.types:
        return None, type_ == conventions.deprecation_type

    if type_ in conventions.type_aliases:
        suggestion = conventions.type_aliases[type_]
        if suggestion:
            hint = f"Use `{suggestion}` instead."
        else:
            hint = "There is no replacement; land finished work only."
        return Violation("unknown-type", f"`{type_}` is not a type. {hint}"), False

    legacy = conventions.legacy_types.get(type_)
    if legacy is not None:
        return (
            Violation(
                "unknown-type",
                f"`{type_}` is retired as a type. Write `{legacy.suggest}` instead.",
            ),
            False,
        )

    return (
        Violation(
            "unknown-type",
            f"`{type_}` is not a type. Allowed: {_quote(sorted(conventions.types))}.",
        ),
        False,
    )


def _check_scope_part(part: str, conventions: Conventions) -> Violation | None:
    """Validate one canonical scope component."""
    if part in conventions.scopes:
        return None

    canonical = conventions.scope_aliases.get(part)
    if canonical is not None:
        return Violation(
            "unknown-scope",
            f"`{part}` is an old spelling of `{canonical}`. Write `{canonical}`.",
        )

    legacy = conventions.legacy_scopes.get(part)
    if legacy is not None:
        return Violation(
            "unknown-scope",
            f"`{part}` is retired. Write `{legacy.suggest}`.",
        )

    candidates = conventions.ambiguous_scopes.get(part)
    if candidates is not None:
        # The note trails the candidates rather than interrupting them. A
        # scope whose other reading takes no scope at all -- `workflows`, where
        # the GitHub Actions answer is a bare `ci:` -- cannot be expressed as a
        # candidate, and reading that before "pick one of these" made the two
        # halves contradict each other.
        note = conventions.ambiguous_scope_notes.get(part)
        detail = f" {note}" if note else ""
        return Violation(
            "ambiguous-scope",
            f"`{part}` is ambiguous. "
            f"Pick the area you actually changed: {_quote(candidates)}.{detail}",
        )

    # Two very different mistakes land here, so the message names both. Calling
    # every unknown scope a vendor misdirects when it is an internal near-miss:
    # `fix(pool): ...` is engine work, and the autolabeler files it under
    # Integrations precisely because nothing claimed it first.
    integrations = conventions.scopes["integrations"]
    return Violation(
        "unknown-scope",
        f"`{part}` is not a scope. If it names a vendor, write "
        f"`feat({integrations}): add {part.replace('_', ' ').title()} issue "
        "search` and put the vendor in the description. If it names part of "
        f"Tracecat, use the scope that covers it: "
        f"{_quote(conventions.canonical_scopes)}.",
    )


def _check_scope(raw: str, conventions: Conventions) -> tuple[Violation, ...]:
    """Validate the whole `(...)` group."""
    if not raw:
        return (
            Violation(
                "empty-scope", "The scope is empty. Drop the `()` or fill it in."
            ),
        )

    if "," in raw or " " in raw:
        return (
            Violation(
                "scope-format",
                f"`{raw}` uses `,` or a space. Join scopes with "
                f"`{SCOPE_SEPARATOR}`, as in `ui{SCOPE_SEPARATOR}api`.",
            ),
        )

    parts = raw.split(SCOPE_SEPARATOR)
    violations: list[Violation] = []

    malformed = [part for part in parts if not SCOPE_PART.match(part)]
    if malformed:
        return (
            Violation(
                "scope-format",
                f"`{raw}` is not lowercase `a-z0-9` with `-` or `_` separators.",
            ),
        )

    if len(parts) > conventions.max_scope_parts:
        violations.append(
            Violation(
                "too-many-scopes",
                f"`{raw}` names {len(parts)} areas; the limit is "
                f"{conventions.max_scope_parts}. More than two usually means the "
                "PR should be split.",
            )
        )

    seen: set[str] = set()
    duplicates: set[str] = set()
    for part in parts:
        if part in seen:
            duplicates.add(part)
        seen.add(part)
    if duplicates:
        violations.append(
            Violation(
                "duplicate-scope", f"`{raw}` repeats {_quote(sorted(duplicates))}."
            )
        )

    for part in dict.fromkeys(parts):
        violation = _check_scope_part(part, conventions)
        if violation is not None:
            violations.append(violation)

    return tuple(violations)


def _canonical_scope(raw: str, conventions: Conventions) -> str | None:
    """The canonical spelling of a quoted title's scope, or `None`.

    An old spelling and a retired one each have a single answer, and it is the
    answer this checker would give anyway. An ambiguous or unknown scope has
    none: `app` and `workflows` are rejected precisely because nothing can pick
    between their two readings, and guessing one into a suggestion is worse
    than handing the shape back.
    """
    parts: list[str] = []
    for part in raw.split(SCOPE_SEPARATOR):
        if part in conventions.scopes:
            resolved = part
        elif (alias := conventions.scope_aliases.get(part)) is not None:
            resolved = alias
        elif (legacy := conventions.legacy_scopes.get(part)) is not None:
            resolved = legacy.suggest
        else:
            return None
        # Two spellings can collapse onto one area: `registry+integration` is
        # `integrations` twice, and naming that would fail `duplicate-scope`.
        if resolved not in parts:
            parts.append(resolved)
    return SCOPE_SEPARATOR.join(parts)


def _revert_suggestion(inner: str, conventions: Conventions) -> str:
    """The `revert(...)` title that should replace a GitHub revert wrapper.

    The quoted title carries the scope, so the author gets the answer rather
    than the rule. The `!` is deliberately not carried over: it described how
    breaking the reverted change was, and the revert is labelled `chore` by the
    autolabeler either way. An author who judges the revert itself breaking can
    add it back.

    Every candidate goes back through `check_title` before it is named. The
    quoted title merged before the cutoff, so nothing ever validated it:
    reverting `feat(registry): add x` verbatim would name
    `revert(registry): add x`, which fails `unknown-scope` and sends the author
    round twice. `test_every_suggestion_is_itself_valid` guards that trap for
    the suggestions that come out of the conventions file; this one is built at
    runtime from an unchecked title, so it is verified rather than assumed.
    """
    body = inner.strip()
    if conventions.allow_bot_prefix:
        body = BOT_PREFIX.sub("", body, count=1)

    match = PREFIX.match(body)
    if match is None:
        return "revert(<scope>): <description>"

    description = match.group("rest").strip()
    # Checked without a scope first: whatever makes `revert: <description>`
    # fail -- an empty description, a trailing period -- fails in every scoped
    # form of it too, and there is then nothing worth carrying over.
    if not description or not check_title(f"revert: {description}", conventions).ok:
        return "revert(<scope>): <description>"

    scope = match.group("scope")
    if not scope:
        return f"revert: {description}"

    canonical = _canonical_scope(scope, conventions)
    if canonical is None:
        return f"revert(<scope>): {description}"

    suggestion = f"revert({canonical}): {description}"
    if not check_title(suggestion, conventions).ok:
        # A scope that resolves and still fails: three areas that stay three
        # after the collapse, say. The description survives, and the scope is
        # the author's to pick.
        return f"revert(<scope>): {description}"
    return suggestion


def check_title(title: str, conventions: Conventions) -> Report:
    """Check one title and collect every violation, not just the first."""
    stripped = title.strip()

    # GitHub stores a title verbatim -- #2856 here is stored as
    # `"fix(agents): custom model name resolution "` -- and every autolabeler
    # rule is anchored at `^`. A title that opens on whitespace therefore gets
    # no labels at all and renders in the draft with no heading, so normalising
    # it away here would put the checker and the labeler into disagreement on
    # the one thing they both key off. A TRAILING space is harmless: nothing
    # anchors on the far end, and the `^(- .*?)[ \t]+$` replacer trims the
    # rendered line before a reader sees it.
    leading: tuple[Violation, ...] = ()
    if title != title.lstrip():
        leading = (
            Violation(
                "leading-whitespace",
                "The title opens on whitespace. GitHub keeps it, and every "
                "autolabeler rule is anchored at the start of the line, so the "
                "pull request would get no labels and no release-notes "
                "heading. Drop the leading space.",
            ),
        )

    # A revert wrapper is generated by GitHub's revert button and quotes a title
    # that was itself checked when it merged. It never gets past the parsing
    # below -- `Revert "fix(agents): x"` has no colon where a prefix would put
    # one -- so it needs its own answer either way: accepted whole under the
    # flag, length included, and otherwise rejected with the replacement spelled
    # out. Falling through would report `missing-prefix`, which describes a
    # different mistake and names a fix that does not apply.
    revert = REVERT_WRAPPER.match(stripped)
    if revert is not None:
        if conventions.allow_revert_wrapper:
            return Report(title=stripped, violations=leading, warnings=())
        suggestion = _revert_suggestion(revert.group("inner"), conventions)
        return Report(
            title=stripped,
            violations=(
                *leading,
                Violation(
                    "revert-wrapper",
                    'GitHub\'s `Revert "..."` title has no type, so the change '
                    "cannot be filed into a release-notes section. Write "
                    f"`{suggestion}` instead.",
                ),
            ),
            warnings=(),
        )

    body = stripped
    if conventions.allow_bot_prefix:
        body = BOT_PREFIX.sub("", body, count=1)

    warnings: list[Violation] = []
    if len(stripped) > conventions.max_length:
        warnings.append(
            Violation(
                "too-long",
                f"{len(stripped)} characters; aim for {conventions.max_length} "
                "or fewer so GitHub does not truncate it.",
            )
        )

    match = PREFIX.match(body)
    if match is None:
        return Report(
            title=stripped,
            violations=(
                *leading,
                Violation(
                    "missing-prefix",
                    "Expected `<type>(<scope>)!: <description>`. A missing colon "
                    "after the scope is the usual cause, as in "
                    "`feat(cases) ENG-1597: ...`.",
                ),
            ),
            warnings=tuple(warnings),
        )

    type_ = match.group("type")
    raw_scope = match.group("scope")
    rest = match.group("rest")

    violations: list[Violation] = list(leading)

    type_violation, is_depr = _check_type(type_, conventions)
    if type_violation is not None:
        violations.append(type_violation)

    if raw_scope is not None:
        violations.extend(_check_scope(raw_scope, conventions))

    if not rest.startswith(" ") and rest:
        violations.append(Violation("missing-space", "Put a space after the colon."))
    description = rest.strip()

    if not description:
        violations.append(Violation("empty-description", "The description is empty."))
    else:
        if description.endswith("."):
            violations.append(
                Violation(
                    "trailing-period",
                    "Drop the trailing period; the title is a headline, not a "
                    "sentence.",
                )
            )
        if is_depr and not _names_replacement(description, conventions):
            violations.append(
                Violation(
                    "depr-no-replacement",
                    "A deprecation must say what to use instead. Add "
                    f"{_quote(conventions.replacement_markers)}, or say so "
                    "explicitly.",
                )
            )

    return Report(
        title=stripped, violations=tuple(violations), warnings=tuple(warnings)
    )


def _names_replacement(description: str, conventions: Conventions) -> bool:
    """Whether the description points at something to move to.

    A marker on its own is not enough. `deprecation(api): use` and a title that
    trails off at `replaced by` both match a marker while naming nothing, so a
    pointing marker has to be followed by at least one more word. A word, not
    just a character: `replaced by ?` points at nothing either, so the token
    after the marker has to hold a word character.

    Terminal phrases are exempt from that, but only where they end the title.
    `with no replacement` IS the statement; `with no replacement yet` is a
    promise, and a title that trails off mid-sentence still names nothing.

    This stays a nudge, not a proof. Nothing here can tell `use tools.y` from
    `use the old endpoint` — that needs a reader, not a regex.
    """
    if any(
        re.search(rf"\b{re.escape(marker.strip())}\b\s*$", description, re.IGNORECASE)
        for marker in conventions.terminal_markers
    ):
        return True
    return any(
        re.search(
            rf"\b{re.escape(marker.strip())}\b\s+(?=\S*\w)", description, re.IGNORECASE
        )
        for marker in conventions.replacement_markers
    )


def render(report: Report) -> str:
    """Human-readable report body, also used for the job summary."""
    lines: list[str] = []
    if report.ok:
        lines.append(f"PR title OK: {report.title}")
    else:
        lines.append(f"PR title is not valid: {report.title}")
        lines.append("")
        for violation in report.violations:
            lines.append(f"  [{violation.code}] {violation.message}")
    for warning in report.warnings:
        lines.append(f"  [{warning.code}] warning: {warning.message}")
    if not report.ok:
        lines.append("")
        lines.append(
            "Format: <type>(<scope>)!: <description>. "
            "See CONTRIBUTING.md, or run "
            "`uv run python scripts/check_pr_title.py --list`."
        )
    return "\n".join(lines)


def render_taxonomy(conventions: Conventions) -> str:
    """The full vocabulary, for `--list`."""
    lines = ["Types (type -> label):"]
    lines += [
        f"  {name:<10} {label}" for name, label in sorted(conventions.types.items())
    ]

    lines.append("")
    lines.append("Scopes (scope -> label):")
    lines += [
        f"  {name:<14} {label}" for name, label in sorted(conventions.scopes.items())
    ]

    lines.append("")
    lines.append("Old spellings (rewrite these):")
    for canonical in sorted(conventions.scopes):
        aliases = sorted(
            alias
            for alias, target in conventions.scope_aliases.items()
            if target == canonical
        )
        if aliases:
            lines.append(f"  {canonical:<14} <- {', '.join(aliases)}")

    lines.append("")
    lines.append("Ambiguous, never auto-mapped:")
    for name, candidates in sorted(conventions.ambiguous_scopes.items()):
        note = conventions.ambiguous_scope_notes.get(name)
        suffix = f"  # {note}" if note else ""
        lines.append(f"  {name:<14} -> {', '.join(candidates)}{suffix}")

    lines.append("")
    lines.append(
        "Any other scope is read as a vendor name and absorbed into "
        f"`{conventions.scopes['integrations']}`; name the vendor in the "
        "description instead."
    )
    lines.append(f"Breaking: `!` before the colon -> `{conventions.breaking_label}`.")
    lines.append(f"Excluded from release notes: {_quote(conventions.exclude_labels)}.")
    return "\n".join(lines)


def resolve_title(args: argparse.Namespace) -> str | None:
    """Positional file, then `--title`, then `$PR_TITLE`."""
    if args.file is not None:
        try:
            text = Path(args.file).read_text(encoding="utf-8")
        except OSError as exc:
            raise ConventionsError(f"cannot read {args.file}: {exc}") from exc
        for line in text.splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                return line
        return ""
    if args.title is not None:
        return args.title
    return os.environ.get("PR_TITLE")


def _write_summary(text: str) -> None:
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary:
        return
    try:
        with open(summary, "a", encoding="utf-8") as handle:
            handle.write(f"```\n{text}\n```\n")
    except OSError:
        # A broken summary path must not fail the check itself.
        pass


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="check_pr_title.py",
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("file", nargs="?", help="path to a COMMIT_EDITMSG-style file")
    parser.add_argument("--title", help="the title to check")
    parser.add_argument(
        "--list", action="store_true", help="print the type and scope taxonomy"
    )
    parser.add_argument(
        "--config", type=Path, default=None, help="path to commit-conventions.toml"
    )
    args = parser.parse_args(argv)

    try:
        conventions = load_conventions(args.config)
        if args.list:
            print(render_taxonomy(conventions))
            return EXIT_OK
        title = resolve_title(args)
    except ConventionsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return EXIT_CONFIG

    if title is None:
        print(
            "error: no title given. Pass a file, --title, or set PR_TITLE.",
            file=sys.stderr,
        )
        return EXIT_CONFIG

    report = check_title(title, conventions)
    for violation in report.violations:
        print(f"::error title=PR title::[{violation.code}] {violation.message}")
    for warning in report.warnings:
        print(f"::warning title=PR title::[{warning.code}] {warning.message}")

    body = render(report)
    print(body)
    _write_summary(body)
    return EXIT_OK if report.ok else EXIT_INVALID


if __name__ == "__main__":
    raise SystemExit(main())
