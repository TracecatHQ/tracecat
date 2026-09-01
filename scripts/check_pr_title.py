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
BOT_PREFIX: Final = re.compile(r"^\[[\w.-]+\]\s+")
# GitHub's own revert titles.
REVERT_WRAPPER: Final = re.compile(r'^Revert\s+".*"\s*$')
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
    """Validate the type. Returns the violation and whether it is `depr`."""
    if type_ in conventions.types:
        return None, type_ == "depr"

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
    if part in conventions.scopes or part in conventions.scopes_by_type:
        return None

    canonical = conventions.scope_aliases.get(part)
    if canonical is not None:
        return Violation(
            "unknown-scope",
            f"`{part}` is an old spelling of `{canonical}`. Write `{canonical}`.",
        )

    candidates = conventions.ambiguous_scopes.get(part)
    if candidates is not None:
        note = conventions.ambiguous_scope_notes.get(part)
        detail = f" ({note})" if note else ""
        return Violation(
            "ambiguous-scope",
            f"`{part}` is ambiguous{detail}. "
            f"Pick the area you actually changed: {_quote(candidates)}.",
        )

    integrations = conventions.scopes["integrations"]
    return Violation(
        "unknown-scope",
        f"`{part}` is a vendor name, not a scope. "
        f"Write `feat({integrations}): add {part.replace('_', ' ').title()} "
        "issue search` and name the vendor in the description.",
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


def check_title(title: str, conventions: Conventions) -> Report:
    """Check one title and collect every violation, not just the first."""
    stripped = title.strip()

    # A revert wrapper is generated by GitHub and quotes a title that was itself
    # checked when it merged. Accept it whole, length included.
    if conventions.allow_revert_wrapper and REVERT_WRAPPER.match(stripped):
        return Report(title=stripped, violations=(), warnings=())

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

    violations: list[Violation] = []

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
    lowered = description.lower()
    return any(marker in lowered for marker in conventions.replacement_markers)


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
    for name, by_type in sorted(conventions.scopes_by_type.items()):
        rendered = ", ".join(f"{k}={v}" for k, v in sorted(by_type.items()))
        lines.append(f"  {name:<14} {rendered}")

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
