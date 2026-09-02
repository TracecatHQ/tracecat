"""Accept/reject cases for the PR-title checker.

Every reject case asserts a violation `code`, never a message: the codes are the
stable contract that CI and hooks branch on, and the messages are free to be
reworded.
"""

from __future__ import annotations

import pytest
from check_pr_title import EXIT_CONFIG, EXIT_INVALID, EXIT_OK, check_title, main
from commit_conventions import Conventions, load_conventions

CONVENTIONS: Conventions = load_conventions()

ACCEPT = [
    "feat(ui): replace preset button with @ mentions",
    "fix: return 404 for missing workspaces",
    "feat(api)!: drop the v1 webhook payload",
    "feat(ui+api): case comment replies",
    "[codex] chore(deps): bump orjson",
    'Revert "feat(mcp): add internal OIDC issuer"',
    "release: 1.0.0-beta.49",
    "deprecation(integrations): tools.x.list_signals in favour of tools.x.search_alerts",
    "build(deps): patch dependabot alerts",
    "feat(actions): add a table lookup action",
    "fix(functions): regex_extract corner case",
    "feat(cases+actions): add a case linking action",
    "feat(audit): stream audit logs via webhook",
]

REJECT: list[tuple[str, str]] = [
    ("feat(cases) ENG-1597: add team scoped agent session reads", "missing-prefix"),
    ("style: reformat", "unknown-type"),
    ("tests: init ux smoke testing", "unknown-type"),
    ("feat(integration): add Okta event hooks", "unknown-scope"),
    ("feat(jira): add issue search", "unknown-scope"),
    ("refactor(app): update case filtering", "ambiguous-scope"),
    ("deps: bump orjson", "unknown-type"),
    ("deprecation(registry): remove old thing", "depr-no-replacement"),
    ("feat(ui+api+ee): x", "too-many-scopes"),
    ("feat(ui, api): x", "scope-format"),
    ("feat(UI): x", "scope-format"),
    ("feat(ui+ui): x", "duplicate-scope"),
    ("feat:no space", "missing-space"),
    ("feat(ui): x.", "trailing-period"),
    ("feat(): x", "empty-scope"),
    ("feat(ui):", "empty-description"),
    ("feat(core): add a table lookup action", "unknown-scope"),
    ("feat(udfs): add a table lookup action", "unknown-scope"),
    ("feat(udf): add a table lookup action", "unknown-scope"),
    ("feat(core-actions): add a table lookup action", "unknown-scope"),
    ("  feat(ui): x", "leading-whitespace"),
]


@pytest.mark.parametrize("title", ACCEPT)
def test_accepts_valid_titles(title: str) -> None:
    report = check_title(title, CONVENTIONS)
    assert report.ok, report.codes


@pytest.mark.parametrize(
    ("title", "code"), REJECT, ids=[f"{c}-{t.split(':')[0]}" for t, c in REJECT]
)
def test_rejects_invalid_titles(title: str, code: str) -> None:
    assert code in check_title(title, CONVENTIONS).codes


@pytest.mark.parametrize(
    ("title", "hint"),
    [
        ("feat(integration): add Okta event hooks", "integrations"),
        ("feat(jira): add issue search", "integrations"),
        ("deps: bump orjson", "build(deps)"),
        ("feat(core): add a table lookup action", "actions"),
        ("feat(udfs): add a table lookup action", "actions"),
        ("feat(udf): add a table lookup action", "actions"),
        ("feat(core-actions): add a table lookup action", "actions"),
    ],
)
def test_hint_names_the_replacement(title: str, hint: str) -> None:
    messages = " ".join(v.message for v in check_title(title, CONVENTIONS).violations)
    assert hint in messages


def test_retired_app_scope_explains_itself() -> None:
    messages = " ".join(
        v.message for v in check_title("refactor(app): x", CONVENTIONS).violations
    )
    assert "retired" in messages
    assert "backend" in messages
    for candidate in ("api", "engine", "cases"):
        assert f"`{candidate}`" in messages


def test_all_violations_are_reported_not_just_the_first() -> None:
    report = check_title("style(app): x.", CONVENTIONS)
    assert set(report.codes) == {"unknown-type", "ambiguous-scope", "trailing-period"}


def test_compound_scope_resolves_to_both_area_labels() -> None:
    """`feat(cases+actions)` is the worked example in CONTRIBUTING.md."""
    title = "feat(cases+actions): add a case linking action"
    assert check_title(title, CONVENTIONS).ok
    assert CONVENTIONS.scope_label("cases", type_="feat") == "cases"
    assert CONVENTIONS.scope_label("actions", type_="feat") == "actions"
    assert CONVENTIONS.scope_label("functions", type_="fix") == "functions"


def test_audit_is_canonical_not_an_alias_for_api() -> None:
    """`audit` was an alias to `api` until 2026-09-01, so this is the regression.

    Nine merged pull requests about security audit logs were filed under `api`
    because of it. The checker used to reject the title below outright.
    """
    result = check_title("fix(audit): preserve client attribution", CONVENTIONS)
    assert result.ok, [v.message for v in result.violations]
    assert "audit" not in CONVENTIONS.scope_aliases
    assert CONVENTIONS.scope_label("audit", type_="fix") == "audit"
    # The distinction the scope exists to draw.
    assert CONVENTIONS.scope_label("logging", type_="fix") == "logging"


def _suggestion_cases() -> list[tuple[str, str]]:
    """(source, title) for every replacement the checker tells an author to write."""
    cases: list[tuple[str, str]] = []

    def described(suggest: str) -> str:
        # A `deprecation:` title must name a replacement, so a generic
        # description would fail for reasons that have nothing to do with the
        # suggestion being valid.
        if suggest.split("(")[0] == CONVENTIONS.deprecation_type:
            return f"{suggest}: tools.x in favour of tools.y"
        return f"{suggest}: example change"

    for name, suggest in CONVENTIONS.type_aliases.items():
        if suggest:
            cases.append((f"type_aliases.{name}", described(suggest)))
    for name, legacy in CONVENTIONS.legacy_types.items():
        if legacy.suggest:
            cases.append((f"legacy_types.{name}", described(legacy.suggest)))
    for name, legacy in CONVENTIONS.legacy_scopes.items():
        if legacy.suggest:
            cases.append((f"legacy_scopes.{name}", f"feat({legacy.suggest}): example"))
    return cases


SUGGESTION_CASES: list[tuple[str, str]] = _suggestion_cases()


@pytest.mark.parametrize(
    ("source", "title"),
    SUGGESTION_CASES,
    ids=[source for source, _ in SUGGESTION_CASES],
)
def test_every_suggestion_is_itself_valid(source: str, title: str) -> None:
    """An error message that names an invalid replacement sends the author round twice.

    `legacy_types.helm` suggested `infra(helm)`, and `helm` is an alias for
    `infra`, so following the message produced a second, different error.
    """
    report = check_title(title, CONVENTIONS)
    assert report.ok, f"{source} suggests {title!r}, which fails: {report.codes}"


def test_over_length_warns_but_does_not_fail() -> None:
    title = f"feat(ui): {'x' * CONVENTIONS.max_length}"
    report = check_title(title, CONVENTIONS)
    assert report.ok
    assert [w.code for w in report.warnings] == ["too-long"]


def test_revert_wrapper_skips_the_length_warning() -> None:
    inner = "feat(integrations): " + "x" * CONVENTIONS.max_length
    report = check_title(f'Revert "{inner}"', CONVENTIONS)
    assert report.ok
    assert not report.warnings


@pytest.mark.parametrize(
    "description",
    [
        "Okta event hooks now retry",
        "GitHub Actions pinning",
        "add a Jira lookup",
    ],
)
def test_description_capitalisation_is_not_enforced(description: str) -> None:
    """Many descriptions start with a proper noun; capitalisation is not a signal."""
    assert check_title(f"feat(integrations): {description}", CONVENTIONS).ok


@pytest.mark.parametrize(
    "description",
    [
        "old thing in favour of new thing",
        "old thing in favor of new thing",
        "old thing replaced by new thing",
        "old thing superseded by new thing",
        "drop old thing, use new thing",
        "old thing with no replacement",
    ],
)
def test_deprecation_replacement_markers(description: str) -> None:
    assert check_title(f"deprecation(integrations): {description}", CONVENTIONS).ok


@pytest.mark.parametrize(
    "title",
    [
        "deprecation(api): reuse old endpoint",
        "deprecation(api): misuse the old field",
    ],
)
def test_deprecation_replacement_markers_require_word_boundaries(title: str) -> None:
    assert "depr-no-replacement" in check_title(title, CONVENTIONS).codes


@pytest.mark.parametrize(
    "title",
    [
        "deprecation(api): use tools.y instead",
        "deprecation(api): x in favour of y",
        "deprecation(api): drop x with no replacement",
    ],
)
def test_deprecation_replacement_markers_match_complete_words(title: str) -> None:
    assert check_title(title, CONVENTIONS).ok


@pytest.mark.parametrize(
    "title",
    [
        "deprecation(api): use",
        "deprecation(api): old endpoint replaced by",
        "deprecation(api): x superseded by",
    ],
)
def test_deprecation_marker_must_be_followed_by_something(title: str) -> None:
    """A marker naming nothing is the failure mode the rule exists to catch."""
    assert "depr-no-replacement" in check_title(title, CONVENTIONS).codes


@pytest.mark.parametrize(
    "title",
    [
        "deprecation(api): x replaced by ?",
        "deprecation(api): use !",
        "deprecation(api): use ,",
        "deprecation(api): use ;",
    ],
)
def test_deprecation_marker_must_be_followed_by_a_word(title: str) -> None:
    """A bare punctuation mark points at nothing, and `\\S` accepted one.

    `deprecation(api): use .` is not a case here: it fails on `trailing-period`
    whatever this rule does, so it would pass the test with the rule removed.
    """
    assert "depr-no-replacement" in check_title(title, CONVENTIONS).codes


def test_terminal_marker_needs_nothing_after_it() -> None:
    """`with no replacement` is the whole statement, so it is exempt."""
    assert check_title(
        "deprecation(api): drop the v1 payload with no replacement", CONVENTIONS
    ).ok


def test_terminal_marker_only_exempts_the_end_of_the_description() -> None:
    """`with no replacement yet` is a promise, not a statement of fact."""
    report = check_title(
        "deprecation(api): drop the v1 payload with no replacement yet", CONVENTIONS
    )
    assert "depr-no-replacement" in report.codes


def test_leading_whitespace_fails_but_trailing_whitespace_does_not() -> None:
    """The autolabeler anchors at `^` only, so the two ends are not symmetric.

    GitHub stores a title verbatim -- #2856 is stored as
    `"fix(agents): custom model name resolution "` -- so a leading space costs
    the pull request every label and its release-notes heading, while a
    trailing one is trimmed by the `^(- .*?)[ \\t]+$` replacer before a reader
    sees it.
    """
    assert "leading-whitespace" in check_title("  feat(ui): x", CONVENTIONS).codes
    assert check_title("feat(ui): x ", CONVENTIONS).ok


def test_main_reads_the_title_from_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PR_TITLE", "feat(ui): a valid title")
    assert main([]) == EXIT_OK
    monkeypatch.setenv("PR_TITLE", "nonsense")
    assert main([]) == EXIT_INVALID


def test_main_prefers_the_file_over_the_environment(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PR_TITLE", "feat(ui): a valid title")
    message = tmp_path / "COMMIT_EDITMSG"
    message.write_text("# a comment\nstyle: reformat\n", encoding="utf-8")
    assert main([str(message)]) == EXIT_INVALID


def test_main_prefers_the_flag_over_the_environment(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("PR_TITLE", "style: reformat")
    assert main(["--title", "feat(ui): a valid title"]) == EXIT_OK


def test_main_without_a_title_is_a_usage_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("PR_TITLE", raising=False)
    assert main([]) == EXIT_CONFIG


def test_main_with_a_broken_config_is_a_config_error(tmp_path) -> None:
    broken = tmp_path / "commit-conventions.toml"
    broken.write_text('[scope_aliases]\nfoo = "nope"\n', encoding="utf-8")
    assert main(["--config", str(broken), "--title", "feat: x"]) == EXIT_CONFIG


def test_list_prints_the_taxonomy(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["--list"]) == EXIT_OK
    out = capsys.readouterr().out
    for scope in CONVENTIONS.canonical_scopes:
        assert scope in out


def test_step_summary_is_written_when_set(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    summary = tmp_path / "summary.md"
    monkeypatch.setenv("GITHUB_STEP_SUMMARY", str(summary))
    assert main(["--title", "style: reformat"]) == EXIT_INVALID
    assert "unknown-type" in summary.read_text(encoding="utf-8")
