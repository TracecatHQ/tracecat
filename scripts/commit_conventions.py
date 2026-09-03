"""Loader for `.github/commit-conventions.toml`.

Stdlib only, so the PR-title checker stays dependency-free and can run as a
required status check without installing the project.
"""

from __future__ import annotations

import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Final

CONVENTIONS_PATH: Final = (
    Path(__file__).resolve().parents[1] / ".github" / "commit-conventions.toml"
)


class ConventionsError(Exception):
    """The conventions file is missing, unparseable, or internally inconsistent."""


@dataclass(frozen=True, slots=True)
class LegacyType:
    """A type that survives only in history.

    The autolabeler maps it so old pull requests keep their release-notes
    section; the checker rejects it and names `suggest` instead.
    """

    label: str
    suggest: str


@dataclass(frozen=True, slots=True)
class Conventions:
    """The commit-convention vocabulary, validated at load time."""

    max_length: int
    max_scope_parts: int
    allow_bot_prefix: bool
    allow_revert_wrapper: bool
    types: Mapping[str, str]
    type_aliases: Mapping[str, str]
    legacy_types: Mapping[str, LegacyType]
    legacy_scopes: Mapping[str, LegacyType]
    scopes: Mapping[str, str]
    scope_aliases: Mapping[str, str]
    ambiguous_scopes: Mapping[str, tuple[str, ...]]
    ambiguous_scope_notes: Mapping[str, str]
    deprecation_type: str
    replacement_markers: tuple[str, ...]
    terminal_markers: tuple[str, ...]
    breaking_label: str
    extra_labels: tuple[str, ...]
    exclude_labels: tuple[str, ...]
    no_section_labels: tuple[str, ...]

    @property
    def canonical_scopes(self) -> tuple[str, ...]:
        """Scopes an author may write."""
        return tuple(sorted(self.scopes))

    def scope_label(self, scope: str) -> str | None:
        """Area label for a canonical scope, or None if it is not one."""
        return self.scopes.get(scope)

    def all_labels(self) -> frozenset[str]:
        """Every label the system can put on a pull request."""
        return frozenset(
            {
                *self.types.values(),
                *(legacy.label for legacy in self.legacy_types.values()),
                *(legacy.label for legacy in self.legacy_scopes.values()),
                *self.scopes.values(),
                self.breaking_label,
                *self.extra_labels,
                *self.exclude_labels,
            }
        )


def _require_str_map(data: Mapping[str, object], key: str) -> dict[str, str]:
    section = data.get(key, {})
    if not isinstance(section, dict):
        raise ConventionsError(f"[{key}] must be a table")
    out: dict[str, str] = {}
    for name, value in section.items():
        if not isinstance(value, str):
            raise ConventionsError(f"[{key}].{name} must be a string")
        out[name] = value
    return out


def _require_str_list(
    data: Mapping[str, object], section: str, key: str
) -> tuple[str, ...]:
    table = data.get(section, {})
    if not isinstance(table, dict):
        raise ConventionsError(f"[{section}] must be a table")
    value = table.get(key, [])
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConventionsError(f"[{section}].{key} must be a list of strings")
    return tuple(value)  # pyright: ignore[reportUnknownArgumentType]


def load_conventions(path: Path | None = None) -> Conventions:
    """Read and validate the conventions file.

    Raises:
        ConventionsError: the file is missing, malformed, or self-inconsistent.
    """
    source = path or CONVENTIONS_PATH
    try:
        raw = tomllib.loads(source.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConventionsError(f"conventions file not found: {source}") from exc
    except (tomllib.TOMLDecodeError, OSError) as exc:
        raise ConventionsError(f"cannot read {source}: {exc}") from exc

    title = raw.get("title", {})
    if not isinstance(title, dict):
        raise ConventionsError("[title] must be a table")

    legacy_raw = raw.get("legacy_types", {})
    if not isinstance(legacy_raw, dict):
        raise ConventionsError("[legacy_types] must be a table")
    legacy_types: dict[str, LegacyType] = {}
    for name, entry in legacy_raw.items():
        if not isinstance(entry, dict):
            raise ConventionsError(f"[legacy_types.{name}] must be a table")
        label = entry.get("label")
        suggest = entry.get("suggest")
        if not isinstance(label, str) or not isinstance(suggest, str):
            raise ConventionsError(
                f"[legacy_types.{name}] needs string `label` and `suggest`"
            )
        legacy_types[name] = LegacyType(label=label, suggest=suggest)

    legacy_scope_raw = raw.get("legacy_scopes", {})
    if not isinstance(legacy_scope_raw, dict):
        raise ConventionsError("[legacy_scopes] must be a table")
    legacy_scopes: dict[str, LegacyType] = {}
    for name, entry in legacy_scope_raw.items():
        if not isinstance(entry, dict):
            raise ConventionsError(f"[legacy_scopes.{name}] must be a table")
        label = entry.get("label")
        suggest = entry.get("suggest")
        if not isinstance(label, str) or not isinstance(suggest, str):
            raise ConventionsError(
                f"[legacy_scopes.{name}] needs string `label` and `suggest`"
            )
        legacy_scopes[name] = LegacyType(label=label, suggest=suggest)

    ambiguous_raw = raw.get("ambiguous_scopes", {})
    if not isinstance(ambiguous_raw, dict):
        raise ConventionsError("[ambiguous_scopes] must be a table")
    ambiguous: dict[str, tuple[str, ...]] = {}
    for name, entry in ambiguous_raw.items():
        if not isinstance(entry, list) or not all(
            isinstance(item, str) for item in entry
        ):
            raise ConventionsError(
                f"[ambiguous_scopes].{name} must be a list of candidate scopes"
            )
        ambiguous[name] = tuple(entry)  # pyright: ignore[reportUnknownArgumentType]

    conventions = Conventions(
        max_length=int(title.get("max_length", 72)),
        max_scope_parts=int(title.get("max_scope_parts", 2)),
        allow_bot_prefix=bool(title.get("allow_bot_prefix", True)),
        allow_revert_wrapper=bool(title.get("allow_revert_wrapper", True)),
        types=_require_str_map(raw, "types"),
        type_aliases=_require_str_map(raw, "type_aliases"),
        legacy_types=legacy_types,
        legacy_scopes=legacy_scopes,
        scopes=_require_str_map(raw, "scopes"),
        scope_aliases=_require_str_map(raw, "scope_aliases"),
        ambiguous_scopes=ambiguous,
        ambiguous_scope_notes=_require_str_map(raw, "ambiguous_scope_notes"),
        deprecation_type=str(raw.get("deprecation", {}).get("type", "deprecation")),
        replacement_markers=_require_str_list(
            raw, "deprecation", "replacement_markers"
        ),
        terminal_markers=_require_str_list(raw, "deprecation", "terminal_markers"),
        breaking_label=str(raw.get("labels", {}).get("breaking", "breaking")),
        extra_labels=_require_str_list(raw, "labels", "extra"),
        exclude_labels=_require_str_list(raw, "labels", "exclude"),
        no_section_labels=_require_str_list(raw, "labels", "no_section"),
    )
    _validate(conventions)
    return conventions


def _validate(conventions: Conventions) -> None:
    """Fail loudly on the mistakes that would otherwise mislabel silently."""
    canonical = set(conventions.canonical_scopes)

    unknown = sorted(set(conventions.no_section_labels) - conventions.all_labels())
    if unknown:
        raise ConventionsError(
            f"[labels].no_section names labels that do not exist: {', '.join(unknown)}"
        )

    overlap = sorted(canonical & set(conventions.scope_aliases))
    if overlap:
        raise ConventionsError(
            f"scopes are both canonical and aliases: {', '.join(overlap)}"
        )

    ambiguous_conflict = sorted(
        set(conventions.ambiguous_scopes) & (canonical | set(conventions.scope_aliases))
    )
    if ambiguous_conflict:
        raise ConventionsError(
            f"scopes are both ambiguous and resolvable: {', '.join(ambiguous_conflict)}"
        )

    for alias, target in conventions.scope_aliases.items():
        if target not in conventions.scopes:
            raise ConventionsError(
                f"[scope_aliases].{alias} points at {target!r}, which is not canonical"
            )

    for name, candidates in conventions.ambiguous_scopes.items():
        unknown = sorted(set(candidates) - canonical)
        if unknown:
            raise ConventionsError(
                f"[ambiguous_scopes].{name} names non-canonical candidates: "
                f"{', '.join(unknown)}"
            )

    for name in conventions.ambiguous_scope_notes:
        if name not in conventions.ambiguous_scopes:
            raise ConventionsError(
                f"[ambiguous_scope_notes].{name} has no [ambiguous_scopes] entry"
            )

    for alias, target in conventions.type_aliases.items():
        if alias in conventions.types:
            raise ConventionsError(f"[type_aliases].{alias} is also a canonical type")
        if target and target not in conventions.types:
            raise ConventionsError(
                f"[type_aliases].{alias} suggests {target!r}, which is not a type"
            )

    for name in conventions.legacy_scopes:
        if name in conventions.scopes:
            raise ConventionsError(f"[legacy_scopes.{name}] is also a canonical scope")
        if name in conventions.scope_aliases:
            raise ConventionsError(f"[legacy_scopes.{name}] is also a scope alias")

    for name in conventions.legacy_types:
        if name in conventions.types:
            raise ConventionsError(f"[legacy_types.{name}] is also a canonical type")
