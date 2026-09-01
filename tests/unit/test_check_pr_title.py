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
