"""Drift guard for the commit-convention system.

`.github/commit-conventions.toml` is the source of truth. Three things restate
parts of it and can silently fall out of step:

- the Release Drafter autolabeler regexes in `.github/release-drafter.yml`,
- the release-notes categories in the same file,
- the marker-delimited tables in `CONTRIBUTING.md` and `AGENTS.md`.

Each failure here is invisible in production: a stale regex just stops applying
a label, and the pull requests it would have held render with no heading.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Final

import pytest
import yaml
from audit_commit_conventions import (
    DEFAULT_LIMIT,
    build_parser,
    parse_title,
    resolve_labels,
)
from commit_conventions import Conventions, LegacyType, load_conventions

REPO_ROOT: Final = Path(__file__).resolve().parents[2]
DRAFTER_PATH: Final = REPO_ROOT / ".github" / "release-drafter.yml"
AUDIT_SCRIPT_PATH: Final = REPO_ROOT / "scripts" / "audit_commit_conventions.py"
DOC_PATHS: Final = (REPO_ROOT / "CONTRIBUTING.md", REPO_ROOT / "AGENTS.md")

# Release Drafter accepts either a bare string or a JavaScript-style
# `/pattern/flags` literal.
JS_REGEX: Final = re.compile(r"^/(?P<pattern>.*)/(?P<flags>[a-z]*)$", re.DOTALL)
BACKTICKED: Final = re.compile(r"`([^`]+)`")

CONVENTIONS: Final = load_conventions()
DRAFTER: Final = yaml.safe_load(DRAFTER_PATH.read_text(encoding="utf-8"))


def _compile(raw: str) -> re.Pattern[str]:
    match = JS_REGEX.match(raw)
    if match is None:
        return re.compile(re.escape(raw))
    flags = re.IGNORECASE if "i" in match.group("flags") else 0
    return re.compile(match.group("pattern"), flags)


AUTOLABEL_RULES: Final = tuple(
    (str(rule["label"]), _compile(pattern))
    for rule in DRAFTER["autolabeler"]
    for pattern in rule["title"]
)
TIER_A_TYPE_RULE: Final = re.compile(
    re.escape(r"^(\[[\w.-]+\]\s+)?(?:")
    + r"(?P<alternatives>[a-z|]+)"
    + re.escape(r")(\([^)]*\))?!?: ")
)
TIER_A_TYPE_ALTERNATIVES: Final = tuple(
    sorted(
        alternative
        for _, pattern in AUTOLABEL_RULES
        if (match := TIER_A_TYPE_RULE.fullmatch(pattern.pattern)) is not None
        for alternative in match.group("alternatives").split("|")
    )
)


def autolabel(title: str) -> frozenset[str]:
    """Every label the autolabeler would apply to `title`."""
    return frozenset(
        label for label, pattern in AUTOLABEL_RULES if pattern.search(title)
    )


def category_labels() -> frozenset[str]:
    return frozenset(
        label for category in DRAFTER["categories"] for label in category["labels"]
    )


def marker_identifiers(path: Path, name: str) -> frozenset[str]:
    """Backticked identifiers inside a `commit-conventions:<name>` block."""
    text = path.read_text(encoding="utf-8")
    match = re.search(
        rf"<!-- BEGIN commit-conventions:{name} -->(?P<body>.*?)"
        rf"<!-- END commit-conventions:{name} -->",
        text,
        re.DOTALL,
    )
    assert match is not None, f"{path.name} is missing the {name} marker block"
    return frozenset(BACKTICKED.findall(match.group("body")))


@pytest.fixture(scope="module")
def conventions() -> Conventions:
    return CONVENTIONS


@pytest.mark.parametrize(("type_", "label"), sorted(CONVENTIONS.types.items()))
def test_type_produces_its_label(type_: str, label: str) -> None:
    assert label in autolabel(f"{type_}: example change")


@pytest.mark.parametrize(("type_", "legacy"), sorted(CONVENTIONS.legacy_types.items()))
def test_legacy_type_still_labels(type_: str, legacy: LegacyType) -> None:
    """History keeps its section even though the checker rejects these."""
    assert legacy.label in autolabel(f"{type_}: example change")


@pytest.mark.parametrize("scope", sorted(CONVENTIONS.scopes))
def test_canonical_scope_produces_its_area_label(
    scope: str, conventions: Conventions
) -> None:
    label = conventions.scopes[scope]
    assert label in autolabel(f"feat({scope}): example change")


@pytest.mark.parametrize("alias", sorted(CONVENTIONS.scope_aliases))
def test_scope_alias_produces_the_canonical_area_label(
    alias: str, conventions: Conventions
) -> None:
    label = conventions.scopes[conventions.scope_aliases[alias]]
    assert label in autolabel(f"feat({alias}): example change")


@pytest.mark.parametrize(
    ("title", "expected"),
    [
        ("ci(workflows): pin actions to immutable SHAs", "cicd"),
        ("feat(workflows): add a workflow alias resolver", "engine"),
        ("fix(workflows): resolve alias collisions", "engine"),
    ],
)
def test_workflows_is_disambiguated_by_type(title: str, expected: str) -> None:
    assert expected in autolabel(title)


def test_ci_workflows_is_not_labelled_as_engine() -> None:
    assert "engine" not in autolabel("ci(workflows): pin actions")


@pytest.mark.parametrize("scope", sorted(set(CONVENTIONS.canonical_scopes) - {"ui"}))
def test_compound_scopes_label_like_their_components(
    scope: str, conventions: Conventions
) -> None:
    expected = conventions.scope_label(scope, type_="feat")
    assert expected is not None
    for compound in (f"{scope}+ui", f"ui+{scope}"):
        labels = autolabel(f"feat({compound}): example change")
        assert expected in labels, compound
        assert conventions.scopes["ui"] in labels, compound


def test_bang_yields_breaking(conventions: Conventions) -> None:
    labels = autolabel("feat(api)!: drop the v1 webhook payload")
    assert conventions.breaking_label in labels
    assert conventions.scopes["api"] in labels


def test_revert_wrapper_is_labelled() -> None:
    labels = autolabel('Revert "feat(mcp): add internal OIDC issuer"')
    assert labels, "GitHub revert titles must not render headerless"


@pytest.mark.parametrize(
    "title",
    [
        "feat(ui): replace preset button with @ mentions",
        "chore(deps): bump orjson",
        "fix(api)!: return 404 for missing workspaces",
        "ci(workflows): pin actions to immutable SHAs",
    ],
)
def test_bot_prefix_does_not_change_labels(title: str) -> None:
    for prefix in ("[codex]", "[pre-commit.ci]", "[foo-bar]"):
        assert autolabel(f"{prefix} {title}") == autolabel(title)


@pytest.mark.parametrize("vendor", ["jira", "splunk", "scc", "elastic_security"])
def test_vendor_scopes_are_absorbed_into_integrations(
    vendor: str, conventions: Conventions
) -> None:
    assert conventions.scopes["integrations"] in autolabel(
        f"feat({vendor}): add an action"
    )


@pytest.mark.parametrize("scope", sorted(CONVENTIONS.ambiguous_scopes))
def test_ambiguous_scopes_get_no_area_label(
    scope: str, conventions: Conventions
) -> None:
    """Guessing an area is worse than leaving the PR on its type label."""
    area_labels = set(conventions.scopes.values())
    labels = autolabel(f"refactor({scope}): example change")
    assert labels == {conventions.types["refactor"]}, labels & area_labels


@pytest.mark.parametrize("label", sorted(category_labels()))
def test_every_category_label_is_in_the_vocabulary(
    label: str, conventions: Conventions
) -> None:
    assert label in conventions.all_labels()


@pytest.mark.parametrize("label", sorted({label for label, _ in AUTOLABEL_RULES}))
def test_every_autolabeler_label_is_in_the_vocabulary(
    label: str, conventions: Conventions
) -> None:
    assert label in conventions.all_labels()


@pytest.mark.parametrize("type_", TIER_A_TYPE_ALTERNATIVES)
def test_every_tier_a_type_is_declared(type_: str, conventions: Conventions) -> None:
    """A drafter-only type leaves the backfill unable to label merged PRs."""
    assert type_ in conventions.types or type_ in conventions.legacy_types


def test_exclude_labels_match_the_toml(conventions: Conventions) -> None:
    assert set(DRAFTER["exclude-labels"]) == set(conventions.exclude_labels)


def test_every_vocabulary_label_has_a_home(conventions: Conventions) -> None:
    """A label with no category renders its PRs with no heading."""
    homeless = (
        conventions.all_labels() - category_labels() - set(conventions.exclude_labels)
    )
    assert not homeless, sorted(homeless)


def test_audit_script_has_no_top_level_yaml_import() -> None:
    """The backfill job would break if yaml were hoisted to module scope."""
    module = ast.parse(AUDIT_SCRIPT_PATH.read_text(encoding="utf-8"))
    imports_yaml = any(
        (
            isinstance(node, ast.Import)
            and any(alias.name == "yaml" for alias in node.names)
        )
        or (isinstance(node, ast.ImportFrom) and node.module == "yaml")
        for node in module.body
    )
    assert not imports_yaml


# `scripts/audit_commit_conventions.py` resolves labels in Python instead of by
# regex, so it is a third restatement of the same rules and can drift too.
CORPUS: Final = (
    "feat(ui): replace preset button with @ mentions",
    "fix: return 404 for missing workspaces",
    "feat(api)!: drop the v1 webhook payload",
    "feat(ui+api): case comment replies",
    "feat(splunk+ui): add saved search export",
    "[codex] chore(deps): bump orjson",
    'Revert "feat(mcp): add internal OIDC issuer"',
    "release: 1.0.0-beta.49",
    "deprecation(integrations): x in favour of y",
    "build(deps): patch dependabot alerts",
    "ci(workflows): pin actions to immutable SHAs",
    "feat(workflows): add a workflow alias resolver",
    "feat(jira): add issue search",
    "feat(elastic_security): add a detection search",
    "feat(actions): add a table lookup action",
    "fix(functions): regex_extract corner case",
    "feat(cases+actions): add a case linking action",
    "feat(udfs): add a table lookup action",
    "refactor(app): update case filtering",
    "perf(executor): batch event writes",
    "test(engine): cover retry backoff",
    "security(deps): patch an unauthenticated RCE",
    "helm: bump the chart version",
    "deps: bump temporalio",
    "tests: agent smoke",
    "doc: fix a broken anchor",
    "depr(integrations): tools.x in favour of tools.y",
)


@pytest.mark.parametrize("title", CORPUS)
def test_audit_script_agrees_with_the_autolabeler(title: str) -> None:
    parsed = parse_title(title, CONVENTIONS)
    resolved = frozenset() if parsed is None else resolve_labels(parsed, CONVENTIONS)
    assert resolved == autolabel(title), title


@pytest.mark.parametrize(
    "argv",
    [
        ["--limit", "7", "prefixes"],
        ["prefixes", "--limit", "7"],
    ],
    ids=["global-first", "subcommand-first"],
)
def test_limit_survives_either_argument_order(argv: list[str]) -> None:
    """A subparser default silently overwrote the top-level value.

    `--limit 7 prefixes` reported 500 rows with no error, which is worse than
    a parse failure because the number looks legitimate.
    """
    assert build_parser().parse_args(argv).limit == 7


def test_limit_falls_back_to_the_default() -> None:
    assert build_parser().parse_args(["prefixes"]).limit == DEFAULT_LIMIT


@pytest.mark.parametrize("path", DOC_PATHS, ids=lambda p: p.name)
def test_documented_types_match_the_toml(path: Path, conventions: Conventions) -> None:
    assert marker_identifiers(path, "types") == set(conventions.types)


@pytest.mark.parametrize("path", DOC_PATHS, ids=lambda p: p.name)
def test_documented_scopes_match_the_toml(path: Path, conventions: Conventions) -> None:
    assert marker_identifiers(path, "scopes") == set(conventions.canonical_scopes)
