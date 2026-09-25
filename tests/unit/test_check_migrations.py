"""Exercise real Alembic revision graphs without application or DB fixtures."""

from pathlib import Path

import pytest
from alembic.script import ScriptDirectory
from alembic.script.revision import RevisionError
from alembic.util import CommandError
from check_migrations import LINEAR_HISTORY_BASE, check_migrations, main

type Parent = str | tuple[str, ...] | None


def write_revision(
    directory: Path,
    revision: str,
    parent: Parent,
    *,
    depends_on: Parent = None,
    filename: str | None = None,
) -> None:
    versions = directory / "versions"
    versions.mkdir(exist_ok=True)
    (versions / f"{filename or revision}.py").write_text(
        f"revision = {revision!r}\n"
        f"down_revision = {parent!r}\n"
        f"depends_on = {depends_on!r}\n"
        "branch_labels = None\n"
    )


@pytest.fixture
def history(tmp_path: Path) -> Path:
    write_revision(tmp_path, "root", None)
    write_revision(tmp_path, "left", "root")
    write_revision(tmp_path, "right", "root")
    write_revision(tmp_path, "baseline", ("left", "right"))
    # Loading revision metadata must never execute the migration environment.
    (tmp_path / "env.py").write_text("raise AssertionError('env.py executed')\n")
    return tmp_path


@pytest.mark.parametrize("new_revisions", [0, 1, 3])
def test_accepts_linear_extensions_of_historical_merge(
    history: Path, new_revisions: int
) -> None:
    head = "baseline"
    for index in range(new_revisions):
        revision = f"new_{index}"
        write_revision(history, revision, head)
        head = revision
    assert (
        check_migrations(ScriptDirectory(str(history)), linear_since="baseline") == head
    )


@pytest.mark.parametrize("parent", ["baseline", "root"])
def test_rejects_forks_even_from_historical_revisions(
    history: Path, parent: str
) -> None:
    write_revision(history, "first", "baseline")
    write_revision(history, "second", parent)
    with pytest.raises(ValueError, match="exactly one Alembic head"):
        check_migrations(ScriptDirectory(str(history)), linear_since="baseline")


def test_rejects_new_merge_even_with_one_head(history: Path) -> None:
    write_revision(history, "first", "baseline")
    write_revision(history, "second", "baseline")
    write_revision(history, "merged", ("first", "second"))
    scripts = ScriptDirectory(str(history))
    assert scripts.get_heads() == ["merged"]
    with pytest.raises(ValueError, match="New merge revisions are not allowed"):
        check_migrations(scripts, linear_since="baseline")


def test_rejects_new_cross_branch_dependencies(history: Path) -> None:
    write_revision(history, "new", "baseline", depends_on="left")
    with pytest.raises(ValueError, match="uses depends_on"):
        check_migrations(ScriptDirectory(str(history)), linear_since="baseline")


def test_accepts_historical_dependencies(history: Path) -> None:
    write_revision(history, "right", "root", depends_on="left")
    assert (
        check_migrations(ScriptDirectory(str(history)), linear_since="baseline")
        == "baseline"
    )


def test_rejects_multiple_roots_even_if_merged(history: Path) -> None:
    write_revision(history, "right", None)
    with pytest.raises(ValueError, match="exactly one Alembic base"):
        check_migrations(ScriptDirectory(str(history)), linear_since="baseline")


def test_rejects_empty_history(tmp_path: Path) -> None:
    (tmp_path / "versions").mkdir()
    with pytest.raises(ValueError, match="found 0"):
        check_migrations(ScriptDirectory(str(tmp_path)), linear_since="baseline")


def test_rejects_missing_policy_boundary(history: Path) -> None:
    with pytest.raises(CommandError, match="missing"):
        check_migrations(ScriptDirectory(str(history)), linear_since="missing")


def test_rejects_missing_parent(history: Path) -> None:
    write_revision(history, "new", "missing")
    with pytest.raises(UserWarning, match="not present"):
        check_migrations(ScriptDirectory(str(history)), linear_since="baseline")


def test_rejects_duplicate_revision_ids(history: Path) -> None:
    write_revision(history, "baseline", ("left", "right"), filename="duplicate")
    with pytest.raises(UserWarning, match="present more than once"):
        check_migrations(ScriptDirectory(str(history)), linear_since="baseline")


def test_rejects_cycles(history: Path) -> None:
    write_revision(history, "first", "second")
    write_revision(history, "second", "first")
    with pytest.raises(RevisionError, match="Cycle"):
        check_migrations(ScriptDirectory(str(history)), linear_since="baseline")


def test_cli_fails_for_invalid_history(
    history: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (history / "alembic.ini").write_text("[alembic]\nscript_location = .\n")
    write_revision(history, "first", "baseline")
    write_revision(history, "second", "baseline")
    monkeypatch.chdir(history)
    assert main() == 1
    assert "Expected exactly one Alembic head" in capsys.readouterr().err


def test_cli_accepts_valid_history(
    history: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    (history / "alembic.ini").write_text("[alembic]\nscript_location = .\n")
    write_revision(history, LINEAR_HISTORY_BASE, "baseline")
    monkeypatch.chdir(history)
    assert main() == 0
    assert "Alembic migration history OK" in capsys.readouterr().out
